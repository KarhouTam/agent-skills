#!/usr/bin/env python3
"""Cloud-back the teaching notes: `main` for archived topics, one branch per draft.

The notes root stays a plain readable tree (`INDEX.md`, `.drafts/`, topic
directories). A disposable git clone under `<notes>/.sync/` mirrors the private
cloud repo and does the branch/merge work; it is internal state, never study
material.

Cloud layout:
    main                        archived topics + each project's _project.md
    script/<project>/<topic>    one unconfirmed draft: note + materials

Local layout:
    .drafts/<project>/<topic>/note.md   draft note
    <project>/<topic>/...               materials (and, once archived, note.md)

Commands (one notes root per machine; `init` once):
    sync.py init <remote-url> [notes]
    sync.py pull [notes]
    sync.py push [notes]
    sync.py draft <project>/<topic> [notes]
    sync.py archive <project>/<topic> [notes]

`pull` fetches every branch: main is applied to the archived tree, each
`script/*` branch is materialized into `.drafts/` plus materials. `draft`
writes the draft branch after a lesson edit. `archive` is run after `note.py
promote`: it updates the branch, merges it into main with --no-ff, pushes, then
deletes the branch. `push` syncs main-side content (`_project.md`, corrections
to archived notes).

Everything is a three-way comparison against the last synced commit recorded in
`.sync/state.json`. A conflict keeps the local copy, prints the file, and exits
1; nothing is force-pushed or silently overwritten. Exit codes: 0 = synced or
local-only, 1 = conflicts / network failure (local safe), 2 = usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import note  # noqa: E402

REMOTE = "origin"
MAIN = "main"
BRANCH_PREFIX = "script/"
STATE_FILE = "state.json"
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
GIT_TIMEOUT = int(os.environ.get("TEACHING_SYNC_TIMEOUT", "30"))


class SyncError(RuntimeError):
    pass


# --------------------------------------------------------------------------- paths


def resolve_notes_root(explicit: str | None) -> str:
    """Same precedence as SKILL.md Step 0."""
    if explicit:
        return os.path.abspath(explicit)
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.environ.get("TEACHING_NOTES_DIR") or "",
        os.path.join(base, "memory"),
        os.path.join(
            os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
            "teaching-notes",
        ),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            os.makedirs(candidate, exist_ok=True)
        except OSError:
            continue
        if os.access(candidate, os.W_OK):
            return os.path.abspath(candidate)
    raise SystemExit("error: no writable notes root (set TEACHING_NOTES_DIR)")


def repo_path(root: str) -> str:
    return os.path.join(root, note.SYNC_DIR)


def state_path(root: str) -> str:
    return os.path.join(repo_path(root), STATE_FILE)


def is_configured(root: str) -> bool:
    return os.path.isdir(os.path.join(repo_path(root), ".git"))


def display_remote(url: str | None) -> str:
    return re.sub(r"(://)[^/@]+@", r"\1", url or "")


def local_note_count(root: str) -> tuple[int, int]:
    return len(note.load_notes(root)), len(note.find_drafts(root))


# ----------------------------------------------------------------------------- git


def run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    try:
        # A black-holed HTTPS connection must not stall the lesson; the skill
        # continues from the local cache when this fails.
        return subprocess.run(
            args, cwd=cwd, capture_output=True, env=GIT_ENV, timeout=GIT_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise SyncError(
            f"git {args[1] if len(args) > 1 else ''} timed out after {GIT_TIMEOUT}s"
        ) from exc


def git(repo: str, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = run(["git", "-C", repo, *args])
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise SyncError(detail or f"git {' '.join(args)} failed")
    return proc


def git_text(repo: str, *args: str, check: bool = True) -> str:
    return git(repo, *args, check=check).stdout.decode("utf-8", "replace").strip()


def has_ref(repo: str, ref: str) -> bool:
    return git(repo, "rev-parse", "--verify", "--quiet", ref, check=False).returncode == 0


def sha_of(repo: str, ref: str) -> str | None:
    if not has_ref(repo, ref):
        return None
    return git_text(repo, "rev-parse", ref)


def commit_files(repo: str, rev: str | None) -> dict[str, bytes]:
    """Every regular blob at `rev`, as path -> bytes."""
    if not rev or not has_ref(repo, rev):
        return {}
    listing = git(repo, "ls-tree", "-r", "-z", "--name-only", rev).stdout
    files: dict[str, bytes] = {}
    for raw in listing.split(b"\0"):
        if not raw:
            continue
        path = raw.decode("utf-8")
        proc = git(repo, "cat-file", "-p", f"{rev}:{path}", check=False)
        if proc.returncode == 0:
            files[path] = proc.stdout
    return files


def topic_files(files: dict[str, bytes], nid: str) -> dict[str, bytes]:
    prefix = nid + "/"
    return {path: data for path, data in files.items() if path.startswith(prefix)}


def main_view(files: dict[str, bytes]) -> dict[str, bytes]:
    """Keep only _project.md and complete topic directories."""
    topics = {
        path[: -len("/" + note.NOTE_FILE)]
        for path in files
        if path.endswith("/" + note.NOTE_FILE) and path.count("/") == 2
    }
    out: dict[str, bytes] = {}
    for path, data in files.items():
        if path.endswith("/" + note.PROJECT_FILE) and path.count("/") == 1:
            out[path] = data
        elif any(path == topic or path.startswith(topic + "/") for topic in topics):
            out[path] = data
    return out


# ----------------------------------------------------------------------- state


def load_state(root: str) -> dict:
    path = state_path(root)
    if not os.path.exists(path):
        return {"remote": None, "branch": MAIN, "main": None, "drafts": {}}
    try:
        with open(path, encoding="utf-8") as fh:
            state = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise SyncError(f"{path} is unreadable ({exc}); re-run `sync.py init <url>`") from exc
    state.setdefault("branch", MAIN)
    state.setdefault("drafts", {})
    state.setdefault("main", None)
    return state


def save_state(root: str, state: dict) -> None:
    os.makedirs(repo_path(root), exist_ok=True)
    with open(state_path(root), "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2, sort_keys=True)
        fh.write("\n")


def branch_ref(state: dict) -> str:
    return f"refs/remotes/{REMOTE}/{state.get('branch') or MAIN}"


def draft_branch(nid: str) -> str:
    return BRANCH_PREFIX + nid


# -------------------------------------------------------------------- local view


def read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def write_file(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def local_main_files(root: str) -> dict[str, bytes]:
    """Archived topic directories plus every project's _project.md."""
    files: dict[str, bytes] = {}
    for nid in note.load_notes(root):
        topic_dir = os.path.dirname(note.note_path(root, nid))
        for dirpath, dirnames, filenames in os.walk(topic_dir):
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                files[rel] = read_file(path)
    for name in sorted(os.listdir(root)):
        path = os.path.join(root, name)
        if not os.path.isdir(path) or name.startswith("."):
            continue
        project_file = os.path.join(path, note.PROJECT_FILE)
        if os.path.isfile(project_file):
            rel = f"{name}/{note.PROJECT_FILE}"
            files[rel] = read_file(project_file)
    return files


