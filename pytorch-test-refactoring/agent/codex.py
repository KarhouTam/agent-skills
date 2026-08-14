"""Codex adapter - the Codex harness implementation of BaseAdapter.

Codex exposes spawn_agent / send_input / resume_agent / wait_agent /
close_agent instead of Claude Code's Agent / SendMessage / CronCreate.
This adapter owns the Codex-specific task-spec shape, completion notes,
poll-based cron policy, per-role model selection, and git preflight.
"""

from __future__ import annotations

from typing import Any

from agent.adapter import AgentTask
from agent.claude_code import ClaudeCodeAdapter, _coder_prompt
from state import FlowSignal


ALLOWED_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}

DEFAULT_MODELS = {
    "analyst": "deepseek-v4-pro",
    "coder": "deepseek-v4-pro",
    "checker": "deepseek-v4-pro",
    "debugger": "deepseek-v4-pro",
    "feedback_triage": "deepseek-v4-flash",
    "feedback_analyst": "deepseek-v4-pro",
    "ruleset_editor": "deepseek-v4-pro",
}

DEFAULT_TIMEOUT_MS = 1_800_000
TIMEOUTS = {
    "checker": 900_000,
}


class CodexAdapter(ClaudeCodeAdapter):
    harness_name = "codex"

    def _model_for(self, agent_name: str) -> str:
        return DEFAULT_MODELS.get(agent_name, "deepseek-v4-pro")

    def _timeout_for(self, agent_name: str) -> int:
        return TIMEOUTS.get(agent_name, DEFAULT_TIMEOUT_MS)

    def _with_model(self, task: AgentTask) -> AgentTask:
        task.model = self._model_for(task.agent_name)
        return task

    # ── task builders: inherit from Claude, inject the per-role model ──

    def build_analyst_task(self, file_path, workspace, ref_dir):
        return self._with_model(
            super().build_analyst_task(file_path, workspace, ref_dir)
        )

    def build_coder_tasks(
        self,
        file_path,
        workspace,
        coder_tasks,
        strategy_assignments=None,
        first_spawn=False,
        total_rules=1,
    ):
        return [
            self._with_model(t)
            for t in super().build_coder_tasks(
                file_path,
                workspace,
                coder_tasks,
                strategy_assignments,
                first_spawn,
                total_rules,
            )
        ]

    def build_send_message(
        self,
        to,
        message_type,
        rule="",
        rule_description="",
        instructions="",
        agent_id="",
    ):
        return self._with_model(
            super().build_send_message(
                to, message_type, rule, rule_description, instructions, agent_id
            )
        )

    def build_checker_task(
        self,
        file_path,
        workspace,
        ref_dir,
        original_test_count,
        verification_summary,
        scope="file",
        rule_context=None,
    ):
        return self._with_model(
            super().build_checker_task(
                file_path,
                workspace,
                ref_dir,
                original_test_count,
                verification_summary,
                scope,
                rule_context,
            )
        )

    def build_debugger_task(self, file_path, workspace):
        return self._with_model(super().build_debugger_task(file_path, workspace))

    def build_feedback_triage_task(self, comments):
        return self._with_model(super().build_feedback_triage_task(comments))

    def build_feedback_analyst_task(self, comment_and_triage):
        return self._with_model(super().build_feedback_analyst_task(comment_and_triage))

    def build_fix_tasks(self, file_path, workspace, findings, agent_ids=None):
        return [
            self._with_model(t)
            for t in super().build_fix_tasks(file_path, workspace, findings, agent_ids)
        ]

    def build_ruleset_editor_task(self, prompt: str) -> AgentTask:
        return AgentTask(
            phase="apply",
            agent_name="ruleset_editor",
            prompt=prompt,
            model=self._model_for("ruleset_editor"),
        )

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
            model=self._model_for("coder"),
        )

    # ── harness-specific emission ───────────────────────────────

    def _spawn_spec(self, task: AgentTask) -> dict[str, Any]:
        return {
            "method": "spawn",
            "tool": "spawn_agent",
            "agent_name": task.agent_name,
            "model": task.model or self._model_for(task.agent_name),
            "timeout_ms": self._timeout_for(task.agent_name),
            "prompt": task.prompt,
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
            "tool": "send_input",
            "agent_name": task.agent_name,
            "send_to": target_id,
            "prompt": task.prompt,
            "recovery": {
                "tool": "resume_agent",
                "then": "retry send_input",
                "else": "spawn_agent with fallback.prompt",
            },
            "fallback": {
                "method": "spawn",
                "tool": "spawn_agent",
                "model": self._model_for(task.agent_name),
                "prompt": respawn.prompt if respawn else task.prompt,
                "note": "register the new agent_id in the result JSON",
            },
        }

    def ci_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        return self._spawn_spec(task)

    def ingest_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
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
                "1. Read the agent's final message. "
                "2. Extract the key result into JSON from it. "
                "3. If you spawned a NEW agent (method=spawn), include "
                '"agent_id" (from the spawn_agent result object) and '
                '"agent_name" (from the task spec) in the JSON. '
                "4. Write the result JSON to a file at "
                f"`{feed_file}` (heredoc), then run:\n"
                f"   {feed_cmd}"
            )
        if kind == "ci_debugger":
            return (
                "1. Read the debugger agent's final message. "
                "2. Extract the key result into JSON (see ci-automation SKILL.md). "
                '3. Include "agent_id" (from the spawn_agent result object) '
                'and "agent_name": "debugger" in the JSON. '
                "4. Write the result JSON to a file at "
                f"`{feed_file}` (heredoc), then run:\n"
                f"   {feed_cmd}\n"
                "Note: the debugger needs `git commit`/`git push`. If sandbox "
                "escalation denies them, it reports `fixes_pending`; push "
                "manually and re-run `--ci-check --resume`."
            )
        if kind == "ingest":
            return (
                "1. Read the agent's final message. "
                "2. Write the result JSON to a file at "
                f"`{feed_file}` (heredoc), then run:\n"
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
