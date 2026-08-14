import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state import FeedbackComment, FeedbackFinding, IngestState


def test_ingest_state_round_trip():
    state = IngestState()
    state.pr_timestamps["185881"] = "2026-08-01T00:00:00Z"
    state.processed_comment_ids.add(5274808164)
    state.findings.append(
        FeedbackFinding(
            id="192760-5274808164",
            comment_id=5274808164,
            pr_number=192760,
            author="claude[bot]",
            html_url="https://github.com/pytorch/pytorch/pull/192760#issuecomment-5274808164",
            tier="Major",
            summary="test renames break compiled_autograd_skips keys",
            target_layers=["coder.md", "verify.py"],
            proposed_edits=[
                {
                    "layer": "coder.md",
                    "intent": "warn about class-rename breaking skip-key lookups",
                }
            ],
        )
    )
    reloaded = IngestState.model_validate_json(state.model_dump_json())
    assert reloaded.pr_timestamps["185881"] == "2026-08-01T00:00:00Z"
    assert 5274808164 in reloaded.processed_comment_ids
    assert reloaded.findings[0].tier == "Major"


def test_feedback_comment_defaults():
    c = FeedbackComment(
        comment_id=1,
        pr_number=192760,
        author="x",
        body="b",
        html_url="u",
        created_at="2026-08-13T00:00:00Z",
    )
    assert c.source == "inline_review"
    assert c.is_reply is False


import json
import scripts.ingest as ingest


def _fake_gh_inline(*args, **kwargs):
    # capture the endpoint to dispatch; tests assert on the returned payload
    return json.dumps(
        [
            {
                "id": 1001,
                "body": "use @onlyAccelerator here",
                "html_url": "u/1001",
                "created_at": "2026-08-12T00:00:00Z",
                "user": {"login": "can-gaa-hou"},
                "in_reply_to_id": 0,
            },
            {
                "id": 1002,
                "body": "this assert should be assertEqual",
                "html_url": "u/1002",
                "created_at": "2026-08-12T00:00:00Z",
                "user": {"login": "fffrog"},
                "in_reply_to_id": 1001,  # reply -> replied thread
            },
            {
                "id": 1003,
                "body": "lone comment, no replies",
                "html_url": "u/1003",
                "created_at": "2026-08-12T00:00:00Z",
                "user": {"login": "albanD"},
                "in_reply_to_id": 0,  # no reply -> dropped
            },
        ]
    )


def _fake_gh_issue(*args, **kwargs):
    return json.dumps(
        [
            {
                "id": 2001,
                "body": "Claude review summary",
                "html_url": "u/2001",
                "created_at": "2026-08-12T00:00:00Z",
                "user": {"login": "claude[bot]"},
            },
            {
                "id": 2002,
                "body": "human issue comment",
                "html_url": "u/2002",
                "created_at": "2026-08-12T00:00:00Z",
                "user": {"login": "someone"},
            },
        ]
    )


def test_reply_filter_keeps_only_replied_threads(monkeypatch):
    monkeypatch.setattr(ingest, "_run_gh", _fake_gh_inline)
    comments = ingest.fetch_inline_review_comments(192760)
    kept = ingest.to_feedback_comments(
        comments, pr_number=192760, source="inline_review"
    )
    ids = {c.comment_id for c in kept}
    assert 1001 in ids and 1002 in ids  # 1001 has a reply; 1002 is a reply
    assert 1003 not in ids  # lone comment dropped


def test_claude_summary_filter(monkeypatch):
    monkeypatch.setattr(ingest, "_run_gh", _fake_gh_issue)
    comments = ingest.fetch_claude_summaries(192760)
    kept = ingest.to_feedback_comments(
        comments, pr_number=192760, source="claude_summary"
    )
    assert [c.comment_id for c in kept] == [2001]
    assert kept[0].author == "claude[bot]"


from state import IngestState


def test_filter_new_respects_timestamp_and_processed(tmp_path, monkeypatch):
    # two comments; one newer than the PR timestamp, one already processed
    c_new = FeedbackComment(
        comment_id=1,
        pr_number=185881,
        author="x",
        body="new",
        html_url="u",
        created_at="2026-08-13T00:00:00Z",
    )
    c_old = FeedbackComment(
        comment_id=2,
        pr_number=185881,
        author="x",
        body="old",
        html_url="u",
        created_at="2026-07-01T00:00:00Z",
    )
    c_done = FeedbackComment(
        comment_id=3,
        pr_number=185881,
        author="x",
        body="done",
        html_url="u",
        created_at="2026-08-13T00:00:00Z",
    )
    state = IngestState(pr_timestamps={"185881": "2026-08-01T00:00:00Z"})
    state.processed_comment_ids.add(3)
    kept = ingest.filter_new([c_new, c_old, c_done], state)
    assert [c.comment_id for c in kept] == [1]


def test_save_and_load_state(tmp_path):
    state = IngestState(pr_timestamps={"185881": "2026-08-01T00:00:00Z"})
    ingest.save_state(state, tmp_path)
    loaded = ingest.load_state(tmp_path)
    assert loaded.pr_timestamps == state.pr_timestamps


def test_load_state_missing_file(tmp_path):
    state = ingest.load_state(tmp_path)
    assert state.pr_timestamps == {}


