"""Unit tests for the PR review queue sidecar (--review-queue)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.claude_code import ClaudeCodeAdapter
from agent.codex import CodexAdapter
from review_ops import ReviewOps
from scripts import review_queue
from state import PrReviewItem, PrReviewResult


def _write_pending(tmp_path: Path, urls: list[str]) -> Path:
    path = tmp_path / "agent_space" / "pr_needs_review.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(urls) + "\n", encoding="utf-8")
    return path


def _select_result(n: int = 3) -> review_queue.SelectResult:
    items = [
        PrReviewItem(
            url=f"https://github.com/pytorch/pytorch/pull/{1000 + i}",
            pr_number=1000 + i,
            title=f"PR {1000 + i}",
            author="u",
            state="OPEN",
            has_test_changes=True,
        )
        for i in range(n)
    ]
    return review_queue.SelectResult(review_queue=items)


def _write_result(workspace: Path, pr_number: int, success: bool = True) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    path = workspace / f"pr_{pr_number}_result.json"
    path.write_text(
        json.dumps(
            {
                "pr_number": pr_number,
                "title": f"PR {pr_number}",
                "author": "u",
                "state": "OPEN",
                "success": success,
                "all_clear": True,
                "reviewed_files": ["test/test_ops.py"],
                "findings": [],
                "summary": "ok" if success else "",
            }
        ),
        encoding="utf-8",
    )


def test_parse_pr_url():
    assert review_queue.parse_pr_url("https://github.com/pytorch/pytorch/pull/189387") == 189387
    assert review_queue.parse_pr_url("https://github.com/pytorch/pytorch/pulls/123") == 123
    assert review_queue.parse_pr_url("https://github.com/pytorch/pytorch/issues/1") is None


def test_load_save_pending_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    urls = [
        "https://github.com/pytorch/pytorch/pull/1",
        "https://github.com/pytorch/pytorch/pull/2",
        "https://github.com/pytorch/pytorch/pull/1",
        "   ",
    ]
    path = _write_pending(tmp_path, urls)
    assert review_queue.load_pending(path) == [
        "https://github.com/pytorch/pytorch/pull/1",
        "https://github.com/pytorch/pytorch/pull/2",
    ]
    review_queue.save_pending(["https://github.com/pytorch/pytorch/pull/2"], path)
    assert review_queue.load_pending(path) == [
        "https://github.com/pytorch/pytorch/pull/2"
    ]


def test_select_pending_partitions_and_limits(tmp_path, monkeypatch):
    def fake_gh_pr_info(number):
        if number == 1004:
            return None
        if number == 1002:
            paths = ["torch/nn/modules/foo.py"]
        else:
            paths = ["test/test_ops.py"]
        state = "OPEN" if number != 1003 else "MERGED"
        return {
            "number": number,
            "title": f"PR {number}",
            "author": f"user{number}",
            "state": state,
            "url": f"https://github.com/pytorch/pytorch/pull/{number}",
            "paths": paths,
        }

    monkeypatch.setattr(review_queue, "gh_pr_info", fake_gh_pr_info)
    path = _write_pending(
        tmp_path,
        [
            "https://github.com/pytorch/pytorch/pull/1004",
            "https://github.com/pytorch/pytorch/pull/1002",
            "https://github.com/pytorch/pytorch/pull/1003",
            "https://github.com/pytorch/pytorch/pull/1001",
            "https://github.com/pytorch/pytorch/pull/1005",
        ],
    )
    sel = review_queue.select_pending(limit=1, pending_path=path)
    assert [i.pr_number for i in sel.review_queue] == [1001]
    assert [i.pr_number for i in sel.not_applicable] == [1002, 1003]
    assert [i.pr_number for i in sel.failed] == [1004]


def test_render_comment():
    reviewed = [
        PrReviewResult(
            pr_number=189387,
            title="Bring modules support",
            author="can-gaa-hou",
            state="OPEN",
            all_clear=False,
            reviewed_files=["test/test_ops.py"],
            findings=[
                {
                    "severity": "Blocker",
                    "category": "classification",
                    "file": "test/test_ops.py",
                    "line_number": 42,
                    "description": "@onlyCUDA should be @onlyAccelerator",
                    "fix": "replace the decorator",
                }
            ],
            summary="Found 1 issue",
        )
    ]
    na = [
        PrReviewItem(
            url="https://github.com/pytorch/pytorch/pull/190296",
            pr_number=190296,
            title="Docs only",
            author="x",
            state="CLOSED",
            status="na",
            reason="merged/closed",
        )
    ]
    body = review_queue.render_comment("2026-08-17", reviewed, na)
    assert "## Daily Review — 2026-08-17" in body
    assert "<details>" in body
    assert "pytorch/pytorch#189387" in body
    assert "@can-gaa-hou" in body
    assert "结论：1 Blocker" in body
    assert "[Blocker] `test/test_ops.py:42`" in body
    assert "不适用（已 merged/closed）" in body
    assert "_本批 2 个：reviewed 1 · 不适用 1_" in body


def test_publish_batch_posts_archives_rewrites(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_run_gh(*args):
        calls.append(args)
        if args[0] == "issue":
            return "https://github.com/cosdt/pytorch-initial-pr-reviews/issues/1#issuecomment-123\n"
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(review_queue, "_run_gh", fake_run_gh)
    pending = _write_pending(
        tmp_path,
        [
            "https://github.com/pytorch/pytorch/pull/1",
            "https://github.com/pytorch/pytorch/pull/2",
            "https://github.com/pytorch/pytorch/pull/3",
        ],
    )
    workspace = tmp_path / "agent_space" / "pr_reviews"
    reviewed = [
        PrReviewResult(pr_number=1, title="T1", author="a", state="OPEN", all_clear=True)
    ]
    na = [
        PrReviewItem(
            url="https://github.com/pytorch/pytorch/pull/2",
            pr_number=2,
            title="T2",
            state="CLOSED",
            status="na",
            reason="merged/closed",
        )
    ]
    out = review_queue.publish_batch(workspace, reviewed, na, date_str="2026-08-17")
    assert out["comment_url"].startswith("https://github.com/cosdt")
    assert (workspace / "comment_2026-08-17.md").exists()
    archive = review_queue.load_archive(workspace / "pr_reviewed.json")
    assert len(archive["records"]) == 2
    assert review_queue.load_pending(pending) == [
        "https://github.com/pytorch/pytorch/pull/3"
    ]


def test_review_ops_inline_flow(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_queue, "select_pending", lambda limit=10: _select_result(3))
    published = {}

    def fake_publish(workspace, reviewed, not_applicable, date_str=None):
        published["reviewed"] = [r.pr_number for r in reviewed]
        return {"comment_url": "https://github.com/.../1#issuecomment-1", "comment_path": "x"}

    monkeypatch.setattr(review_queue, "publish_batch", fake_publish)
    ops = ReviewOps(adapter=CodexAdapter(), limit=10)
    assert ops.mode == "inline"
    ops.run()
    assert ops.state.phase == "review"
    assert ops.state.signal.value == "spawn_single"

    instruction = ops.get_inline_instruction("feed.json", "python x")
    assert "PR #1000" in instruction
    assert "Do NOT spawn" in instruction

    workspace = tmp_path / "agent_space" / "pr_reviews"
    for pr in (1000, 1001, 1002):
        _write_result(workspace, pr)
    ops.feed_reviewer_result({})
    ops.run()
    assert ops.state.phase == "done"
    assert sorted(ops.state.results) == ["1000", "1001", "1002"]
    assert sorted(published["reviewed"]) == [1000, 1001, 1002]
    ops.finalize()
    assert not (workspace / "flow_state.json").exists()


def test_review_ops_skips_missing_or_failed_results(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_queue, "select_pending", lambda limit=10: _select_result(2))
    monkeypatch.setattr(
        review_queue,
        "publish_batch",
        lambda *a, **k: {"comment_url": "u", "comment_path": "p"},
    )
    workspace = tmp_path / "agent_space" / "pr_reviews"
    _write_result(workspace, 1000, success=True)
    _write_result(workspace, 1001, success=False)
    ops = ReviewOps(adapter=CodexAdapter(), limit=10)
    ops.run()
    ops.feed_reviewer_result({})
    ops.run()
    assert ops.state.results == {
        "1000": PrReviewResult(
            pr_number=1000,
            title="PR 1000",
            author="u",
            state="OPEN",
            success=True,
            all_clear=True,
            reviewed_files=["test/test_ops.py"],
            findings=[],
            summary="ok",
        )
    }


def test_review_ops_subagent_waves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(review_queue, "select_pending", lambda limit=10: _select_result(6))
    published = {}

    def fake_publish(workspace, reviewed, not_applicable, date_str=None):
        published["reviewed"] = [r.pr_number for r in reviewed]
        return {"comment_url": "https://github.com/.../1#issuecomment-1", "comment_path": "x"}

    monkeypatch.setattr(review_queue, "publish_batch", fake_publish)
    ops = ReviewOps(adapter=ClaudeCodeAdapter(), limit=10)
    assert ops.mode == "subagents"
    ops.run()
    assert ops.state.phase == "review"
    assert len(ops.state.in_flight) == 4
    assert ops.state.signal.value == "spawn_parallel"
    tasks = ops.get_pending_tasks()
    assert [t.agent_name for t in tasks] == [
        "reviewer_pr_1000",
        "reviewer_pr_1001",
        "reviewer_pr_1002",
        "reviewer_pr_1003",
    ]

    for task in tasks:
        pr = task.context["pr_number"]
        ops.feed_reviewer_result({"pr_number": pr, "success": True, "all_clear": True})
        ops.run()
    assert len(ops.state.in_flight) == 2
    assert ops.state.signal.value == "spawn_parallel"

    for task in ops.get_pending_tasks():
        pr = task.context["pr_number"]
        ops.feed_reviewer_result({"pr_number": pr, "success": True, "all_clear": True})
        ops.run()
    assert ops.state.phase == "done"
    assert sorted(published["reviewed"]) == [1000, 1001, 1002, 1003, 1004, 1005]
    ops.finalize()
    assert not (tmp_path / "agent_space" / "pr_reviews" / "flow_state.json").exists()
