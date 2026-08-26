"""Harness-agnostic AgentTask builders.

These functions construct the ``AgentTask`` objects that the state machines
emit for the host to execute. They contain no harness knowledge: they build
only task content (phase, agent name, prompt, context). The host-specific
spec shape, permission mode, and tooling are the harness's concern
(``agent/harnesses/``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agent.harness import AgentTask

_PROMPT_DIR = Path(__file__).parent / "prompts"
_SKILL_DIR = _PROMPT_DIR.parent.parent
_CORE_REF_DIR = str(_SKILL_DIR / "reference")
_NON_CORE_FIELDS = {"distributed", "graph"}


def _load_prompt(name: str) -> str:
    return (_PROMPT_DIR / f"{name}.md").read_text()


def _field_from_ref_dir(ref_dir: str) -> str:
    """Derive the workflow field from a reference directory path."""
    name = Path(ref_dir).name
    return name if name in _NON_CORE_FIELDS else "core"


def _field_from_workspace(workspace: str) -> str:
    """Derive the workflow field from a fielded workspace path."""
    parts = Path(workspace).parts
    if "refactor" not in parts:
        return "core"
    idx = parts.index("refactor")
    if idx + 1 >= len(parts):
        return "core"
    candidate = parts[idx + 1]
    return candidate if candidate in _NON_CORE_FIELDS or candidate == "core" else "core"


def _core_ref_dir(field: str, ref_dir: str) -> str:
    """Return the core fallback reference directory."""
    return ref_dir if field == "core" else _CORE_REF_DIR


def _coder_prompt(
    file_path: str,
    workspace: str,
    coder_id: str,
    rule: str,
    rule_description: str,
    instructions: str,
    total_rules: int,
) -> str:
    """Format coder.md; shared by build_coder_tasks and build_respawn_task."""
    field = _field_from_workspace(workspace)
    if field != "core":
        return _load_prompt("coder_baseline").format(
            coder_id=coder_id,
            file_name=Path(file_path).stem,
            file_path=file_path,
            workspace=workspace,
            rule=rule,
            rule_description=rule_description,
            action_items=instructions,
            total_rules=str(total_rules),
            field=field,
        )
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


# ── task builders ─────────────────────────────────────────────


def build_analyst_task(file_path: str, workspace: str, ref_dir: str) -> AgentTask:
    file_name = Path(file_path).stem
    field = _field_from_ref_dir(ref_dir)
    if field == "core":
        prompt = _load_prompt("analyst").format(
            file_name=file_name,
            file_path=file_path,
            workspace=workspace,
            ref_dir=ref_dir,
        )
    else:
        prompt = _load_prompt("analyst_baseline").format(
            file_name=file_name,
            file_path=file_path,
            workspace=workspace,
            ref_dir=ref_dir,
            core_ref_dir=_core_ref_dir(field, ref_dir),
            field=field,
        )
    return AgentTask(phase="analyze", agent_name="analyst", prompt=prompt)


def build_coder_tasks(
    file_path: str,
    workspace: str,
    coder_tasks: list,
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
                agent_name="coder",  # fixed name for follow-up targeting
                prompt=prompt,
            )
        )
    return tasks


def build_send_message(
    to: str,
    message_type: str,
    rule: str = "",
    rule_description: str = "",
    instructions: str = "",
    target_id: str = "",
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
    # Use target_id for follow-up; fall back to the role name if not registered yet
    target = target_id or to
    return AgentTask(
        phase="code",
        agent_name=to,
        prompt=msg,
        context={"send_message_to": target},
    )


def build_checker_task(
    file_path: str,
    workspace: str,
    ref_dir: str,
    original_test_count: int,
    verification_summary: str,
    scope: str = "file",
    rule_context: dict | None = None,
) -> AgentTask:
    field = _field_from_ref_dir(ref_dir)
    template = "checker" if field == "core" else "checker_baseline"
    format_kwargs = {
        "file_name": Path(file_path).stem,
        "file_path": file_path,
        "workspace": workspace,
        "ref_dir": ref_dir,
        "original_test_count": original_test_count,
        "verification_summary": verification_summary,
        "field": field,
        "core_ref_dir": _core_ref_dir(field, ref_dir),
    }
    if scope == "rule" and rule_context:
        prompt = _load_prompt(template).format(
            **format_kwargs,
            scope="PER-RULE",
            scope_detail=(
                f"Only check rule **{rule_context.get('rule', '?')}**: "
                f"{rule_context.get('rule_description', '?')}\n\n"
                f"Coder actions: {rule_context.get('instructions', 'none')}\n"
                f"Coder result: {rule_context.get('result_summary', 'no result')}"
            ),
        )
    else:
        prompt = _load_prompt(template).format(
            **format_kwargs,
            scope="FULL FILE",
            scope_detail="Review the entire file against all decoupling standards.",
        )
    return AgentTask(phase="review", agent_name="checker", prompt=prompt)


def build_debugger_task(
    file_path: str,
    workspace: str,
) -> AgentTask:
    prompt = _load_prompt("debugger").format(
        file_path=file_path,
        workspace=workspace,
    )
    return AgentTask(phase="debug", agent_name="debugger", prompt=prompt)


def build_feedback_triage_task(comments: list) -> AgentTask:
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
    return AgentTask(phase="triage", agent_name="feedback_triage", prompt=prompt)


def build_feedback_analyst_task(comment_and_triage: dict) -> AgentTask:
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
    return AgentTask(phase="draft", agent_name="feedback_analyst", prompt=prompt)


def build_reviewer_task(
    item: Any,
    workspace: str,
    result_file: str,
) -> AgentTask:
    prompt = _load_prompt("reviewer").format(
        pr_number=item.pr_number,
        pr_url=item.url,
        title=item.title,
        author=item.author,
        state=item.state,
        result_file=result_file,
        workspace=workspace,
        review_skill_path=str(
            _SKILL_DIR / "agent" / "skills" / "review-test-refactoring" / "SKILL.md"
        ),
    )
    return AgentTask(
        phase="review",
        agent_name=f"reviewer_pr_{item.pr_number}",
        prompt=prompt,
        context={"pr_number": item.pr_number},
    )


def build_fix_tasks(
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
        build_send_message(
            to="coder",
            target_id=coder_id,
            message_type="fix",
            rule="review-fix",
            rule_description="Fix final review findings",
            instructions=items_text,
        )
    ]


def build_test_fix_task(
    file_path: str,
    workspace: str,
    failures: list,
    deferred_failures: list,
    agent_ids: dict[str, str] | None = None,
) -> AgentTask:
    items = "\n".join(
        f"- `{f.test_name}` [{f.outcome}] ({f.device_type or '?'}): {f.message[:500]}"
        for f in failures
    )
    deferred_text = (
        "\n".join(
            f"- `{d.test_name}` — {d.defer_reason or 'deferred'}"
            for d in deferred_failures
        )
        or "_(none)_"
    )
    prompt = (
        "The local test gate found failing tests in the refactored file "
        f"`{file_path}`. For EACH failure below, decide whether it is caused "
        "by this refactoring (fix it) or pre-existing/environmental "
        "(defer it — do NOT fix). Fix only refactor-caused failures.\n\n"
        "## Failing tests\n\n"
        f"{items or '_(none)_'}\n\n"
        "## Already deferred (do not re-judge)\n\n"
        f"{deferred_text}\n\n"
        "## What to do\n\n"
        "1. For refactor-caused failures, edit the file to fix them. "
        "Keep changes minimal and surgical.\n"
        "2. For pre-existing/environmental failures, leave the file as-is "
        "and mark them `deferred`.\n"
        "3. Report one verdict per failing test: `fixed` or `deferred`."
    )
    coder_id = (agent_ids or {}).get("coder", "")
    return AgentTask(
        phase="test",
        agent_name="coder",
        prompt=prompt,
        context={"send_message_to": coder_id},
    )


def build_ruleset_editor_task(prompt: str) -> AgentTask:
    return AgentTask(
        phase="apply",
        agent_name="ruleset_editor",
        prompt=prompt,
    )


def build_respawn_task(
    message_task: AgentTask,
    file_path: str,
    workspace: str,
    rule_context: dict | None,
) -> AgentTask:
    """Rebuild a full role prompt for a replacement agent.

    Used by harnesses that re-spawn with the full role contract on fallback
    (Codex); harnesses that keep the bare follow-up prompt (Claude) do not
    call it.
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
        prompt=prompt,
    )
