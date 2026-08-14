"""Claude Code adapter - returns structured AgentTask objects.

The Flow calls these methods; Claude (the LLM) reads the returned
AgentTask objects and spawns agents via the Agent tool. This adapter also
owns the Claude-specific task-spec shape, completion notes, CronCreate
policy, and permission-based git preflight.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.adapter import BaseAdapter, AgentTask
from state import FlowSignal


_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text()


def _coder_prompt(
    file_path: str,
    workspace: str,
    coder_id: str,
    rule: str,
    rule_description: str,
    instructions: str,
    total_rules: int,
) -> str:
    """Format coder.md; shared by build_coder_tasks and Codex build_respawn_task."""
    return _load_prompt("coder").format(
        coder_id=coder_id,
        file_name=Path(file_path).stem,
        file_path=file_path,
        workspace=workspace,
        rule=rule,
        rule_description=rule_description,
        action_items=instructions,
        total_rules=str(total_rules),
    )


class ClaudeCodeAdapter(BaseAdapter):
    harness_name = "claude"

    def execute(self, task: AgentTask) -> str:
        raise NotImplementedError(
            "ClaudeCodeAdapter does not execute tasks directly. "
            "Use flow signals + Agent tool instead."
        )

    def build_analyst_task(
        self, file_path: str, workspace: str, ref_dir: str
    ) -> AgentTask:
        file_name = Path(file_path).stem
        prompt = _load_prompt("analyst").format(
            file_name=file_name,
            file_path=file_path,
            workspace=workspace,
            ref_dir=ref_dir,
        )
        return AgentTask(
            phase="analyze",
            agent_name="analyst",
            agent_type="general-purpose",
            prompt=prompt,
            run_in_background=True,
            mode="acceptEdits",
        )

    def build_coder_tasks(
        self,
        file_path: str,
        workspace: str,
        coder_tasks: list,
        strategy_assignments: dict | None = None,
        first_spawn: bool = False,
        total_rules: int = 1,
    ) -> list[AgentTask]:
        tasks = []
        for ct in coder_tasks:
            prompt = _coder_prompt(
                file_path=file_path,
                workspace=workspace,
                coder_id=ct.coder_id,
                rule=ct.rule,
                rule_description=ct.rule_description,
                instructions=ct.instructions,
                total_rules=total_rules,
            )
            tasks.append(
                AgentTask(
                    phase="code",
                    agent_name="coder",  # fixed name for SendMessage
                    agent_type="general-purpose",
                    prompt=prompt,
                    run_in_background=True,
                    mode="acceptEdits",
                )
            )
        return tasks

    def build_send_message(
        self,
        to: str,
        message_type: str,
        rule: str = "",
        rule_description: str = "",
        instructions: str = "",
        agent_id: str = "",
    ) -> AgentTask:
        if message_type == "next_rule":
            msg = (
                f"Now apply the next rule: **{rule_description}**.\n\n"
                f"Action items:\n{instructions}\n\n"
                f"Previous rules are already applied in the file. "
                f"Apply only this rule, then report your result and wait."
            )
        else:  # fix
            msg = (
                f"Checker found issues with your change for rule **{rule_description}**.\n\n"
                f"Fix these issues:\n{instructions}\n\n"
                f"Report your result when done."
            )
        # Use agent_id for SendMessage target; fall back to name if not registered yet
        target = agent_id or to
        return AgentTask(
            phase="code",
            agent_name=to,
            agent_type="general-purpose",
            prompt=msg,
            run_in_background=False,  # SendMessage, not a new spawn
            context={"send_message_to": target, "message_type": message_type},
            agent_id=agent_id,
        )

    def build_checker_task(
        self,
        file_path: str,
        workspace: str,
        ref_dir: str,
        original_test_count: int,
        verification_summary: str,
        scope: str = "file",
        rule_context: dict | None = None,
    ) -> AgentTask:
        if scope == "rule" and rule_context:
            prompt = _load_prompt("checker").format(
                file_name=Path(file_path).stem,
                file_path=file_path,
                workspace=workspace,
                ref_dir=ref_dir,
                original_test_count=original_test_count,
                verification_summary=verification_summary,
                scope="PER-RULE",
                scope_detail=(
                    f"Only check rule **{rule_context.get('rule', '?')}**: "
                    f"{rule_context.get('rule_description', '?')}\n\n"
                    f"Coder actions: {rule_context.get('instructions', 'none')}\n"
                    f"Coder result: {rule_context.get('result_summary', 'no result')}"
                ),
            )
        else:
            prompt = _load_prompt("checker").format(
                file_name=Path(file_path).stem,
                file_path=file_path,
                workspace=workspace,
                ref_dir=ref_dir,
                original_test_count=original_test_count,
                verification_summary=verification_summary,
                scope="FULL FILE",
                scope_detail="Review the entire file against all decoupling standards.",
            )
        return AgentTask(
            phase="review",
            agent_name="checker",
            agent_type="general-purpose",
            prompt=prompt,
            run_in_background=True,
            mode="acceptEdits",
        )

    def build_debugger_task(
        self,
        file_path: str,
        workspace: str,
    ) -> AgentTask:
        prompt = _load_prompt("debugger").format(
            file_path=file_path,
            workspace=workspace,
        )
        return AgentTask(
            phase="debug",
            agent_name="debugger",
            agent_type="general-purpose",
            prompt=prompt,
            run_in_background=True,
            mode="bypassPermissions",
        )

    def build_feedback_triage_task(self, comments: list) -> AgentTask:
        import json

        comments_json = json.dumps(
            [
                {
                    "comment_id": c.comment_id,
                    "pr_number": c.pr_number,
                    "author": c.author,
                    "source": c.source,
                    "html_url": c.html_url,
                    "body": c.body[:6000],
                }
                for c in comments
            ],
            indent=2,
        )
        prompt = _load_prompt("feedback_triage").format(comments_json=comments_json)
        return AgentTask(
            phase="triage",
            agent_name="feedback_triage",
            agent_type="general-purpose",
            prompt=prompt,
            run_in_background=True,
            mode="default",
        )

    def build_feedback_analyst_task(self, comment_and_triage: dict) -> AgentTask:
        import json

        comment = comment_and_triage["comment"]
        triage = comment_and_triage["triage"]
        payload = json.dumps(
            {
                "comment_id": comment.comment_id,
                "pr_number": comment.pr_number,
                "author": comment.author,
                "source": comment.source,
                "html_url": comment.html_url,
                "body": comment.body,
                "target_layers": triage.get("target_layers", []),
                "triage_summary": triage.get("summary", ""),
                "tier": triage.get("tier", "Major"),
            },
            indent=2,
        )
        prompt = _load_prompt("feedback_analyst").format(payload_json=payload)
        return AgentTask(
            phase="draft",
            agent_name="feedback_analyst",
            agent_type="general-purpose",
            prompt=prompt,
            run_in_background=True,
            mode="default",
        )

    def build_fix_tasks(
        self,
        file_path: str,
        workspace: str,
        findings: list,
        agent_ids: dict[str, str] | None = None,
    ) -> list[AgentTask]:
        items_text = "\n".join(
            f"- [{f.severity}] {f.category}: {f.description} (line {f.line_number})"
            for f in findings
        )
        coder_id = (agent_ids or {}).get("coder", "")
        return [
            self.build_send_message(
                to="coder",
                agent_id=coder_id,
                message_type="fix",
                rule="review-fix",
                rule_description="Fix final review findings",
                instructions=items_text,
            )
        ]

    # ── new builders ────────────────────────────────────────────

    def build_ruleset_editor_task(self, prompt: str) -> AgentTask:
        return AgentTask(
            phase="apply",
            agent_name="ruleset_editor",
            agent_type="general-purpose",
            prompt=prompt,
            run_in_background=True,
            mode="acceptEdits",
        )

    def build_respawn_task(
        self,
        message_task: AgentTask,
        file_path: str,
        workspace: str,
        rule_context: dict | None,
    ) -> AgentTask:
        """Rebuild a full role prompt for a replacement agent.

        Claude Code keeps the bare follow-up prompt on fallback, so this is
        only exercised by harnesses that re-spawn with the full role contract
        (Codex). It lives here so both adapters share the coder template.
        """
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
            agent_type="general-purpose",
            prompt=prompt,
            run_in_background=True,
            mode="acceptEdits",
        )

    # ── harness-specific emission ───────────────────────────────

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

        spec: dict[str, Any] = {
            "method": method,
            "agent_name": task.agent_name,
            "agent_type": getattr(task, "agent_type", "general-purpose"),
            "run_in_background": getattr(task, "run_in_background", False),
            "mode": getattr(task, "mode", "default"),
            "prompt": task.prompt,
        }

        if method == "send_message":
            target_id = getattr(task, "agent_id", "") or task.context.get(
                "send_message_to", task.agent_name
            )
            spec["send_to"] = target_id
            spec["fallback"] = {
                "method": "spawn",
                "agent_name": task.agent_name,
                "agent_type": getattr(task, "agent_type", "general-purpose"),
                "run_in_background": True,
                "mode": getattr(task, "mode", "default"),
                "prompt": task.prompt,
                "note": (
                    "If you use this fallback, include "
                    '"agent_id" and "agent_name" in the result JSON '
                    "so the orchestrator can register the new agent ID."
                ),
            }

        return spec

    def ci_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        return {
            "method": "spawn",
            "agent_name": task.agent_name,
            "agent_type": getattr(task, "agent_type", "general-purpose"),
            "run_in_background": getattr(task, "run_in_background", False),
            "mode": getattr(task, "mode", "default"),
            "prompt": task.prompt,
        }

    def ingest_task_to_spec(self, task: AgentTask) -> dict[str, Any]:
        return {
            "method": "spawn",
            "agent_name": task.agent_name,
            "agent_type": getattr(task, "agent_type", "general-purpose"),
            "run_in_background": getattr(task, "run_in_background", False),
            "mode": getattr(task, "mode", "default"),
            "prompt": task.prompt,
        }

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

    def ci_wait_on_complete(self, file_path: str) -> dict[str, Any]:
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

    def ci_done_next_steps(self, pr_number: int | None) -> str:
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