def draft_branch_path(root: str, nid: str) -> str:
    """Where a draft note lives locally (`<nid>/note.md` is the branch path)."""
    return note.draft_path(root, nid)


def local_draft_files(root: str, nid: str) -> dict[str, bytes]:
    """Draft note + materials, keyed by their cloud (final-layout) path."""
    draft = note.draft_path(root, nid)
    archived = note.note_path(root, nid)
    source = draft if os.path.exists(draft) else archived if os.path.exists(archived) else None
    if source is None:
        return {}
    files: dict[str, bytes] = {f"{nid}/{note.NOTE_FILE}": read_file(source)}
    topic_dir = os.path.dirname(archived)
    if os.path.isdir(topic_dir):
        for dirpath, dirnames, filenames in os.walk(topic_dir):
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            for name in filenames:
                if name == note.NOTE_FILE or name.startswith("."):
                    continue
                path = os.path.join(dirpath, name)
                rel = os.path.relpath(path, root).replace(os.sep, "/")
                files[rel] = read_file(path)
    return files


def write_local(root: str, path: str, data: bytes | None) -> None:
    target = os.path.join(root, *path.split("/"))
    if data is None:
        if os.path.exists(target):
            os.remove(target)
            _prune(target, root)
        return
    write_file(target, data)


def write_draft_local(root: str, nid: str, branch_path: str, data: bytes | None) -> None:
    if branch_path == f"{nid}/{note.NOTE_FILE}":
        target = draft_branch_path(root, nid)
        if data is None:
            if os.path.exists(target):
                os.remove(target)
                _prune(os.path.dirname(target), os.path.join(root, note.DRAFT_DIR))
            return
        write_file(target, data)
        return
    write_local(root, branch_path, data)


