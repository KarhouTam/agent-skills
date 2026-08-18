"""Codex adapter - the Codex harness implementation of BaseAdapter.

Codex exposes spawn_agent / followup_task / wait_agent instead of Claude
Code's Agent / SendMessage / CronCreate. This adapter owns the
Codex-specific task-spec shape, completion notes, poll-based cron policy,
and git preflight. Codex full-history forks inherit the parent model, so
spawn specs do not emit model overrides.
"""

from __future__ import annotations

from typing import Any

from agent.adapter import AgentTask
from agent.claude_code import ClaudeCodeAdapter, _coder_prompt
from state import FlowSignal


DEFAULT_TIMEOUT_MS = 1_800_000
TIMEOUTS = {
    "checker": 900_000,
}


class CodexAdapter(ClaudeCodeAdapter):
    harness_name = "codex"

    def _timeout_for(self, agent_name: str) -> int:
        return TIMEOUTS.get(agent_name, DEFAULT_TIMEOUT_MS)

    def build_respawn_task(
        self,
        message_task: AgentTask,
        file_path: str,
        workspace: str,
        rule_context: dict | None,
    ) -> AgentTask:
        """Rebuild the full coder role prompt plus the current assignment."""
        rc = rule_context or {}
        prompt = _coder_prompt(
            file_path=file_path,
            workspace=workspace,
            coder_id=rc.get("coder_id", "coder"),
            rule=rc.get("rule", ""),
            rule_description=rc.get("rule_description", ""),
            instructions=rc.get("instructions", ""),
            total_rules=rc.get("total_rules", 1),
        )
        prompt += "\n\n## Current Assignment\n\n" + message_task.prompt
        return AgentTask(
            phase="code",
            agent_name="coder",
            prompt=prompt,
        )

    # ── harness-specific emission ───────────────────────────────

    def _spawn_spec(self, task: AgentTask) -> dict[str, Any]:
        agent_name = task.agent_name
        return {
            "method": "spawn",
            "tool": "spawn_agent",
            "agent_name": agent_name,
            "task_name": agent_name,
            "message": task.prompt,
            "fork_turns": "all",
            "wait": {
                "tool": "wait_agent",
                "timeout_ms": self._timeout_for(agent_name),
            },
        }

    def task_to_spec(
        self,
        task: AgentTask,
        signal: FlowSignal,
        *,
        file_path: str = "",
        workspace: str = "",
        rule_context: dict | None = None,
    ) -> dict[str, Any]:
        method = "spawn"
        if signal in (FlowSignal.SEND_MESSAGE, FlowSignal.RELAY_FINDINGS):
            method = "send_message"

        if method == "spawn":
            return self._spawn_spec(task)

        target_id = getattr(task, "agent_id", "") or task.context.get(
            "send_message_to", task.agent_name
        )
        respawn = (
            self.build_respawn_task(task, file_path, workspace, rule_context)
            if rule_context
            else None
        )
        return {
            "method": "send_message",
            "tool": "followup_task",
            "agent_name": task.agent_name,
            "target": target_id,
            "message": task.prompt,
            "recovery": {
                "tool": "followup_task",
                "target": target_id,
                "note": "Retry followup_task after wait_agent if the target is busy.",
            },
            "fallback": {
                "method": "spawn",
                "tool": "spawn_agent",
                "agent_name": task.agent_name,
                "task_name": task.agent_name,
                "message": respawn.prompt if respawn else task.prompt,
                "fork_turns": "all",
                "note": "register the new agent_id in the result JSON",
            },
        }

    def ci_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        return self._spawn_spec(task)

    def ingest_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        return self._spawn_spec(task)

    def review_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        # Kept for the abstract contract; Codex review-queue uses inline
        # execution instead because spawn_agent mis-delivers task messages
        # (openai/codex#25458).
        return self._spawn_spec(task)

    def completion_note(
        self,
        kind: str,
        *,
        feed_file: str,
        feed_cmd: str,
        feed_as: str = "",
    ) -> str:
        if kind == "refactor":
            return (
                "1. For each task, call the named collaboration tool with the "
                "exact fields in the task spec (`task_name`, `message`, "
                "`fork_turns` for spawns; `target`, `message` for "
                "follow-ups). "
                "2. After a spawn or follow-up, wait for the agent's final "
                "message (use "
                "`wait_agent` with the task's `wait.timeout_ms`, or read the "
                "final answer when it is delivered). "
                "3. Extract the key result into JSON from it. "
                "4. If you spawned a NEW agent (method=spawn), include "
                '"agent_id" (the canonical task name returned by '
                "`spawn_agent`, e.g. `/root/analyst`) and "
                '"agent_name" (from the task spec) in the JSON. '
                "5. Write the result JSON to a file at "
                f"`{feed_file}` (use apply_patch or a shell heredoc), then run:\n"
                f"   {feed_cmd}"
            )
        if kind == "ci_debugger":
            return (
                "1. Spawn the debugger with the exact fields in the task spec, "
                "then wait for its final message (via `wait_agent` with "
                "`wait.timeout_ms`, or the delivered final answer). "
                "2. Read the debugger agent's final message and extract the key "
                "result into JSON (see ci-automation SKILL.md). "
                '3. Include "agent_id" (the canonical task name returned by '
                "`spawn_agent`) "
                'and "agent_name": "debugger" in the JSON. '
                "4. Write the result JSON to a file at "
                f"`{feed_file}` (use apply_patch or a shell heredoc), then run:\n"
                f"   {feed_cmd}\n"
                "Note: the debugger needs `git commit`/`git push`. If sandbox "
                "escalation denies them, it reports `fixes_pending`; push "
                "manually and re-run `--ci-check --resume`."
            )
        if kind == "ingest":
            return (
                "1. Read the agent's final message. "
                "2. Write the result JSON to a file at "
                f"`{feed_file}` (use apply_patch or a shell heredoc), then run:\n"
                f"   {feed_cmd}"
            )
        return ""

    def ci_wait_on_complete(self, file_path: str) -> dict[str, Any]:
        return {
            "action": "poll",
            "poll_interval_seconds": 420,
            "poll_command": (
                f"python orchestrator.py --harness codex {file_path} "
                "--ci-check --resume"
            ),
            "user_cron_line": (
                "*/7 * * * * cd <pytorch_root> && python <skill_dir>/orchestrator.py "
                f"--harness codex {file_path} --ci-check --resume "
                ">> agent_space/refactor/<file_stem>/ci_poll.log 2>&1"
            ),
            "note": (
                "No cron tool on Codex. (a) Keep this session alive: wait ~7 min "
                "(sleep 420), re-run poll_command; (b) install user_cron_line for "
                "durable monitoring-only polling (debugger steps run when you "
                "return to this skill); (c) optionally create a Codex Automation "
                "that asks this thread to run poll_command."
            ),
        }

    def ci_done_next_steps(self, pr_number: int | None) -> str:
        return (
            "All CI checks passed. Run `gh pr ready "
            f"{pr_number}`. Stop the poll loop; if you installed the user cron "
            "line, remove it - there are no cron jobs to delete."
        )

    def git_preflight(self) -> list[str]:
        # Codex has no Claude settings.json deny list; escalation is per-command.
        return []

    def git_preflight_error(self, blocked: list[str], file_path: str) -> str:
        return (
            "CI automation cannot push fixes: sandbox escalation denied "
            f"`{'` and `'.join(blocked)}`. The debugger needs `git commit` and "
            "`git push` to apply and push CI fixes. Allow them, then re-run "
            f"`python orchestrator.py --harness codex {file_path} --ci-check`."
        )
