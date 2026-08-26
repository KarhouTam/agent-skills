"""Slimmed AgentTask model and the Harness protocol.

A harness encapsulates ONE agent runtime (Claude Code, Codex, ...). It owns
only what genuinely differs between harnesses: the task-spec shape emitted
to the host (spawn/followup), the completion-note wording, the CI wait
policy, and the git preflight. Everything harness-agnostic — prompt and
AgentTask construction — lives in ``agent/tasks.py`` and is shared by all
harnesses.

Adding a harness is one module in ``agent/harnesses/`` plus one entry in the
``HARNESSES`` registry (``agent/harnesses/__init__.py``). No orchestration or
state-machine code branches on a harness name.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel


class AgentTask(BaseModel):
    """A task to be executed by an agent, independent of any harness.

    Holds only task content. Harness-specific emission details (permission
    mode, background flag, agent type, tool names) are decided by the harness
    when it renders the task into a spec, not stored on the task.
    """

    phase: str
    agent_name: str
    prompt: str
    context: dict = {}  # {"send_message_to": id} for follow-ups; {"pr_number": n} for reviewers


class Harness(ABC):
    """Protocol a harness implements.

    Concrete harnesses turn an AgentTask into a host-executable spec and own
    the host-specific wording/policy. The orchestrator and state machines
    never branch on a harness name; they branch only on capabilities.
    """

    name: str = ""
    supports_delegated_agents: bool = True

    # ── emission ──────────────────────────────────────────────

    @abstractmethod
    def spawn(self, task: AgentTask) -> dict[str, Any]:
        """Render a spec for spawning a fresh agent."""

    @abstractmethod
    def followup(
        self,
        task: AgentTask,
        *,
        file_path: str = "",
        workspace: str = "",
        rule_context: dict | None = None,
    ) -> dict[str, Any]:
        """Render a spec for resuming an existing agent.

        ``file_path``/``workspace``/``rule_context`` let a harness rebuild a
        full role prompt for its fallback re-spawn; harnesses that keep the
        bare follow-up prompt ignore them.
        """

    # ── policy ────────────────────────────────────────────────

    @abstractmethod
    def note(
        self,
        kind: str,
        *,
        feed_file: str,
        feed_cmd: str,
        feed_as: str = "",
    ) -> str:
        """Return the harness-specific 'how to feed the result back' note.

        ``kind`` is one of "refactor", "ci_debugger", or "ingest".
        """

    @abstractmethod
    def wait_on_complete(self, file_path: str) -> dict[str, Any]:
        """Return the on_complete payload emitted while CI is still running."""

    @abstractmethod
    def done_next_steps(self, pr_number: int | None) -> str:
        """Return the next-steps text emitted when CI is green."""

    def git_preflight(self) -> list[str]:
        """Return git operations the harness will block (empty = none)."""
        return []

    def git_preflight_error(self, blocked: list[str], file_path: str) -> str:
        """Return the actionable error emitted when git_preflight is non-empty."""
        return ""