def _prune(path: str, stop: str) -> None:
    stop = os.path.abspath(stop)
    path = os.path.abspath(path)
    while path.startswith(stop + os.sep):
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


# --------------------------------------------------------------------- three-way


def three_way(
    local: dict[str, bytes], base: dict[str, bytes], remote: dict[str, bytes]
) -> dict[str, tuple[str, bytes | None]]:
    """Path -> (\"local\" | \"remote\" | \"conflict\", value)."""
    decisions: dict[str, tuple[str, bytes | None]] = {}
    for path in sorted(set(local) | set(base) | set(remote)):
        mine, old, theirs = local.get(path), base.get(path), remote.get(path)
        if mine == theirs:
            continue
        if mine == old:
            decisions[path] = ("remote", theirs)
        elif theirs == old:
            decisions[path] = ("local", mine)
        else:
            decisions[path] = ("conflict", None)
    return decisions


def report_conflicts(label: str, paths: list[str]) -> None:
    for path in paths:
        print(f"sync: CONFLICT {label}: {path} (local kept; resolve manually)", file=sys.stderr)


def force_decisions(
    decisions: dict[str, tuple[str, bytes | None]],
    local: dict[str, bytes],
    remote: dict[str, bytes],
    take: str,
) -> None:
    """Resolve every conflict the user explicitly asked to settle in one direction."""
    for path, (kind, _) in decisions.items():
        if kind == "conflict":
            decisions[path] = (take, local.get(path) if take == "local" else remote.get(path))


# ------------------------------------------------------------------- operations


