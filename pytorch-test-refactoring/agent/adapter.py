"""Abstract base for AI adapters and the AgentTask model.

Each adapter encapsulates one agent harness (Claude Code, Codex, ...).
The deterministic core only builds AgentTask objects through the adapter;
everything that differs between harnesses - the JSON task-spec shape, the
completion notes, the cron policy, and the git preflight - is owned by the
adapter so that adding a harness is one new module plus one registry entry.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class AgentTask(BaseModel):
    """A task to be executed by a spawned agent.

    For SPAWN_SINGLE tasks, agent_id is empty initially and gets
    populated after the agent is spawned (via feed_agent_spawned).

    For SEND_MESSAGE tasks, agent_id holds the ID of the previously
    spawned agent so the orchestrator can resume it via SendMessage.
    """

    phase: str
    agent_name: str
    agent_type: str = "general-purpose"
    prompt: str
    run_in_background: bool = False
    mode: str = "default"  # Permission mode: "dontAsk" | "acceptEdits" | "bypassPermissions" | "default"
    context: dict = {}
    agent_id: str = ""
    model: str = ""  # Harness model override (e.g. Codex); empty means inherit


class BaseAdapter(ABC):
    """Abstract base for AI adapters.

    Concrete adapters build AgentTask objects and own all harness-specific
    emission. The orchestrator and state machines never branch on the
    harness name; they delegate to the adapter.
    """

    harness_name: str = ""

    @abstractmethod
    def build_analyst_task(
        self, file_path: str, workspace: str, ref_dir: str
    ) -> AgentTask: ...

    @abstractmethod
    def build_coder_tasks(
        self,
        file_path: str,
        workspace: str,
        coder_tasks: list,
        strategy_assignments: dict | None = None,
    ) -> list[AgentTask]: ...

    @abstractmethod
    def build_send_message(
        self,
        to: str,
        message_type: str,
        rule: str = "",
        rule_description: str = "",
        instructions: str = "",
        agent_id: str = "",
    ) -> "AgentTask": ...

    @abstractmethod
    def build_checker_task(
        self,
        file_path: str,
        workspace: str,
        ref_dir: str,
        original_test_count: int,
        verification_summary: str,
        scope: str = "file",
        rule_context: dict | None = None,
    ) -> AgentTask: ...

    @abstractmethod
    def build_debugger_task(
        self,
        file_path: str,
        workspace: str,
    ) -> AgentTask: ...

    @abstractmethod
    def build_feedback_triage_task(self, comments: list) -> AgentTask: ...

    @abstractmethod
    def build_feedback_analyst_task(self, comment_and_triage: dict) -> AgentTask: ...

    def build_fix_tasks(
        self,
        file_path: str,
        workspace: str,
        findings: list,
        agent_ids: dict[str, str] | None = None,
    ) -> list[AgentTask]:
        return []

    @abstractmethod
    def build_ruleset_editor_task(self, prompt: str) -> AgentTask:
        """Build the task for the ruleset_editor agent (--apply-ingest)."""

    # ── Harness-specific emission ────────────────────────────────

    @abstractmethod
    def task_to_spec(
        self,
        task: AgentTask,
        signal: Any,
        *,
        file_path: str = "",
        workspace: str = "",
        rule_context: dict | None = None,
    ) -> dict[str, Any]:
        """Convert a refactor-flow AgentTask into a harness-executable spec."""

    @abstractmethod
    def ci_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        """Convert a CI debugger AgentTask into a harness-executable spec."""

    @abstractmethod
    def ingest_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        """Convert an ingest/ruleset-editor AgentTask into a spec."""

    @abstractmethod
    def completion_note(
        self,
        kind: str,
        *,
        feed_file: str,
        feed_cmd: str,
        feed_as: str = "",
    ) -> str:
        """Return the harness-specific 'how to feed the result back' note.

        kind is one of "refactor", "ci_debugger", or "ingest".
        """

    @abstractmethod
    def ci_wait_on_complete(self, file_path: str) -> dict[str, Any]:
        """Return the on_complete payload emitted while CI is still running."""

    @abstractmethod
    def ci_done_next_steps(self, pr_number: int | None) -> str:
        """Return the next-steps text emitted when CI is green."""

    @abstractmethod
    def git_preflight(self) -> list[str]:
        """Return git operations the harness will block (empty = none)."""

    @abstractmethod
    def git_preflight_error(self, blocked: list[str], file_path: str) -> str:
        """Return the actionable error emitted when git_preflight is non-empty."""
