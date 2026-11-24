#!/usr/bin/env python3
"""Batch manager for PRs under automated review.

Reads ``agent_space/pr_needs_monitor.txt`` (one GitHub PR URL per line; blank
lines and ``#`` comments are ignored) and in one pass:

1. adds the ``in progress`` label to a PR that has neither ``in progress`` nor
   ``ready for review``;
2. moves a PR that has ``ready for review`` and no ``in progress`` to
   ``agent_space/pr_ready_for_review.txt``;
3. drops closed/merged PRs from the monitor file.

GitHub is the single source of truth for label state, so the pass keeps no
local state and is idempotent: it never labels a PR twice, never duplicates a
ready entry, and never re-adds a label the external reviewer removed. A PR
carrying both labels is reported and left alone.

``--dry-run`` prints the decisions without touching GitHub or the files.
Exit status is 1 when any ``gh`` call failed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

IN_PROGRESS = "in progress"
READY_FOR_REVIEW = "ready for review"

DEFAULT_DIR = Path(__file__).resolve().parent
MONITOR_REL = Path("agent_space") / "pr_needs_monitor.txt"
READY_REL = Path("agent_space") / "pr_ready_for_review.txt"

_PR_URL = re.compile(
    r"https?://(?:www\.)?github\.com/([^/\s]+)/([^/\s]+)/pull(?:s)?/(\d+)"
)

KINDS = ("labeled", "moved", "waiting", "dropped", "stuck", "error")


@dataclass(frozen=True)
class Entry:
    """One PR URL from the monitor file."""

    repo: str  # "owner/name"
    number: int
    url: str

    @property
    def key(self) -> tuple[str, int]:
        return self.repo, self.number


@dataclass
class Outcome:
    entry: Entry
    kind: str
    detail: str = ""


# ── monitor file ────────────────────────────────────────────────────


def parse_entry(line: str) -> Entry | None:
    m = _PR_URL.search(line)
    if m is None:
        return None
    owner, name, number = m.group(1), m.group(2), int(m.group(3))
    return Entry(f"{owner}/{name}", number, f"https://github.com/{owner}/{name}/pull/{number}")


def read_entries(path: Path) -> list[Entry]:
    if not path.exists():
        return []
    entries: list[Entry] = []
    seen: set[tuple[str, int]] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        entry = parse_entry(line)
        if entry is not None and entry.key not in seen:
            seen.add(entry.key)
            entries.append(entry)
    return entries


# ── gh ──────────────────────────────────────────────────────────────


def run_gh(*args: str) -> str:
    try:
        result = subprocess.run(
            ["gh", *args], check=True, capture_output=True, text=True
        )
    except FileNotFoundError as exc:
        raise RuntimeError("gh CLI not found on PATH") from exc
    return result.stdout


def fetch_state(entry: Entry) -> dict:
    out = run_gh(
        "pr", "view", str(entry.number), "--repo", entry.repo,
        "--json", "state,labels",
    )
    data = json.loads(out)
    return {
        "state": data.get("state", ""),
        "labels": [label.get("name", "") for label in data.get("labels") or []],
    }


def add_label(entry: Entry) -> None:
    run_gh(
        "pr", "edit", str(entry.number), "--repo", entry.repo,
        "--add-label", IN_PROGRESS,
    )


# ── per-PR decision ─────────────────────────────────────────────────


def decide(entry: Entry, dry_run: bool) -> Outcome:
    try:
        info = fetch_state(entry)
    except (subprocess.CalledProcessError, json.JSONDecodeError, RuntimeError) as exc:
        return Outcome(entry, "error", _error_text(exc))

    labels = set(info["labels"])
    if info["state"] != "OPEN":
        return Outcome(entry, "dropped", info["state"])
    if READY_FOR_REVIEW in labels:
        if IN_PROGRESS in labels:
            return Outcome(
                entry, "stuck", f"both labels set; remove '{IN_PROGRESS}'"
            )
        return Outcome(entry, "moved")
    if IN_PROGRESS in labels:
        return Outcome(entry, "waiting")
    if not dry_run:
        try:
            add_label(entry)
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            return Outcome(entry, "error", _error_text(exc))
    return Outcome(entry, "labeled")


# ── batch pass ──────────────────────────────────────────────────────


def run(monitor: Path, ready: Path, dry_run: bool = False) -> list[Outcome]:
    entries = read_entries(monitor)
    if not entries:
        return []

    outcomes = [decide(entry, dry_run) for entry in entries]
    moved = [o.entry for o in outcomes if o.kind == "moved"]
    dropped = {o.entry.key for o in outcomes if o.kind == "dropped"}
    if dry_run:
        return outcomes

    if moved:
        append_ready(ready, moved)
    removed = dropped | {entry.key for entry in moved}
    if removed:
        rewrite_monitor(monitor, removed)
    return outcomes


def append_ready(path: Path, entries: list[Entry]) -> None:
    known = {entry.key for entry in read_entries(path)}
    added = [entry for entry in entries if entry.key not in known]
    if not added:
        return
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    _write_atomic(path, text + "".join(f"{entry.url}\n" for entry in added))


def rewrite_monitor(path: Path, removed: set[tuple[str, int]]) -> None:
    kept: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = parse_entry(line)
        if entry is None or entry.key not in removed:
            kept.append(line)
    _write_atomic(path, "".join(f"{line}\n" for line in kept))


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── report ──────────────────────────────────────────────────────────


def render(outcomes: list[Outcome], dry_run: bool) -> list[str]:
    prefix = "would " if dry_run else ""
    rows: list[str] = []
    for outcome in outcomes:
        kind = outcome.kind
        if kind == "labeled":
            message = f"{prefix}add '{IN_PROGRESS}'"
        elif kind == "moved":
            message = f"{prefix}move to {READY_REL}"
        elif kind == "waiting":
            message = f"waiting ('{IN_PROGRESS}' still set)"
        elif kind == "dropped":
            message = f"{prefix}drop ({outcome.detail} PR)"
        elif kind == "stuck":
            message = f"NEEDS ATTENTION: {outcome.detail}"
        else:
            message = f"ERROR: {outcome.detail}"
        rows.append(f"#{outcome.entry.number:<7} {kind:<8} {message}")
    return rows


def summarize(outcomes: list[Outcome]) -> str:
    counts = Counter(outcome.kind for outcome in outcomes)
    parts = [f"{counts[kind]} {kind}" for kind in KINDS if counts[kind]]
    return f"summary: {len(outcomes)} PR(s) checked — {', '.join(parts)}"


def _error_text(exc: Exception) -> str:
    text = str(exc)
    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
        text = exc.stderr.strip().splitlines()[-1]
    text = " ".join(text.split()) or exc.__class__.__name__
    return text if len(text) <= 160 else text[:157] + "..."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Process agent_space/pr_needs_monitor.txt in one batch."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report decisions without changing GitHub or the files",
    )
    args = parser.parse_args(argv)

    monitor = DEFAULT_DIR / MONITOR_REL
    ready = DEFAULT_DIR / READY_REL
    outcomes = run(monitor, ready, dry_run=args.dry_run)
    if not outcomes:
        print(f"nothing to do: no PR URLs in {monitor}")
        return 0

    for row in render(outcomes, args.dry_run):
        print(row)
    print(summarize(outcomes))
    return 1 if any(outcome.kind == "error" for outcome in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(main())
