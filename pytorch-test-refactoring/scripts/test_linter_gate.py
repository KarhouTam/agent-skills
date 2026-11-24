"""Mandatory TEST_LINTER gate for the PR review queue pre-pass.

Runs the vendored official linter (``scripts/linter.py`` at a pinned SHA)
against the PR-head version of the PR's changed test files, mirroring the
upstream ``.lintrunner.toml`` invocation: the linter and its allowlist are
copied into a repo-layout mirror, head files are written at their original
relative paths, and the linter runs with that root as its working directory —
so the allowlist's relative-path matching behaves exactly as it does in CI.

The gate is mandatory: a PR is never reviewed, let alone published, without
good gate output. Gate failures leave the PR pending for the next run.

It also retires stale allowlist entries: a changed test file the official
allowlist exempts is re-checked with an empty allowlist, and when it lints
clean while the PR-head allowlist still lists it, the gate reports a
`TEST_LINTER_ALLOWLIST` finding (a Blocker per the review criteria, II.11).
"""

from __future__ import annotations

import fnmatch
import json
import shutil
import subprocess
import sys
from pathlib import Path

from utils import run_gh as _run_gh

VENDORED_SCRIPT = Path(__file__).resolve().parent / "linter.py"
VENDORED_ALLOWLIST = (
    Path(__file__).resolve().parent / "test_linter_allowlist.json"
)

# Mirrors the upstream `.lintrunner.toml` include_patterns and the vendored
# linter's own scope constants; keep in sync when re-vendoring.
REPO = "pytorch/pytorch"
TEST_DIR = "test"
FILE_NAME_PATTERNS = ("test_*.py", "*_test.py")
EXCLUDE_PREFIXES = (
    "test/cpp_extensions/open_registration_extension/",
    "test/cpython/",
)
MAX_LINTED_FILES = 30
ALLOWLIST_REL_PATH = "tools/linter/adapters/test_linter_allowlist.json"

GATE_FINDING_FIELDS = ("check", "file", "line_number", "evidence")


def _is_test_path(rel_path: str) -> bool:
    """True for a path in the upstream linter's scope."""
    if not rel_path.startswith(f"{TEST_DIR}/") or not rel_path.endswith(".py"):
        return False
    if rel_path.startswith(EXCLUDE_PREFIXES):
        return False
    name = rel_path.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in FILE_NAME_PATTERNS)


def _changed_paths(patch: str) -> list[str]:
    """Paths touched by the patch, new-side names, in first-seen order.

    Splits the `diff --git` header at the first ` b/` separator, which is
    safe for the repo's test-file paths (they cannot contain " b/").
    """
    prefix = "diff --git a/"
    paths: list[str] = []
    for line in patch.splitlines():
        if line.startswith(prefix):
            rest = line[len(prefix) :].split(" b/", 1)[0]
            if rest:
                paths.append(rest)
    return paths


def _fetch_head_file(rel_path: str, ref: str) -> str | None:
    """Raw content of `rel_path` at `ref`, or None when the path is deleted."""
    try:
        return _run_gh(
            "api",
            "-H",
            "Accept: application/vnd.github.raw",
            f"repos/{REPO}/contents/{rel_path}?ref={ref}",
        )
    except subprocess.CalledProcessError as exc:
        stderr = str(getattr(exc, "stderr", "") or "")
        if "404" in stderr or "Not Found" in stderr:
            return None  # deleted at PR head; nothing to lint
        raise


def _gate_findings(output: str) -> list[dict]:
    """Error-severity lint messages as pre-pass finding dicts."""
    findings: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict) and msg.get("severity") == "error":
            findings.append(
                {
                    "check": "TEST_LINTER",
                    "file": msg.get("path", ""),
                    "line_number": msg.get("line", 0) or 0,
                    "evidence": f"{msg.get('name', '')}: {msg.get('description', '') or ''}"
                    .strip(),
                }
            )
    return findings


