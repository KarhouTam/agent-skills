"""Unit tests for the standalone PR monitor batch script (pr_monitor.py)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pr_monitor


class FakeGh:
    def __init__(self, prs: dict[int, dict | Exception]) -> None:
        self.prs = prs
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, *args: str) -> str:
        self.calls.append(args)
        if args[:2] == ("pr", "view"):
            pr = self.prs[int(args[2])]
            if isinstance(pr, Exception):
                raise pr
            return json.dumps(
                {
                    "state": pr.get("state", "OPEN"),
                    "labels": [{"name": name} for name in pr.get("labels", [])],
                }
            )
        return ""


@pytest.fixture
def files(tmp_path, monkeypatch):
    monitor = tmp_path / "pr_needs_monitor.txt"
    ready = tmp_path / "pr_ready_for_review.txt"
    monitor.write_text(
        "# batch one\n"
        "https://github.com/pytorch/pytorch/pull/101\n"
        "https://github.com/pytorch/pytorch/pull/102\n"
        "\n"
        "https://github.com/pytorch/pytorch/pull/103\n"
        "https://github.com/pytorch/pytorch/pull/104\n",
        encoding="utf-8",
    )
    fake = FakeGh(
        {
            101: {"labels": []},
            102: {"labels": [pr_monitor.IN_PROGRESS]},
            103: {"labels": [pr_monitor.READY_FOR_REVIEW]},
            104: {"state": "MERGED", "labels": []},
        }
    )
    monkeypatch.setattr(pr_monitor, "run_gh", fake)
    return monitor, ready, fake


def _numbers(path: Path) -> set[int]:
    return {entry.number for entry in pr_monitor.read_entries(path)}


def test_parse_entry_extracts_number_and_ignores_noise():
    entry = pr_monitor.parse_entry(
        "see https://github.com/pytorch/pytorch/pull/123/files"
    )
    assert (entry.repo, entry.number, entry.url) == (
        "pytorch/pytorch",
        123,
        "https://github.com/pytorch/pytorch/pull/123",
    )
    assert pr_monitor.parse_entry("not a pr") is None


def test_batch_labels_moves_waits_and_drops(files):
    monitor, ready, fake = files
    outcomes = pr_monitor.run(monitor, ready)
    assert {o.entry.number: o.kind for o in outcomes} == {
        101: "labeled",
        102: "waiting",
        103: "moved",
        104: "dropped",
    }
    assert _numbers(monitor) == {101, 102}
    assert monitor.read_text(encoding="utf-8").startswith("# batch one\n")
    assert ready.read_text(encoding="utf-8").split() == [
        "https://github.com/pytorch/pytorch/pull/103"
    ]
    edits = [call for call in fake.calls if call[:2] == ("pr", "edit")]
    assert edits == [
        (
            "pr",
            "edit",
            "101",
            "--repo",
            "pytorch/pytorch",
            "--add-label",
            pr_monitor.IN_PROGRESS,
        )
    ]


def test_dry_run_changes_nothing(files):
    monitor, ready, fake = files
    outcomes = pr_monitor.run(monitor, ready, dry_run=True)
    assert {o.kind for o in outcomes} == {"labeled", "waiting", "moved", "dropped"}
    assert _numbers(monitor) == {101, 102, 103, 104}
    assert not ready.exists()
    assert all(call[:2] != ("pr", "edit") for call in fake.calls)


def test_ready_file_deduplicates(files):
    monitor, ready, fake = files
    ready.write_text("https://github.com/pytorch/pytorch/pull/103/files\n")
    pr_monitor.run(monitor, ready)
    assert ready.read_text(encoding="utf-8").splitlines() == [
        "https://github.com/pytorch/pytorch/pull/103/files"
    ]


def test_failed_gh_call_keeps_the_pr(files):
    monitor, ready, fake = files
    fake.prs[102] = subprocess.CalledProcessError(1, ["gh"], stderr="rate limited")
    outcomes = pr_monitor.run(monitor, ready)
    assert {o.entry.number: o.kind for o in outcomes}[102] == "error"
    assert 102 in _numbers(monitor)
