"""Unit tests for the harness adapter contract (Claude Code vs Codex)."""

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.adapter import AgentTask
from agent.claude_code import ClaudeCodeAdapter
from agent.codex import CodexAdapter
from agent.registry import get_adapter
from state import FlowSignal


def _spawn_task(name="analyst"):
    return AgentTask(
        phase="analyze",
        agent_name=name,
        prompt="do the thing",
        run_in_background=True,
        mode="acceptEdits",
    )


def _send_task():
    return AgentTask(
        phase="code",
        agent_name="coder",
        prompt="Apply the next rule",
        context={"send_message_to": "agent_123"},
        agent_id="agent_123",
    )


def test_registry_maps_harnesses():
    assert isinstance(get_adapter("claude"), ClaudeCodeAdapter)
    assert isinstance(get_adapter("codex"), CodexAdapter)
    assert get_adapter(None).harness_name == "claude"


def test_registry_rejects_unknown():
    with pytest.raises(ValueError):
        get_adapter("bogus")


def test_claude_spawn_spec_shape():
    spec = ClaudeCodeAdapter().task_to_spec(_spawn_task(), FlowSignal.SPAWN_SINGLE)
    assert spec["method"] == "spawn"
    assert spec["agent_type"] == "general-purpose"
    assert spec["run_in_background"] is True
    assert spec["mode"] == "acceptEdits"
    assert "tool" not in spec
    assert "model" not in spec


def test_claude_send_message_keeps_bare_fallback():
    task = _send_task()
    spec = ClaudeCodeAdapter().task_to_spec(task, FlowSignal.SEND_MESSAGE)
    assert spec["method"] == "send_message"
    assert spec["send_to"] == "agent_123"
    assert spec["fallback"]["prompt"] == task.prompt
    assert spec["fallback"]["run_in_background"] is True
    assert spec["fallback"]["mode"] == "default"


def test_codex_spawn_spec_shape():
    spec = CodexAdapter().task_to_spec(_spawn_task(), FlowSignal.SPAWN_SINGLE)
    assert spec["method"] == "spawn"
    assert spec["tool"] == "spawn_agent"
    assert spec["model"] == "deepseek-v4-pro"
    assert spec["timeout_ms"] == 1_800_000
    assert "mode" not in spec
    assert "agent_type" not in spec
    assert "run_in_background" not in spec


def test_codex_send_message_rebuilds_role_prompt():
    rc = {
        "coder_id": "coder-1",
        "rule": "strategy_2",
        "rule_description": "Convert to device-agnostic",
        "instructions": "- enlarge @onlyCUDA",
        "total_rules": 1,
    }
    spec = CodexAdapter().task_to_spec(
        _send_task(),
        FlowSignal.SEND_MESSAGE,
        file_path="test/test_ops.py",
        workspace="agent_space/refactor/test_ops",
        rule_context=rc,
    )
    assert spec["tool"] == "send_input"
    assert spec["recovery"]["tool"] == "resume_agent"
    assert "Refactoring Standards" in spec["fallback"]["prompt"]
    assert "Apply the next rule" in spec["fallback"]["prompt"]
    assert spec["fallback"]["model"] == "deepseek-v4-pro"


def test_codex_model_injection():
    a = CodexAdapter()
    assert a.build_analyst_task("t.py", "ws", "ref").model == "deepseek-v4-pro"
    assert a.build_feedback_triage_task([]).model == "deepseek-v4-flash"
    assert a.build_debugger_task("t.py", "ws").model == "deepseek-v4-pro"
    comment = types.SimpleNamespace(
        comment_id=1,
        pr_number=1,
        author="x",
        source="inline_review",
        html_url="u",
        body="b",
    )
    assert (
        a.build_feedback_analyst_task({"comment": comment, "triage": {}}).model
        == "deepseek-v4-pro"
    )


def test_claude_model_empty():
    a = ClaudeCodeAdapter()
    assert a.build_analyst_task("t.py", "ws", "ref").model == ""


def test_completion_note_differs():
    claude = ClaudeCodeAdapter().completion_note(
        "refactor", feed_file="f.json", feed_cmd="python x"
    )
    codex = CodexAdapter().completion_note(
        "refactor", feed_file="f.json", feed_cmd="python x"
    )
    assert "Write tool" in claude
    assert "Bash allow rule" in claude
    assert "heredoc" in codex
    assert "Write tool" not in codex
    assert "Bash allow rule" not in codex


def test_ci_wait_policy():
    claude = ClaudeCodeAdapter().ci_wait_on_complete("test/test_ops.py")
    codex = CodexAdapter().ci_wait_on_complete("test/test_ops.py")
    assert claude["action"] == "CronCreate"
    assert codex["action"] == "poll"
    assert "--harness codex" in codex["poll_command"]


def test_git_preflight():
    assert CodexAdapter().git_preflight() == []
    assert isinstance(ClaudeCodeAdapter().git_preflight(), list)


def test_build_ruleset_editor_task():
    claude = ClaudeCodeAdapter().build_ruleset_editor_task("prompt")
    codex = CodexAdapter().build_ruleset_editor_task("prompt")
    assert claude.mode == "acceptEdits"
    assert claude.run_in_background is True
    assert claude.model == ""
    assert codex.model == "deepseek-v4-pro"


def test_ci_and_ingest_spec_shapes():
    codex = CodexAdapter()
    ci = codex.ci_task_to_spec(_spawn_task("debugger"))
    ing = codex.ingest_task_to_spec(_spawn_task("feedback_triage"))
    assert ci["tool"] == "spawn_agent"
    assert ci["model"] == "deepseek-v4-pro"
    assert ing["tool"] == "spawn_agent"
    assert ing["model"] == "deepseek-v4-flash"


def test_cmd_roundtrips_harness():
    from orchestrator import _cmd

    assert _cmd(get_adapter("claude"), "test.py", "--feed", "coder") == (
        "python orchestrator.py test.py --feed coder"
    )
    assert _cmd(get_adapter("codex"), "test.py", "--feed", "coder") == (
        "python orchestrator.py --harness codex test.py --feed coder"
    )
