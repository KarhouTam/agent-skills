"""Claude Code harness — emission and policy only.

Prompt/AgentTask construction lives in ``agent/tasks.py`` and is shared with
every harness. This class owns only what is Claude-specific: the spec shape
(Agent/SendMessage tools), the per-role permission mode, the completion-note
wording, the CronCreate wait policy, and the settings.json git preflight.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.harness import AgentTask, Harness


_ROLE_MODES = {
    "analyst": "acceptEdits",
    "coder": "acceptEdits",
    "checker": "acceptEdits",
    "debugger": "bypassPermissions",
    "ruleset_editor": "acceptEdits",
}


class ClaudeHarness(Harness):
    name = "claude"
    supports_delegated_agents = True

    @staticmethod
    def _mode_for(agent_name: str) -> str:
        return _ROLE_MODES.get(agent_name, "default")

    # ── emission ──────────────────────────────────────────────

    def spawn(self, task: AgentTask) -> dict[str, Any]:
        return {
            "method": "spawn",
            "agent_name": task.agent_name,
            "agent_type": "general-purpose",
            "run_in_background": True,
            "mode": self._mode_for(task.agent_name),
            "prompt": task.prompt,
        }

    def followup(
        self,
        task: AgentTask,
        *,
        file_path: str = "",
        workspace: str = "",
        rule_context: dict | None = None,
    ) -> dict[str, Any]:
        target = task.context.get("send_message_to", task.agent_name)
        return {
            "method": "send_message",
            "agent_name": task.agent_name,
            "send_to": target,
            "prompt": task.prompt,
            "fallback": {
                "method": "spawn",
                "agent_name": task.agent_name,
                "agent_type": "general-purpose",
                "run_in_background": True,
                "mode": "default",
                "prompt": task.prompt,
                "note": (
                    "If you use this fallback, include "
                    '"agent_id" and "agent_name" in the result JSON '
                    "so the orchestrator can register the new agent ID."
                ),
            },
        }

    # ── policy ────────────────────────────────────────────────

    def note(
        self,
        kind: str,
        *,
        feed_file: str,
        feed_cmd: str,
        feed_as: str = "",
    ) -> str:
        if kind == "refactor":
            return (
                "1. Read the agent's output. "
                "2. Extract key result into JSON (see formats below). "
                "3. If you spawned a NEW agent (method=spawn), include "
                '"agent_id" (from the Agent tool result) and '
                '"agent_name" (from the task spec) in the JSON. '
                "4. Save the result JSON to a file at "
                f"`{feed_file}` (use the Write tool), then run:\n"
                f"   {feed_cmd}\n"
                "   (Feeding via --feed-file, not stdin, so the command "
                "matches a Bash allow rule in Auto/restricted modes.)"
            )
        if kind == "ci_debugger":
            return (
                "1. Read the debugger agent's output.\\n"
                "2. Extract key result into JSON (see ci-automation SKILL.md).\\n"
                '3. Include "agent_id" (from the Agent tool result) and '
                '"agent_name": "debugger" in the JSON.\\n'
                "4. Save the result JSON to a file at "
                f"`{feed_file}` (use the Write tool), then run:\\n"
                f"   {feed_cmd}\\n"
                "   (Feed via --feed-file, not stdin, so the command "
                "matches a Bash allow rule in Auto/restricted modes.)"
            )
        if kind == "ingest":
            return (
                "1. Read the agent's output.\\n"
                "2. Save the result JSON to a file at "
                f"`{feed_file}` (Write tool), then run:\\n"
                f"   {feed_cmd}"
            )
        return ""

    def wait_on_complete(self, file_path: str) -> dict[str, Any]:
        return {
            "action": "CronCreate",
            "cron_interval": "*/7 * * * *",
            "durable": True,
            "prompt": (
                f"Run `python orchestrator.py {file_path} --ci-check`. "
                f"Read the JSON output. If status is `need_agent`, spawn the "
                f"debugger agent with the provided parameters "
                f"(run_in_background=true, mode=bypassPermissions). "
                f"When done, feed the result back: save the JSON to the "
                f"`feed_file` path from the task spec (Write tool), then run "
                f"the `on_complete.command`. "
                f"Loop until status is `schedule_cron` or `done`."
            ),
            "note": "Use CronCreate with this prompt. Save the job ID for later cleanup.",
        }

    def done_next_steps(self, pr_number: int | None) -> str:
        return (
            "All CI checks passed. Mark the draft PR as ready for review:\n"
            f"  gh pr ready {pr_number}\n"
            "Then delete the CI cron jobs (CronDelete). The workflow is truly finished."
        )

    def git_preflight(self) -> list[str]:
        """Detect git operations that Claude Code's permission settings deny."""
        denied: list[str] = []
        settings_candidates = [
            Path.home() / ".claude" / "settings.json",
            Path.home() / ".claude" / "settings.local.json",
            Path.cwd() / ".claude" / "settings.json",
            Path.cwd() / ".claude" / "settings.local.json",
        ]
        targets = (
            ("git commit", "Bash(git commit"),
            ("git push", "Bash(git push"),
        )
        for path in settings_candidates:
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            rules = (data.get("permissions") or {}).get("deny") or []
            if not isinstance(rules, list):
                continue
            for rule in rules:
                if not isinstance(rule, str):
                    continue
                for op, prefix in targets:
                    if op not in denied and rule.startswith(prefix):
                        denied.append(op)
        return denied

    def git_preflight_error(self, blocked: list[str], file_path: str) -> str:
        return (
            "CI automation cannot push fixes: your Claude Code permission "
            f"settings deny `{'` and `'.join(blocked)}` (found in "
            "permissions.deny of a settings.json file). The debugger agent "
            "needs `git commit` and `git push` to apply and push CI fixes. "
            "Allow them — add `Bash(git commit *)` / `Bash(git push *)` to "
            "permissions.allow (or remove them from permissions.deny) in "
            "~/.claude/settings.json or the project settings — then re-run "
            f"`python orchestrator.py {file_path} --ci-check`."
        )
