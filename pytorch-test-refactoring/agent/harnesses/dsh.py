"""DeepSeek Harness (dsh) — emission and policy only.

dsh exposes ``subagent`` / ``send_message`` for delegation and
``schedule_create`` / ``schedule_edit`` for recurring work instead of Claude
Code's ``Agent`` / ``SendMessage`` / ``CronCreate``. Two capabilities shape
the mapping:

* ``subagent`` always runs in the background and returns its id immediately;
  there is no blocking wait — the runtime delivers a completion notice, which
  starts the executor's next turn.
* ``send_message`` continues a direct child (``agent_id`` on stock dsh,
  ``target`` when Agent Teams is enabled), so follow-ups reuse the same child
  instead of re-seeding context.

Prompt/AgentTask construction is shared via ``agent/tasks.py``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.harness import AgentTask, Harness
from agent.tasks import build_respawn_task


CI_POLL_MINUTES = 7


class DshHarness(Harness):
    name = "dsh"
    supports_delegated_agents = True

    # ── emission ──────────────────────────────────────────────

    def spawn(self, task: AgentTask) -> dict[str, Any]:
        return {
            "method": "spawn",
            "tool": "subagent",
            "agent_name": task.agent_name,
            "description": f"{task.agent_name} agent",
            "prompt": task.prompt,
            "note": (
                "Runs in the background; the tool result is `started subagent "
                "<id>`, and that id is returned immediately. The runtime "
                "delivers a completion notice when it finishes; do not poll "
                "`list_agents`."
            ),
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
        respawn = (
            build_respawn_task(task, file_path, workspace, rule_context)
            if rule_context
            else None
        )
        return {
            "method": "send_message",
            "tool": "send_message",
            "agent_name": task.agent_name,
            # The same id under both names: stock dsh's send_message takes
            # `agent_id`, the Agent Teams variant takes `target`.
            "agent_id": target,
            "target": target,
            "message": task.prompt,
            "note": (
                "Pass the id as `agent_id` (stock dsh) or `target` (Agent "
                "Teams); if the tool rejects it because a subagent id is not a "
                "Team member, use this spec's `fallback` to re-dispatch. The "
                "child's reply arrives as a notification."
            ),
            "fallback": {
                "method": "spawn",
                "tool": "subagent",
                "agent_name": task.agent_name,
                "description": f"{task.agent_name} agent",
                "prompt": respawn.prompt if respawn else task.prompt,
                "note": "register the new agent_id in the result JSON",
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
                "1. Dispatch each task with the `subagent` tool, passing the "
                "task's `description` and `prompt`; it returns an agent id "
                "immediately. "
                "2. End your turn if there is nothing else to do — the runtime "
                "delivers the completion notice, which starts your next turn. "
                "Do not poll. "
                "3. Extract the key result into JSON from the notice. "
                "4. If you spawned a NEW agent (method=spawn), include "
                '"agent_id" (the id in `started subagent <id>`) and '
                '"agent_name" (from the task spec) in the JSON. '
                "5. Write the result JSON to a file at "
                f"`{feed_file}` with the `write` tool, then run with `bash`:\n"
                f"   {feed_cmd}"
            )
        if kind == "ci_debugger":
            return (
                "1. Dispatch the debugger with the `subagent` tool "
                "(`description` + `prompt` from the task spec). "
                "2. When the completion notice arrives, extract the key result "
                "into JSON (see ci-automation SKILL.md) and include "
                '"agent_id" and "agent_name": "debugger". '
                "3. Write the result JSON to a file at "
                f"`{feed_file}` with the `write` tool, then run with `bash`:\n"
                f"   {feed_cmd}\n"
                "Note: the debugger needs `git commit`/`git push`. If the "
                "session's sandbox denies them it reports the fixes as "
                "uncommitted — push manually and re-run `--ci-check --resume`."
            )
        if kind == "ingest":
            return (
                "1. Read the agent's result.\n"
                "2. Write the result JSON to a file at "
                f"`{feed_file}` with the `write` tool, then run with `bash`:\n"
                f"   {feed_cmd}"
            )
        return ""

    def wait_on_complete(self, file_path: str) -> dict[str, Any]:
        start = datetime.now(timezone.utc) + timedelta(minutes=CI_POLL_MINUTES)
        start = start.replace(second=0, microsecond=0)
        return {
            "action": "schedule_create",
            "title": f"Monitor PyTorch CI: {Path(file_path).name}",
            "target": "this_session",
            "rrule": (
                f"DTSTART:{start:%Y%m%dT%H%M%SZ}\n"
                f"RRULE:FREQ=MINUTELY;INTERVAL={CI_POLL_MINUTES}"
            ),
            "prompt": (
                f"Run `python orchestrator.py --harness dsh {file_path} "
                f"--ci-check`. Read the JSON output. If status is "
                f"`need_agent`, dispatch the debugger with the `subagent` tool "
                f"using the task spec's `description` and `prompt`. When the "
                f"completion notice arrives, write the result JSON to the "
                f"`feed_file` path from the task spec with the `write` tool, "
                f"then run the `on_complete.command`. Loop until status is "
                f"`schedule_cron` or `done`."
            ),
            "note": (
                "Call `schedule_create` with these exact arguments. Save the "
                "returned task id; delete the schedule with "
                "`schedule_edit(action=delete)` once CI is green."
            ),
        }

    def done_next_steps(self, pr_number: int | None) -> str:
        return (
            "All CI checks passed. Mark the draft PR as ready for review:\n"
            f"  gh pr ready {pr_number}\n"
            "Then delete the CI schedule: find it with `schedule_list`, then "
            "`schedule_edit(id=..., action=delete)`. The workflow is truly "
            "finished."
        )
