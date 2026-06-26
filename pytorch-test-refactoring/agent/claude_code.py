"""Claude Code adapter — returns structured AgentTask objects.

The Flow calls these methods; Claude (the LLM) reads the returned
AgentTask objects and spawns agents via the Agent tool.
"""

from pathlib import Path

from agent.adapter import BaseAdapter, AgentTask


_PROMPT_DIR = Path(__file__).parent / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text()


class ClaudeCodeAdapter(BaseAdapter):
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
            prompt = _load_prompt("coder").format(
                coder_id=ct.coder_id,
                file_name=Path(file_path).stem,
                file_path=file_path,
                workspace=workspace,
                rule=ct.rule,
                rule_description=ct.rule_description,
                action_items=ct.instructions,
                total_rules=str(total_rules),
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