def _stale_allowlist_entries(
    root: Path, fetched: list[str], deleted: list[str] = ()
) -> list[str]:
    """Changed files whose allowlist entry is now obsolete.

    The normal pass applies the official allowlist, so an exempted file is
    never checked. Re-running the linter with an empty allowlist exposes the
    entries that outlived their violations: a file with no error-severity
    message lints clean on its own and must leave the allowlist. A file the PR
    deleted is obsolete by the same logic.
    """
    allowlist_file = (
        root / "tools" / "linter" / "adapters" / "test_linter_allowlist.json"
    )
    official = set(json.loads(allowlist_file.read_text(encoding="utf-8")))
    stale = [p for p in deleted if p in official]
    candidates = [p for p in fetched if p in official]
    if not candidates:
        return stale
    saved = allowlist_file.read_text(encoding="utf-8")
    try:
        allowlist_file.write_text("[]\n", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "tools/linter/adapters/test_linter.py", *candidates],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=300,
        )
    finally:
        allowlist_file.write_text(saved, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError("allowlist re-check failed")
    failing = {f["file"] for f in _gate_findings(proc.stdout)}
    return stale + [p for p in candidates if p not in failing]


def _allowlist_findings(head_oid: str, stale: list[str]) -> list[dict]:
    """Stale entries the PR-head allowlist still carries."""
    content = _fetch_head_file(ALLOWLIST_REL_PATH, head_oid)
    if content is None:
        return []  # allowlist deleted at head: every entry is gone with it
    try:
        head_entries = set(json.loads(content))
    except json.JSONDecodeError as exc:
        raise RuntimeError("head allowlist is not valid JSON") from exc
    return [
        {
            "check": "TEST_LINTER_ALLOWLIST",
            "file": rel,
            "line_number": 0,
            "evidence": (
                f"{rel} leaves a stale entry in {ALLOWLIST_REL_PATH}: the file "
                "was deleted or now lints clean without it, so this PR must "
                "remove that entry."
            ),
        }
        for rel in stale
        if rel in head_entries
    ]


def _run_gate(
    pr_number: int, head_oid: str, patch: str, workspace: Path
) -> dict:
    """Materialize the mirror and lint the PR's head test files."""
    raw_changed = _changed_paths(patch)
    allowlist_changed = ALLOWLIST_REL_PATH in raw_changed
    changed = [p for p in dict.fromkeys(raw_changed) if _is_test_path(p)]
    if not changed:
        if allowlist_changed:
            return {
                "ran": True,
                "skipped": True,
                "reason": "allowlist_changed",
                "findings": [],
            }
        return {"ran": True, "skipped": False, "reason": "", "findings": []}
    if not head_oid:
        return {
            "ran": False,
            "skipped": False,
            "reason": "gate_failed",
            "findings": [],
        }
    if len(changed) > MAX_LINTED_FILES:
        return {
            "ran": False,
            "skipped": False,
            "reason": "too_many_changed_files",
            "findings": [],
        }

    root = Path(workspace) / "linter_gate" / f"pr_{pr_number}"
    adapters = root / "tools" / "linter" / "adapters"
    adapters.mkdir(parents=True, exist_ok=True)
    shutil.copy2(VENDORED_SCRIPT, adapters / "test_linter.py")
    shutil.copy2(VENDORED_ALLOWLIST, adapters / "test_linter_allowlist.json")

    fetched: list[str] = []
    for rel in changed:
        content = _fetch_head_file(rel, head_oid)
        if content is None:
            continue
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        fetched.append(rel)

    stale = _stale_allowlist_entries(
        root, fetched, [p for p in changed if p not in fetched]
    )
    findings = _allowlist_findings(head_oid, stale) if stale else []
    if allowlist_changed:
        return {
            "ran": True,
            "skipped": True,
            "reason": "allowlist_changed",
            "findings": findings,
        }
    if not fetched:
        return {"ran": True, "skipped": False, "reason": "", "findings": findings}

    proc = subprocess.run(
        [sys.executable, "tools/linter/adapters/test_linter.py", *fetched],
        cwd=str(root),
        capture_output=True,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        return {
            "ran": False,
            "skipped": False,
            "reason": "gate_failed",
            "findings": [],
        }
    return {
        "ran": True,
        "skipped": False,
        "reason": "",
        "findings": _gate_findings(proc.stdout) + findings,
    }


def run_gate(pr_number: int, head_oid: str, patch: str, workspace: Path) -> dict:
    """Run the mandatory TEST_LINTER gate over one PR's changed test files.

    Always returns a gate dict (`ran`/`skipped`/`reason`/`findings`); an
    unexpected error is reported as `ran: false, reason: gate_failed` so
    callers never have to handle gate crashes, they only honor `ran: false`.
    """
    try:
        return _run_gate(pr_number, head_oid, patch, workspace)
    except Exception:
        return {
            "ran": False,
            "skipped": False,
            "reason": "gate_failed",
            "findings": [],
        }
