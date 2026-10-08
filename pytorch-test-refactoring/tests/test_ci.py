"""Unit tests for deterministic CI helpers (scripts/ci.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import ci
from state import CIBotHints, CICheckRun


def _run(conclusion: str, status: str = "completed") -> CICheckRun:
    return CICheckRun(name="x", status=status, conclusion=conclusion)


def test_unknown_completed_conclusion_is_a_failure():
    """A workflow that failed to start must not be reported as green."""
    for conclusion in ("startup_failure", "action_required", "stale", ""):
        verdict, failures = ci.classify_ci_state(
            [_run(conclusion)], CIBotHints()
        )
        assert verdict == "failures", conclusion
        assert [f.check_name for f in failures] == ["x"]


def test_neutral_and_skipped_are_green():
    for conclusion in ("success", "neutral", "skipped"):
        verdict, _ = ci.classify_ci_state([_run(conclusion)], CIBotHints())
        assert verdict == "all_pass", conclusion


def test_parse_bot_comment_trunk_patterns():
    hints = ci.parse_bot_comment('check "foo" is broken on trunk')
    assert hints.trunk_broken == ["foo"]

    # The bare phrase has no check name: it must not capture the verb or crash
    # on a missing group.
    for raw in ("This test is broken on trunk", "test  broken on trunk"):
        assert ci.parse_bot_comment(raw).trunk_broken == []


def test_get_bot_comment_reads_newest_page(monkeypatch):
    payload = [
        [{"user": {"login": "pytorch-bot"}, "body": "old"}],
        [
            {"user": {"login": "someone"}, "body": "n/a"},
            {"user": {"login": "pytorch-bot"}, "body": "new"},
        ],
    ]
    seen = {}

    def fake_run_gh(*args, **kwargs):
        seen["args"] = args
        return json.dumps(payload)

    monkeypatch.setattr(ci, "_run_gh", fake_run_gh)
    assert ci.get_bot_comment(1) == "new"
    assert "--paginate" in seen["args"]
    assert "--slurp" in seen["args"]
