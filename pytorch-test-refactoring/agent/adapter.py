"""Abstract base for AI adapters and the AgentTask model."""

from abc import ABC, abstractmethod

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


class BaseAdapter(ABC):
    """Abstract base for AI adapters.

    ClaudeCodeAdapter returns AgentTask objects; Claude spawns agents
    via the Agent tool. Only used in Claude Code mode.
    """

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

    def build_fix_tasks(
        self,
        file_path: str,
        workspace: str,
        findings: list,
        agent_ids: dict[str, str] | None = None,
    ) -> list[AgentTask]:
        return []