def ignore_local_state(repo: str) -> None:
    """state.json is machine-local; keep it out of every commit in the mirror."""
    exclude = os.path.join(repo, ".git", "info", "exclude")
    os.makedirs(os.path.dirname(exclude), exist_ok=True)
    try:
        with open(exclude, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        text = ""
    if STATE_FILE not in text.split():
        with open(exclude, "a", encoding="utf-8") as fh:
            if text and not text.endswith("\n"):
                fh.write("\n")
            fh.write(f"{STATE_FILE}\n")


def ensure_repo(root: str) -> str:
    if not is_configured(root):
        raise SyncError("not initialized — run `sync.py init <private-repo-url>` once")
    ignore_local_state(repo_path(root))
    return repo_path(root)


def fetch(root: str) -> None:
    repo = ensure_repo(root)
    proc = git(repo, "fetch", "--prune", REMOTE, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise SyncError(f"cannot reach {display_remote(load_state(root).get('remote'))}: {detail}")


def checkout_clean(repo: str, rev: str) -> None:
    git(repo, "checkout", "-f", "--detach", rev)
    git(repo, "clean", "-fdx", "-e", STATE_FILE, check=False)


def commit(repo: str, message: str) -> str:
    git(repo, "add", "-A")
    if git(repo, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return git_text(repo, "rev-parse", "HEAD")
    proc = git(repo, "commit", "-m", message, check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise SyncError(
            detail + "\n(check `git config user.name` / `git config user.email`)"
        )
    return git_text(repo, "rev-parse", "HEAD")


def push_ref(repo: str, ref: str, branch: str) -> str:
    proc = git(repo, "push", REMOTE, f"HEAD:{ref}", check=False)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise SyncError(f"push to {branch} failed: {detail}")
    return git_text(repo, "rev-parse", "HEAD")


def delete_remote_branch(repo: str, branch: str) -> bool:
    proc = git(repo, "push", REMOTE, "--delete", branch, check=False)
    if proc.returncode != 0:
        print(
            f"sync: WARNING could not delete cloud branch {branch}: "
            + (proc.stderr or proc.stdout).decode("utf-8", "replace").strip(),
            file=sys.stderr,
        )
        return False
    return True


def push_main(
    root: str,
    state: dict,
    exclude_topic: str | None = None,
    force: bool = False,
) -> tuple[bool, str | None]:
    repo = ensure_repo(root)
    fetch(root)
    remote = sha_of(repo, branch_ref(state))
    if remote is None:
        raise SyncError("cloud main has no commits yet — re-run `sync.py init <url>`")
    base = main_view(commit_files(repo, state.get("main"))) if state.get("main") else {}
    local = local_main_files(root)
    # A promoted-but-not-yet-archived topic still has a draft branch: leave it for
    # `archive` to merge instead of writing it straight to main.
    excluded = set(state.get("drafts", {}))
    if exclude_topic:
        excluded.add(exclude_topic)
    for nid in excluded:
        prefix = nid + "/"
        local = {path: data for path, data in local.items() if not path.startswith(prefix)}
    remote_files = main_view(commit_files(repo, remote))
    decisions = three_way(local, base, remote_files)
    conflicts = [path for path, (kind, _) in decisions.items() if kind == "conflict"]
    if conflicts and force:
        force_decisions(decisions, local, remote_files, "local")
        conflicts = []
    if conflicts:
        report_conflicts("main", conflicts)
        return False, state.get("main")
    wanted = {path: value for path, (kind, value) in decisions.items() if kind == "local"}
    if not wanted:
        state["main"] = remote
        save_state(root, state)
        print("sync: main up to date")
        return True, remote

    checkout_clean(repo, remote)
    for path, data in wanted.items():
        target = os.path.join(repo, *path.split("/"))
        if data is None:
            if os.path.exists(target):
                os.remove(target)
        else:
            write_file(target, data)
    sha = commit(repo, "sync: update teaching memory")
    sha = push_ref(repo, f"refs/heads/{state.get('branch') or MAIN}", state.get("branch") or MAIN)
    state["main"] = sha
    save_state(root, state)
    print(f"sync: pushed main ({len(wanted)} file(s))")
    return True, sha


def push_draft(root: str, state: dict, nid: str, require_draft: bool = True, force: bool = False) -> bool:
    repo = ensure_repo(root)
    if require_draft and not os.path.exists(note.draft_path(root, nid)):
        raise SyncError(f"no draft at {note.draft_path(root, nid)}")
    local = local_draft_files(root, nid)
    if not local:
        raise SyncError(f"nothing to push for {nid}")
    fetch(root)
    branch = draft_branch(nid)
    remote_sha = sha_of(repo, f"refs/remotes/{REMOTE}/{branch}")
    base_info = state.get("drafts", {}).get(nid) or {}
    base_sha = base_info.get("commit")
    base = topic_files(commit_files(repo, base_sha), nid) if base_sha else {}
    remote = topic_files(commit_files(repo, f"refs/remotes/{REMOTE}/{branch}"), nid) if remote_sha else {}
    if base_sha and remote_sha is None and not force:
        print(
            f"sync: draft {nid}: cloud branch was deleted (archived elsewhere?) — "
            "not recreating it; run `sync.py archive` after resolving",
            file=sys.stderr,
        )
        return False
    decisions = three_way(local, base, remote)
    conflicts = [path for path, (kind, _) in decisions.items() if kind == "conflict"]
    if conflicts and force:
        force_decisions(decisions, local, remote, "local")
        conflicts = []
    if conflicts:
        report_conflicts(f"draft {nid}", conflicts)
        return False
    if remote_sha and not decisions:
        state.setdefault("drafts", {})[nid] = {"branch": branch, "commit": remote_sha}
        save_state(root, state)
        print(f"sync: draft {nid} up to date")
        return True

    base_rev = remote_sha or state.get("main")
    if base_rev is None:
        raise SyncError("cloud main has no commits yet — re-run `sync.py init <url>`")
    checkout_clean(repo, base_rev)
    topic_dir = os.path.join(repo, *nid.split("/"))
    shutil.rmtree(topic_dir, ignore_errors=True)
    merged = dict(remote)
    for path, (kind, value) in decisions.items():
        if kind == "local":
            if value is None:
                merged.pop(path, None)
            else:
                merged[path] = value
    for path, data in merged.items():
        write_file(os.path.join(repo, *path.split("/")), data)
    sha = commit(repo, f"draft: {nid}")
    sha = push_ref(repo, f"refs/heads/{branch}", branch)
    state.setdefault("drafts", {})[nid] = {"branch": branch, "commit": sha}
    save_state(root, state)
    print(f"sync: draft {nid} pushed to {branch}")
    return True


def pull_main(root: str, state: dict, force: bool = False) -> tuple[bool, int, int]:
    repo = ensure_repo(root)
    fetch(root)
    remote = sha_of(repo, branch_ref(state))
    if remote is None:
        return True, 0, 0
    base = main_view(commit_files(repo, state.get("main"))) if state.get("main") else {}
    remote_files = main_view(commit_files(repo, remote))
    local = local_main_files(root)
    decisions = three_way(local, base, remote_files)
    conflicts = [path for path, (kind, _) in decisions.items() if kind == "conflict"]
    if conflicts and force:
        force_decisions(decisions, local, remote_files, "remote")
        conflicts = []
    updated = 0
    for path, (kind, value) in decisions.items():
        if kind == "remote":
            write_local(root, path, value)
            updated += 1
    pending = len([kind for kind, _ in decisions.values() if kind == "local"])
    if conflicts:
        report_conflicts("main", conflicts)
        return False, updated, pending
    state["main"] = remote
    save_state(root, state)
    print(f"sync: main pulled ({updated} file(s) updated, {pending} local pending)")
    return True, updated, pending


def pull_drafts(root: str, state: dict, force: bool = False) -> tuple[bool, int, int]:
    repo = ensure_repo(root)
    listing = git_text(
        repo, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin/script", check=False
    )
    remote_branches: dict[str, str] = {}
    prefix = f"{REMOTE}/{BRANCH_PREFIX}"
    for line in listing.splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        nid = line[len(prefix):]
        if nid.count("/") == 1:
            remote_branches[nid] = line
    ok = True
    updated = new = 0
    for nid, ref in sorted(remote_branches.items()):
        if os.path.exists(note.note_path(root, nid)):
            print(
                f"sync: WARNING remote branch {draft_branch(nid)} exists for archived topic "
                f"{nid}; run `sync.py archive {nid}` to finish it",
                file=sys.stderr,
            )
            continue
        remote_files = topic_files(commit_files(repo, ref), nid)
        local = local_draft_files(root, nid)
        base_sha = (state.get("drafts", {}).get(nid) or {}).get("commit")
        base = topic_files(commit_files(repo, base_sha), nid) if base_sha else {}
        decisions = three_way(local, base, remote_files)
        conflicts = [path for path, (kind, _) in decisions.items() if kind == "conflict"]
        if conflicts and force:
            force_decisions(decisions, local, remote_files, "remote")
            conflicts = []
        if conflicts:
            report_conflicts(f"draft {nid}", conflicts)
            ok = False
            continue
        if not local:
            # A missing local draft is pulled back in full (the branch is the backup).
            for path, data in remote_files.items():
                write_draft_local(root, nid, path, data)
            new += 1
        else:
            wrote = False
            for path, (kind, value) in decisions.items():
                if kind == "remote":
                    write_draft_local(root, nid, path, value)
                    wrote = True
            if wrote:
                updated += 1
        state.setdefault("drafts", {})[nid] = {
            "branch": draft_branch(nid),
            "commit": sha_of(repo, ref),
        }
        save_state(root, state)

    known = set(state.get("drafts", {}))
    for nid in sorted(known - set(remote_branches)):
        local = local_draft_files(root, nid)
        base_sha = (state.get("drafts", {}).get(nid) or {}).get("commit")
        base = topic_files(commit_files(repo, base_sha), nid) if base_sha else {}
        if not local:
            state["drafts"].pop(nid, None)
            save_state(root, state)
            continue
        if local == base:
            write_draft_local(root, nid, f"{nid}/{note.NOTE_FILE}", None)
            state["drafts"].pop(nid, None)
            save_state(root, state)
            print(f"sync: draft {nid} archived elsewhere — local draft removed")
        elif local:
            print(
                f"sync: WARNING cloud branch for draft {nid} is gone but local changes differ; "
                "left untouched",
                file=sys.stderr,
            )
            ok = False
    print(f"sync: drafts pulled ({new} new, {updated} updated)")
    return ok, updated, new


def _init_main(repo: str, state: dict) -> str:
    """Create the first main commit for an empty remote."""
    if has_ref(repo, branch_ref(state)):
        return sha_of(repo, branch_ref(state)) or ""
    if not has_ref(repo, "refs/heads/main"):
        git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    git(repo, "commit", "--allow-empty", "-m", "init teaching notes")
    sha = push_ref(repo, "refs/heads/main", "main")
    state["branch"] = "main"
    state["main"] = sha
    return sha


def init(root: str, remote: str) -> int:
    os.makedirs(root, exist_ok=True)
    sdir = repo_path(root)
    if is_configured(root):
        state = load_state(root)
        if state.get("remote") and state["remote"] != remote:
            raise SyncError(
                f"{sdir} is already bound to {display_remote(state['remote'])}; refusing to rebind"
            )
        print(f"sync: already initialized ({display_remote(remote)})")
        ok, _, _ = pull_main(root, state)
        pull_drafts(root, state)
        return 0 if ok else 1
    if os.path.exists(sdir) and os.listdir(sdir):
        raise SyncError(f"{sdir} exists and is not the sync clone; move it away first")

    print(f"sync: cloning {display_remote(remote)}")
    proc = run(["git", "clone", "--quiet", remote, sdir])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()
        raise SyncError(f"clone failed: {detail}")
    ignore_local_state(sdir)
    state = {"remote": remote, "branch": "main", "main": None, "drafts": {}}
    proc = git(sdir, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    if proc.returncode == 0:
        state["branch"] = git_text(sdir, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").split("/")[-1]
        state["main"] = sha_of(sdir, branch_ref(state))
    else:
        candidate = detect_branch(sdir)
        state["branch"] = candidate
        state["main"] = sha_of(sdir, branch_ref(state))
    save_state(root, state)

    if state["main"] is None:
        _init_main(sdir, state)
        git(sdir, "fetch", "--prune", REMOTE, check=False)
        save_state(root, state)

    # First join: copy remote content locally; never overwrite a differing local file.
    remote_files = main_view(commit_files(sdir, branch_ref(state)))
    conflicts = []
    for path, data in remote_files.items():
        local_path = os.path.join(root, *path.split("/"))
        if not os.path.exists(local_path):
            write_file(local_path, data)
        elif read_file(local_path) != data:
            conflicts.append(path)
    for path in conflicts:
        print(f"sync: CONFLICT main: {path} (local kept; remote not overwritten)", file=sys.stderr)
    save_state(root, state)
    if conflicts:
        print(
            "sync: first join has conflicts; nothing was pushed. Compare the files above, "
            "then `sync.py pull --force` (take cloud) or `sync.py push --force` (keep local).",
            file=sys.stderr,
        )
        ok = False
    else:
        ok, _ = push_main(root, state)

    local_drafts = sorted(note.find_drafts(root))
    ok_drafts, _, _ = pull_drafts(root, state)
    ok = ok and ok_drafts
    for nid in local_drafts:
        try:
            push_draft(root, state, nid)
        except SyncError as exc:
            print(f"sync: draft {nid}: {exc}", file=sys.stderr)
            ok = False
    save_state(root, state)
    print(
        f"sync: initialized {display_remote(remote)} "
        f"({len(note.load_notes(root))} archived, {len(note.find_drafts(root))} draft(s))"
    )
    return 0 if ok else 1


def detect_branch(repo: str) -> str:
    for name in ("main", "master"):
        if has_ref(repo, f"refs/remotes/{REMOTE}/{name}"):
            return name
    return MAIN


def archive(root: str, state: dict, nid: str, force: bool = False) -> int:
    archived_note = note.note_path(root, nid)
    if not os.path.exists(archived_note):
        raise SyncError(f"{archived_note} does not exist — run `note.py promote` first")
    if os.path.exists(note.draft_path(root, nid)):
        raise SyncError("the draft still exists — run `note.py promote` first")
    repo = ensure_repo(root)
    fetch(root)
    branch = draft_branch(nid)
    remote_main = main_view(commit_files(repo, branch_ref(state)))
    local_main = local_main_files(root)
    topic_note = f"{nid}/{note.NOTE_FILE}"
    if topic_note in remote_main:
        if remote_main[topic_note] == local_main.get(topic_note):
            delete_remote_branch(repo, branch)
            state.get("drafts", {}).pop(nid, None)
            state["main"] = sha_of(repo, branch_ref(state))
            save_state(root, state)
            print(f"sync: {nid} was already archived in the cloud")
            return 0
        raise SyncError(f"{nid} already exists on cloud main with different content")
    if not push_draft(root, state, nid, require_draft=False, force=force):
        return 1
    ok, main_sha = push_main(root, state, exclude_topic=nid, force=force)
    if not ok or not main_sha:
        print("sync: archive aborted before merge — resolve the main conflicts first", file=sys.stderr)
        return 1
    checkout_clean(repo, main_sha)
    merge = git(repo, "merge", "--no-ff", "--no-edit", "-m", f"archive {nid}", state["drafts"][nid]["commit"], check=False)
    if merge.returncode != 0:
        git(repo, "merge", "--abort", check=False)
        detail = (merge.stderr or merge.stdout).decode("utf-8", "replace").strip()
        raise SyncError(f"merge of {branch} failed: {detail}")
    sha = git_text(repo, "rev-parse", "HEAD")
    try:
        sha = push_ref(repo, f"refs/heads/{state.get('branch') or MAIN}", state.get("branch") or MAIN)
    except SyncError:
        state["main"] = main_sha
        save_state(root, state)
        raise
    state["main"] = sha
    state.get("drafts", {}).pop(nid, None)
    delete_remote_branch(repo, branch)
    save_state(root, state)
    print(f"sync: {nid} merged into main ({display_remote(state.get('remote'))})")
    return 0


# ------------------------------------------------------------------------ commands


def cmd_init(args: argparse.Namespace) -> int:
    return init(resolve_notes_root(args.notes), args.remote)


def cmd_pull(args: argparse.Namespace) -> int:
    root = resolve_notes_root(args.notes)
    if not is_configured(root):
        notes, drafts = local_note_count(root)
        print(f"sync: local-only ({notes} note(s), {drafts} draft(s)); run `sync.py init <url>`")
        return 0
    state = load_state(root)
    ok, _, _ = pull_main(root, state, force=args.force)
    ok_drafts, _, _ = pull_drafts(root, state, force=args.force)
    return 0 if ok and ok_drafts else 1


def cmd_push(args: argparse.Namespace) -> int:
    root = resolve_notes_root(args.notes)
    if not is_configured(root):
        print("sync: local-only; run `sync.py init <url>` once to back the notes up")
        return 0
    ok, _ = push_main(root, load_state(root), force=args.force)
    return 0 if ok else 1


def cmd_draft(args: argparse.Namespace) -> int:
    root = resolve_notes_root(args.notes)
    nid = args.topic
    if nid.count("/") != 1:
        raise SystemExit("error: topic must be `<project>/<topic>`")
    if not is_configured(root):
        print("sync: local-only; run `sync.py init <url>` once to back drafts up")
        return 0
    if not os.path.exists(note.draft_path(root, nid)):
        print(f"sync: no draft at {note.draft_path(root, nid)}; use `sync.py archive` after promote", file=sys.stderr)
        return 1
    state = load_state(root)
    ok_main, _ = push_main(root, state, force=args.force)
    ok = push_draft(root, state, nid, force=args.force)
    return 0 if ok_main and ok else 1


def cmd_archive(args: argparse.Namespace) -> int:
    root = resolve_notes_root(args.notes)
    nid = args.topic
    if nid.count("/") != 1:
        raise SystemExit("error: topic must be `<project>/<topic>`")
    if not is_configured(root):
        print("sync: local-only; run `sync.py init <url>` once to archive to the cloud")
        return 0
    return archive(root, load_state(root), nid, force=args.force)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="clone the cloud repo into <notes>/.sync (once per machine)")
    p_init.add_argument("remote", help="private git remote, e.g. https://github.com/you/teaching-notes.git")
    p_init.add_argument("notes", nargs="?", help="notes root (default: TEACHING_NOTES_DIR or <skill>/memory)")
    p_init.set_defaults(func=cmd_init)

    p_pull = sub.add_parser("pull", help="fetch and apply main + every script/* draft branch")
    p_pull.add_argument("notes", nargs="?", help="notes root")
    p_pull.add_argument("--force", action="store_true", help="on conflict, take the cloud version")
    p_pull.set_defaults(func=cmd_pull)

    p_push = sub.add_parser("push", help="sync main-side content (_project.md, archived corrections)")
    p_push.add_argument("notes", nargs="?", help="notes root")
    p_push.add_argument("--force", action="store_true", help="on conflict, keep the local version")
    p_push.set_defaults(func=cmd_push)

    p_draft = sub.add_parser("draft", help="push one draft to script/<project>/<topic>")
    p_draft.add_argument("topic", help="project/topic")
    p_draft.add_argument("notes", nargs="?", help="notes root")
    p_draft.add_argument("--force", action="store_true", help="on conflict, keep the local version")
    p_draft.set_defaults(func=cmd_draft)

    p_arch = sub.add_parser("archive", help="merge a promoted topic's branch into main")
    p_arch.add_argument("topic", help="project/topic")
    p_arch.add_argument("notes", nargs="?", help="notes root")
    p_arch.add_argument("--force", action="store_true", help="on conflict, keep the local version")
    p_arch.set_defaults(func=cmd_archive)

    args = ap.parse_args()
    try:
        return args.func(args)
    except (SyncError, OSError) as exc:
        print(f"sync: FAILED — {display_remote(str(exc))}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
