"""Unit tests for the harness protocol (Claude Code vs Codex)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from agent.harness import AgentTask
from agent.harnesses import ClaudeHarness, CodexHarness, get_harness
from agent.tasks import build_analyst_task, build_ruleset_editor_task


def _spawn_task(name="analyst"):
    return AgentTask(phase="analyze", agent_name=name, prompt="do the thing")


def _send_task():
    return AgentTask(
        phase="code",
        agent_name="coder",
        prompt="Apply the next rule",
        context={"send_message_to": "agent_123"},
    )


def test_registry_maps_harnesses():
    assert isinstance(get_harness("claude"), ClaudeHarness)
    assert isinstance(get_harness("codex"), CodexHarness)
    assert get_harness(None).name == "claude"


def test_registry_rejects_unknown():
    with pytest.raises(ValueError):
        get_harness("bogus")


def test_supports_delegated_agents():
    assert ClaudeHarness().supports_delegated_agents is True
    assert CodexHarness().supports_delegated_agents is False


def test_agent_task_is_slim():
    task = build_analyst_task("t.py", "ws", "ref")
    assert not hasattr(task, "mode")
    assert not hasattr(task, "run_in_background")
    assert not hasattr(task, "agent_type")
    assert not hasattr(task, "model")
    assert not hasattr(task, "agent_id")


def test_claude_spawn_spec_shape():
    spec = ClaudeHarness().spawn(_spawn_task())
    assert spec["method"] == "spawn"
    assert spec["agent_type"] == "general-purpose"
    assert spec["run_in_background"] is True
    assert spec["mode"] == "acceptEdits"
    assert "tool" not in spec
    assert "model" not in spec


def test_claude_followup_keeps_bare_fallback():
    task = _send_task()
    spec = ClaudeHarness().followup(task)
    assert spec["method"] == "send_message"
    assert spec["send_to"] == "agent_123"
    assert spec["fallback"]["prompt"] == task.prompt
    assert spec["fallback"]["run_in_background"] is True
    assert spec["fallback"]["mode"] == "default"


def test_codex_spawn_spec_shape():
    spec = CodexHarness().spawn(_spawn_task())
    assert spec["method"] == "spawn"
    assert spec["tool"] == "spawn_agent"
    assert spec["task_name"] == "analyst"
    assert spec["message"] == "do the thing"
    assert "model" not in spec
    assert spec["fork_turns"] == "all"
    assert spec["wait"]["tool"] == "wait_agent"
    assert spec["wait"]["timeout_ms"] == 1_800_000
    assert "mode" not in spec
    assert "agent_type" not in spec
    assert "run_in_background" not in spec


def test_codex_followup_rebuilds_role_prompt():
    rc = {
        "coder_id": "coder-1",
        "rule": "strategy_2",
        "rule_description": "Convert to device-agnostic",
        "instructions": "- enlarge @onlyCUDA",
        "total_rules": 1,
    }
    spec = CodexHarness().followup(
        _send_task(),
        file_path="test/test_ops.py",
        workspace="agent_space/refactor/test_ops",
        rule_context=rc,
    )
    assert spec["tool"] == "followup_task"
    assert spec["target"] == "agent_123"
    assert spec["message"] == "Apply the next rule"
    assert spec["recovery"]["tool"] == "followup_task"
    assert spec["fallback"]["tool"] == "spawn_agent"
    assert spec["fallback"]["task_name"] == "coder"
    assert spec["fallback"]["message"].startswith(
        "You are the CODER for the test_ops refactoring team."
    )
    assert "Refactoring Standards" in spec["fallback"]["message"]
    assert "Apply the next rule" in spec["fallback"]["message"]
    assert "model" not in spec["fallback"]
    assert spec["fallback"]["fork_turns"] == "all"


def test_note_differs():
    claude = ClaudeHarness().note("refactor", feed_file="f.json", feed_cmd="python x")
    codex = CodexHarness().note("refactor", feed_file="f.json", feed_cmd="python x")
    assert "Write tool" in claude
    assert "Bash allow rule" in claude
    assert "heredoc" in codex
    assert "Write tool" not in codex
    assert "Bash allow rule" not in codex


def test_ci_wait_policy():
    claude = ClaudeHarness().wait_on_complete("test/test_ops.py")
    codex = CodexHarness().wait_on_complete("test/test_ops.py")
    assert claude["action"] == "CronCreate"
    assert codex["action"] == "poll"
    assert "--harness codex" in codex["poll_command"]


def test_git_preflight():
    assert CodexHarness().git_preflight() == []
    assert isinstance(ClaudeHarness().git_preflight(), list)


def test_build_ruleset_editor_task():
    task = build_ruleset_editor_task("prompt")
    assert task.agent_name == "ruleset_editor"
    assert task.prompt == "prompt"
    assert not hasattr(task, "mode")
    # The Claude spawn spec applies the editor's permission mode.
    assert ClaudeHarness().spawn(task)["mode"] == "acceptEdits"


def test_ci_and_ingest_spawn_shapes():
    codex = CodexHarness()
    ci = codex.spawn(_spawn_task("debugger"))
    ing = codex.spawn(_spawn_task("feedback_triage"))
    assert ci["tool"] == "spawn_agent"
    assert "model" not in ci
    assert ci["fork_turns"] == "all"
    assert ing["tool"] == "spawn_agent"
    assert "model" not in ing
    assert ing["fork_turns"] == "all"


def test_cmd_roundtrips_harness():
    from orchestrator import _cmd

    assert _cmd(get_harness("claude"), "test.py", "--feed", "coder") == (
        "python orchestrator.py --harness claude test.py --feed coder"
    )
    assert _cmd(get_harness("codex"), "test.py", "--feed", "coder") == (
        "python orchestrator.py --harness codex test.py --feed coder"
    )


def test_harness_choices_derive_from_registry(monkeypatch):
    import orchestrator

    monkeypatch.setitem(orchestrator.HARNESSES, "foo", ClaudeHarness)
    monkeypatch.setattr(
        orchestrator.sys, "argv", ["orchestrator.py", "test.py", "--harness", "foo"]
    )
    assert orchestrator._parse_args().harness == "foo"