def test_harvest_defers_processed_marking(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "get_ingest_workspace", lambda: tmp_path)
    monkeypatch.setattr(
        ingest,
        "discover_merged_prs",
        lambda *a, **k: [{"number": 185881, "title": "[Test] x", "closed_at": ""}],
    )
    monkeypatch.setattr(
        ingest,
        "fetch_inline_review_comments",
        lambda pr: [
            {
                "id": 1,
                "body": "b",
                "html_url": "u",
                "created_at": "2026-08-13T00:00:00Z",
                "user": {"login": "x"},
                "in_reply_to_id": 0,
            },
            {
                "id": 2,
                "body": "r",
                "html_url": "u",
                "created_at": "2026-08-13T00:00:00Z",
                "user": {"login": "y"},
                "in_reply_to_id": 1,
            },
        ],
    )
    monkeypatch.setattr(ingest, "fetch_claude_summaries", lambda pr: [])
    fresh, state = ingest.harvest()
    assert [c.comment_id for c in fresh] == [1, 2]
    # deferred: nothing is marked processed until finalize_harvest
    assert 1 not in state.processed_comment_ids
    assert 2 not in state.processed_comment_ids


def test_finalize_harvest_marks_processed(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "get_ingest_workspace", lambda: tmp_path)
    c = FeedbackComment(
        comment_id=42,
        pr_number=185881,
        author="x",
        body="b",
        html_url="u",
        created_at="2026-08-13T00:00:00Z",
    )
    state = ingest.finalize_harvest([c])
    assert 42 in state.processed_comment_ids
    assert state.pr_timestamps["185881"]


import ingest_ops as ingest_ops_module
from ingest_ops import IngestOps


def test_ingest_ops_done_when_no_fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(ingest, "harvest", lambda *a, **k: ([], IngestState()))
    monkeypatch.setattr(ingest_ops_module, "get_ingest_workspace", lambda: tmp_path)
    ops = IngestOps()
    state = ops.run()
    assert state.phase == "done"
    assert state.signal.value == "done"


def test_ingest_ops_triage_to_draft_flow(monkeypatch, tmp_path):
    c = FeedbackComment(
        comment_id=1,
        pr_number=185881,
        author="x",
        body="b",
        html_url="u",
        created_at="2026-08-13T00:00:00Z",
    )
    monkeypatch.setattr(ingest, "harvest", lambda *a, **k: ([c], IngestState()))
    monkeypatch.setattr(ingest_ops_module, "get_ingest_workspace", lambda: tmp_path)

    ops = IngestOps()
    st = ops.run()
    assert st.phase == "triage"
    assert st.signal.value == "spawn_single"

    ops.feed_triage_result(
        {
            "decisions": [
                {
                    "comment_id": 1,
                    "relevant": True,
                    "already_fixed": False,
                    "target_layers": ["coder.md"],
                    "tier": "Major",
                    "summary": "s",
                }
            ]
        }
    )
    assert ops.state.phase == "draft"
    assert ops.state.draft_queue == [1]

    tasks = ops.get_pending_tasks()
    assert tasks and tasks[0].agent_name == "feedback_analyst"

    ops.feed_draft_result(
        {
            "finding": {
                "id": "185881-1",
                "comment_id": 1,
                "pr_number": 185881,
                "author": "x",
                "html_url": "u",
                "tier": "Major",
                "summary": "s",
                "target_layers": ["coder.md"],
                "proposed_edits": [{"layer": "coder.md", "intent": "warn"}],
            }
        }
    )
    assert ops.state.phase == "done"
    assert len(ops.state.pending_findings) == 1


def test_ingest_ops_resumes_from_persisted_state(monkeypatch, tmp_path):
    # First process: harvest finds one comment, triage advances to draft.
    c = FeedbackComment(
        comment_id=7,
        pr_number=185881,
        author="x",
        body="b",
        html_url="u",
        created_at="2026-08-13T00:00:00Z",
    )
    monkeypatch.setattr(ingest, "harvest", lambda *a, **k: ([c], IngestState()))
    monkeypatch.setattr(ingest_ops_module, "get_ingest_workspace", lambda: tmp_path)
    ops = IngestOps()
    ops.run()
    ops.feed_triage_result(
        {
            "decisions": [
                {
                    "comment_id": 7,
                    "relevant": True,
                    "already_fixed": False,
                    "target_layers": ["coder.md"],
                    "tier": "Major",
                    "summary": "s",
                }
            ]
        }
    )

    # Fresh cron process: harvest now returns nothing, but the new IngestOps
    # must RESUME at draft from persisted flow_state.json, not re-harvest.
    monkeypatch.setattr(ingest, "harvest", lambda *a, **k: ([], IngestState()))
    ops2 = IngestOps()
    st2 = ops2.run()
    assert st2.phase == "draft"
    assert st2.draft_queue == [7]
    tasks2 = ops2.get_pending_tasks()
    assert tasks2 and tasks2[0].agent_name == "feedback_analyst"


def test_write_findings_md_contains_status_checkbox(tmp_path):
    f = FeedbackFinding(
        id="192760-1001",
        comment_id=1001,
        pr_number=192760,
        author="can-gaa-hou",
        html_url="u/1001",
        tier="Major",
        summary="class rename breaks skips",
        target_layers=["coder.md"],
        proposed_edits=[{"layer": "coder.md", "intent": "warn"}],
    )
    path = ingest.write_findings_md([f], tmp_path)
    text = path.read_text()
    assert "[ ] Approved" in text
    assert "[ ] Rejected" in text
    assert "192760-1001" in text


def test_append_changelog_writes_entry(tmp_path):
    f = FeedbackFinding(
        id="192760-1001",
        comment_id=1001,
        pr_number=192760,
        author="can-gaa-hou",
        html_url="u/1001",
        tier="Major",
        summary="class rename breaks skips",
        target_layers=["coder.md"],
        proposed_edits=[],
    )
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text("# Changelog\n\nexisting\n")
    ingest.append_changelog([f], changelog)
    text = changelog.read_text()
    assert "192760-1001" in text
    assert "class rename breaks skips" in text
