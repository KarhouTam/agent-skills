#!/usr/bin/env python3
"""Fetch the pinned public skills listed in skills.json into this directory.

Fetched skills are deliberately untracked; see .gitignore. Self-developed
skills are version-controlled and are never touched by this script.

    python3 sync_skills.py --list          # show the manifest
    python3 sync_skills.py                 # install anything missing
    python3 sync_skills.py --force         # refresh to the pinned revisions
    python3 sync_skills.py --only handoff  # one skill (repeatable)
"""

import argparse
import http.client
import io
import json
import os
import shutil
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "skills.json"
CODELOAD = "https://codeload.github.com/{repo}/tar.gz/{ref}"
TIMEOUT = 30
RETRIES = 3

_archives = {}


def download(repo, ref):
    """Download (and memoise) the tarball for a pinned repo revision."""
    key = (repo, ref)
    if key in _archives:
        return _archives[key]

    url = CODELOAD.format(repo=repo, ref=ref)
    for attempt in range(1, RETRIES + 1):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "sync-skills"})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                _archives[key] = response.read()
            return _archives[key]
        except (OSError, http.client.HTTPException) as exc:
            if attempt == RETRIES:
                raise RuntimeError(f"download failed for {repo}@{ref[:12]}: {exc}") from exc
            print(f"retry {attempt}/{RETRIES - 1} for {repo}: {exc}", file=sys.stderr, flush=True)
            time.sleep(attempt * 2)


def write_member(tar, member, dest, suffix):
    """Extract one archive member to dest/suffix, refusing to escape dest."""
    target = dest / suffix
    if not target.resolve().is_relative_to(dest.resolve()):
        raise RuntimeError(f"unsafe path in archive: {member.name}")

    if member.isdir():
        target.mkdir(parents=True, exist_ok=True)
    elif member.issym():
        link = member.linkname
        if os.path.isabs(link) or not (target.parent / link).resolve().is_relative_to(dest.resolve()):
            raise RuntimeError(f"unsafe symlink in archive: {member.name} -> {link}")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(link, target)
    elif member.isreg():
        target.parent.mkdir(parents=True, exist_ok=True)
        with tar.extractfile(member) as source, open(target, "wb") as handle:
            shutil.copyfileobj(source, handle)
        os.chmod(target, member.mode & 0o777)
    else:
        raise RuntimeError(f"unsupported archive member: {member.name}")


def extract(blob, src_path, dest):
    """Write the archive subtree at src_path into dest."""
    src_path = src_path.strip("/")
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        names = tar.getnames()
        if not names:
            raise RuntimeError("empty archive")
        top = names[0].split("/", 1)[0]

        found = False
        for member in tar.getmembers():
            parts = member.name.split("/")
            if parts[0] != top:
                continue
            rel = "/".join(parts[1:])
            if rel != src_path and not rel.startswith(src_path + "/"):
                continue
            suffix = rel[len(src_path):].lstrip("/")
            if not suffix:
                continue
            found = True
            write_member(tar, member, dest, suffix)
        if not found:
            raise RuntimeError(f"path not found in archive: {src_path}")


def write_interface(dest, interface):
    """Generate agents/openai.yaml when upstream ships none. Returns True if written."""
    if not interface:
        return False
    path = dest / "agents" / "openai.yaml"
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "interface:",
        f'  display_name: "{escape(interface["display_name"])}"',
        f'  short_description: "{escape(interface["short_description"])}"',
    ]
    if interface.get("allow_implicit_invocation") is False:
        lines += ["policy:", "  allow_implicit_invocation: false"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def install(name, repo, ref, entry, force):
    """Install one skill. Returns (status, metadata_generated)."""
    dest = ROOT / name
    if dest.exists() and not force:
        return "skipped", False

    with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
        staged = Path(tmp) / name
        extract(download(repo, ref), entry["path"], staged)
        generated = write_interface(staged, entry.get("interface"))
        if dest.exists():
            shutil.rmtree(dest)
        staged.rename(dest)
    return "installed", generated


def iter_skills(manifest):
    for source in manifest["sources"]:
        for name, entry in source["skills"].items():
            yield name, source["repo"], source["ref"], entry


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--force", action="store_true", help="overwrite skills that already exist")
    parser.add_argument("--only", action="append", metavar="NAME", help="sync only this skill (repeatable)")
    parser.add_argument("--list", action="store_true", help="show the manifest without fetching")
    args = parser.parse_args(argv)

    skills = list(iter_skills(json.loads(MANIFEST.read_text(encoding="utf-8"))))

    if args.only:
        unknown = sorted(set(args.only) - {name for name, *_ in skills})
        if unknown:
            parser.error(f"unknown skill(s): {', '.join(unknown)}")
        skills = [skill for skill in skills if skill[0] in set(args.only)]

    if args.list:
        width = max(len(name) for name, *_ in skills)
        for name, repo, ref, _ in skills:
            state = "present" if (ROOT / name).is_dir() else "missing"
            print(f"{name:<{width}}  {state:<7}  {repo}@{ref[:12]}")
        return 0

    failures = 0
    for name, repo, ref, entry in skills:
        try:
            status, generated = install(name, repo, ref, entry, args.force)
        except Exception as exc:
            failures += 1
            print(f"{'FAIL':<9} {name}: {exc}", flush=True)
            continue
        suffix = " (+agents/openai.yaml)" if generated else ""
        print(f"{status.upper():<9} {name}{suffix}", flush=True)

    if failures:
        print(f"\n{failures} skill(s) failed", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:  # e.g. `--list | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
