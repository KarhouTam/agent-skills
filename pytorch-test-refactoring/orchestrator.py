#!/usr/bin/env python3
"""Deterministic orchestrator for the test refactoring workflow.

Replaces the LLM-driven while-loop in SKILL.md. The LLM's role is reduced
to a simple executor: run this script, read the JSON task spec on stdout,
execute the instructions (spawn agent or send message), extract key info
from the agent's output, and feed it back via stdin.

Usage:
    # Start a new refactoring
    python orchestrator.py test/test_ops.py

    # Feed agent result and continue (prefer --feed-file: it keeps the
    # command a single plain prefix that matches a Bash allow rule, unlike
    # stdin/redirect forms which get blocked in Auto/restricted modes)
    python orchestrator.py test/test_ops.py --feed coder --feed-file result.json

    # Resume an interrupted workflow
    python orchestrator.py test/test_ops.py --resume

Protocol:
    The script outputs a JSON task spec to stdout. All other output goes
    to stderr. The JSON has three possible shapes:

    1. {"status": "need_agent", "tasks": [...], "on_complete": {...}}
       → Execute each task, then run on_complete.command with the result.

    2. {"status": "done", "summary_path": "...", "workspace": "..."}
       → Workflow complete. Read the summary at summary_path.

    3. {"status": "error", "message": "...", "phase": "..."}
       → Something went wrong. Check the message and workspace.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

# Ensure the skill directory is on sys.path so imports work when the
# script is invoked from any working directory.
_skill_dir = str(Path(__file__).resolve().parent)
if _skill_dir not in sys.path:
    sys.path.insert(0, _skill_dir)

from flow import RefactorFlow
from ci_ops import CIOps
from ingest_ops import IngestOps
from state import (
    FlowSignal,
    AnalystReport,
    CoderResult,
    ReviewFindings,
    RefactorState,
)
from utils import ANALYST_REPORT_JSON
from agent.registry import get_adapter


# ── argument parsing ──────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PyTorch Test Refactoring Orchestrator"
    )
    parser.add_argument(
        "file_path",
        nargs="?",
        default=None,
        help=(
            "Path to the test file to refactor (e.g. test/test_ops.py). "
            "Omit for --ingest-feedback / --apply-ingest."
        ),
    )
    parser.add_argument(
        "--feed",
        choices=[
            "analyst",
            "coder",
            "checker",
            "debugger",
            "feedback_triage",
            "feedback_analyst",
            "ruleset_editor",
        ],
        help=(
            "Feed agent output back to the orchestrator. "
            "Reads a JSON object from stdin describing the agent result."
        ),
    )
    parser.add_argument(
        "--feed-file",
        type=str,
        default=None,
        help=(
            "Path to a JSON file containing the agent result to feed back. "
            "Alternative to piping JSON via stdin. Preferred in Auto/restricted "
            "permission modes: piped commands like `echo '{...}' | python ...` "
            "don't match Bash allow rules (whole-string prefix matching), while "
            "a plain `python orchestrator.py ... --feed-file <path>` command does."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from workspace artifacts on disk (cross-process resume).",
    )
    parser.add_argument(
        "--ci-check",
        action="store_true",
        help="Run CI monitoring phase (for cron invocations). Skips refactoring phases.",
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        default=None,
        help="PR number for CI monitoring. If omitted, auto-detected from current branch.",
    )
    parser.add_argument(
        "--ingest-feedback",
        action="store_true",
        help="Run the PR feedback ingest sidecar (harvest + triage + draft).",
    )
    parser.add_argument(
        "--apply-ingest",
        type=str,
        default=None,
        metavar="FINDINGS_FILE",
        help="Apply approved findings from a feedback_findings.md file.",
    )
    parser.add_argument(
        "--harness",
        choices=["claude", "codex"],
        default=os.environ.get("PYTORCH_TEST_REFACTOR_HARNESS", "claude"),
        help=(
            "Agent harness to emit task specs for. Defaults to the "
            "PYTORCH_TEST_REFACTOR_HARNESS env var, then 'claude'."
        ),
    )
    return parser.parse_args()


# ── main entry point ──────────────────────────────────────────────


def main() -> None:
    args = _parse_args()
    adapter = get_adapter(args.harness)

    if args.ingest_feedback:
        _run_ingest(args, adapter)
        return

    if args.apply_ingest:
        _run_apply_ingest(args, adapter)
        return

    if args.ci_check:
        _run_ci_check(args, adapter)
        return

    flow = RefactorFlow(adapter=adapter)

    if args.feed:
        # ── feed path ──────────────────────────────────────────
        # Step 1: Restore  state and position the state machine at the
        #   correct phase.  flow.run() with resume=True loads artifacts
        #   from disk and runs _run_phases() to set current_phase,
        #   rule_sub_phase, etc.  It stops at the same AI-needed step
        #   that hasn't been satisfied yet.
        state = flow.run(args.file_path, resume=True)

        # Step 2: Read and apply the agent result.  This satisfies the
        #   guard that was blocking progress.
        raw = _read_feed(args)
        feed_data = _parse_feed_json(raw, args.feed)

        success = _dispatch_feed(flow, args.feed, feed_data)
        if not success:
            _emit_error(
                flow,
                f"Failed to process --feed {args.feed}. "
                f"Check that the stdin JSON matches the expected format. "
                f"Phase: {state.current_phase}",
            )
            return

        # Step 3: Advance through deterministic phases until the next
        #   AI-needed step.
        state = flow.run(args.file_path)

    else:
        # ── fresh / resume path ────────────────────────────────
        state = flow.run(args.file_path, resume=args.resume)

    # ── emit next action ──
    _emit_next_action(flow, state)


# ── stdin helpers ─────────────────────────────────────────────────


def _read_stdin() -> str:
    """Read all of stdin. Return '' if stdin is a TTY (no pipe)."""
    if sys.stdin.isatty():
        return ""
    return sys.stdin.read().strip()


def _read_feed(args) -> str:
    """Read the agent result JSON from --feed-file if given, else stdin.

    Feeding via a file is the permission-friendly path: the harness saves
    the result JSON with the Write tool, then runs a plain
    `python orchestrator.py ... --feed X --feed-file <path>` command that
    matches a `Bash(python *)` allow rule. Piped alternatives
    (`echo '{...}' | python ...`) don't match any single Bash prefix rule
    and get blocked in Auto mode.
    """
    feed_file = getattr(args, "feed_file", None)
    if feed_file:
        path = Path(feed_file)
        if not path.exists():
            print(
                f"Warning: feed file not found: {path}. Using stdin defaults.",
                file=sys.stderr,
            )
            return ""
        return path.read_text(encoding="utf-8").strip()
    return _read_stdin()


def _parse_feed_json(raw: str, feed_type: str) -> dict[str, Any]:
    """Parse the stdin JSON for a given feed type.

    Returns an empty dict if raw is empty or unparseable, so the
    dispatch function can apply sensible defaults (e.g. mark as failed).
    """
    if not raw:
        print(
            f"Warning: empty stdin for --feed {feed_type}, using defaults",
            file=sys.stderr,
        )
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(
            f"Warning: invalid JSON for --feed {feed_type}, using defaults",
            file=sys.stderr,
        )
        return {}


# ── feed dispatch ─────────────────────────────────────────────────


def _dispatch_feed(
    flow: RefactorFlow,
    feed_type: str,
    data: dict[str, Any],
) -> bool:
    """Route agent result to the correct flow.feed_* method.

    The correct method depends on both the feed_type AND the current
    phase / sub_phase.  This is the logic that was previously left to
    the LLM to figure out from SKILL.md.
    """
    state = flow.state

    try:
        # ── register agent ID (if provided) ──────────────────
        # The LLM includes agent_id in the result JSON after spawning.
        # This must happen BEFORE the feed dispatch so that subsequent
        # SEND_MESSAGE signals can target the correct agent ID.
        agent_id = data.get("agent_id", "")
        agent_name = data.get("agent_name", "")
        if agent_id and agent_name:
            flow.feed_agent_spawned(agent_name, agent_id)

        # ── analyst ──────────────────────────────────────────
        if feed_type == "analyst":
            report = _load_analyst_from_workspace(state) or _build_analyst_report(data)
            flow.feed_analyst_result(report)
            return True

        # ── coder ────────────────────────────────────────────
        if feed_type == "coder":
            result = _build_coder_result(data)

            if state.current_phase == "fix":
                # Review fix round complete — verify and maybe re-review
                flow.feed_fix_complete()
            elif state.rule_sub_phase == "fix":
                # Per-rule fix complete — go back to check
                flow.feed_rule_fix_result("coder", result)
            else:
                # Rule applied — transition to per-rule check
                flow.feed_coder_result("coder", result)
            return True

        # ── checker ──────────────────────────────────────────
        if feed_type == "checker":
            if state.current_phase == "code":
                # Per-rule scope: coder just applied one rule
                passed = data.get("passed", False)
                flow.feed_rule_check_result(passed)
            elif state.current_phase == "review":
                # Full-file scope: mandatory Phase 6 review
                findings = _build_review_findings(data)
                flow.feed_review_findings(findings)
            else:
                print(
                    f"Warning: --feed checker in unexpected phase "
                    f"'{state.current_phase}', treating as per-rule",
                    file=sys.stderr,
                )
                flow.feed_rule_check_result(data.get("passed", False))
            return True

    except Exception as exc:
        print(f"dispatch_feed error: {exc}", file=sys.stderr)
        return False

    return False


# ── result builders (LLM-provided JSON → Pydantic models) ─────────


def _load_analyst_from_workspace(state: RefactorState) -> AnalystReport | None:
    """Try to load the analyst report from the workspace file.

    The analyst agent writes analyst_report.json to workspace.  If it
    exists, we prefer it over the LLM-extracted JSON because it's the
    raw structured output directly from the agent.
    """
    if state.workspace is None:
        return None
    path = state.workspace / ANALYST_REPORT_JSON
    if not path.exists():
        return None
    try:
        return AnalystReport.model_validate_json(path.read_text())
    except Exception:
        return None


def _build_analyst_report(data: dict[str, Any]) -> AnalystReport | None:
    """Build AnalystReport from LLM-provided JSON.  Returns None on failure."""
    if not data:
        return None
    try:
        return AnalystReport(**data)
    except Exception:
        return None


def _build_coder_result(data: dict[str, Any]) -> CoderResult:
    """Build CoderResult from LLM-provided JSON, with safe defaults."""
    return CoderResult(
        coder_id=data.get("coder_id", "coder"),
        success=data.get("success", False),
        tests_moved=data.get("tests_moved", []),
        errors=data.get("errors", []),
        warnings=data.get("warnings", []),
    )


def _build_review_findings(data: dict[str, Any]) -> ReviewFindings:
    """Build ReviewFindings from LLM-provided JSON, with safe defaults."""
    if not data:
        return ReviewFindings(all_clear=True, findings=[])
    try:
        return ReviewFindings(**data)
    except Exception:
        return ReviewFindings(
            all_clear=data.get("all_clear", True),
            findings=data.get("findings", []),
            summary=data.get("summary", ""),
        )


# ── output emitters ───────────────────────────────────────────────


def _emit_next_action(flow: RefactorFlow, state: RefactorState) -> None:
    """Output the next action as JSON to stdout.

    Three possible outputs:
      - done:     workflow complete
      - need_agent:  LLM must spawn or message an agent
      - error:    unexpected state
    """
    signal = state.signal

    # ── workflow complete ────────────────────────────────────
    if state.current_phase == "finalize" and signal == FlowSignal.DONE:
        _emit_done(state)
        return

    # ── need an agent ────────────────────────────────────────
    if signal in (
        FlowSignal.SPAWN_SINGLE,
        FlowSignal.SEND_MESSAGE,
        FlowSignal.RELAY_FINDINGS,
    ):
        tasks = flow.get_pending_tasks()
        if not tasks:
            _emit_error(
                flow,
                f"Signal {signal.value} but no pending tasks returned. "
                f"Phase: {state.current_phase}, sub_phase: {state.rule_sub_phase}",
            )
            return
        _emit_tasks(flow, state, tasks, signal)
        return

    # ── deterministic phase completed internally ─────────────
    # (DONE signal but not in finalize — there are more phases)
    if signal == FlowSignal.DONE:
        # Advance again — rare, but can happen if a deterministic
        # phase completed and _run_phases didn't hit an AI phase
        state2 = flow.run(state.file_path)
        if state2.current_phase == "finalize" and state2.signal == FlowSignal.DONE:
            _emit_done(state2)
        elif state2.signal in (
            FlowSignal.SPAWN_SINGLE,
            FlowSignal.SEND_MESSAGE,
            FlowSignal.RELAY_FINDINGS,
        ):
            tasks2 = flow.get_pending_tasks()
            _emit_tasks(flow, state2, tasks2, state2.signal)
        else:
            _emit_error(
                flow,
                f"Unexpected signal after DONE re-run: "
                f"phase={state2.current_phase}, signal={state2.signal.value}",
            )
        return

    # ── unknown ──────────────────────────────────────────────
    _emit_error(
        flow,
        f"Unexpected state: phase={state.current_phase}, "
        f"signal={signal.value}, sub_phase={state.rule_sub_phase}",
    )


def _emit_done(state: RefactorState) -> None:
    """Emit the 'done' signal — workflow complete."""
    ws = str(state.workspace) if state.workspace else ""
    payload: dict = {
        "status": "done",
        "phase": state.current_phase,
        "summary_path": f"{ws}/final_summary.md" if ws else "",
        "workspace": ws,
    }
    if state.current_phase == "finalize":
        file_path = state.file_path
        payload["next_steps"] = (
            "Refactoring is complete. To start CI monitoring:\n"
            "1. Create a branch, commit, and push your changes\n"
            "2. Create a draft PR targeting upstream main\n"
            '3. Say "look after the CI" or run:\n'
            f"   python orchestrator.py {file_path} --ci-check"
        )
    _write_json(payload)


def _emit_tasks(
    flow: RefactorFlow,
    state: RefactorState,
    tasks: list[Any],
    signal: FlowSignal,
) -> None:
    """Emit the 'need_agent' task spec."""
    adapter = flow.adapter
    file_path = state.file_path
    ws = str(state.workspace) if state.workspace else "."
    rule_context = _rule_context_for(state)
    task_specs = [
        adapter.task_to_spec(
            t,
            signal,
            file_path=file_path,
            workspace=ws,
            rule_context=rule_context,
        )
        for t in tasks
    ]
    feed_as = _feed_type_for(state)

    # Build the resume command
    cmd = _cmd(adapter, file_path, "--feed", feed_as)
    feed_file = f"{ws}/{feed_as}_result.json"
    feed_cmd = f"{cmd} --feed-file {feed_file}"

    _write_json(
        {
            "status": "need_agent",
            "phase": state.current_phase,
            "rule_sub_phase": getattr(state, "rule_sub_phase", None),
            "rule_index": getattr(state, "rule_index", 0),
            "retry_count": getattr(state, "retry_count", 0),
            "tasks": task_specs,
            "on_complete": {
                "feed_as": feed_as,
                "command": feed_cmd,
                "feed_file": feed_file,
                "note": adapter.completion_note(
                    "refactor",
                    feed_file=feed_file,
                    feed_cmd=feed_cmd,
                    feed_as=feed_as,
                ),
            },
        }
    )


def _emit_error(flow: RefactorFlow, message: str) -> None:
    """Emit an error signal."""
    ws = str(flow.state.workspace) if flow.state.workspace else ""
    _write_json(
        {
            "status": "error",
            "message": message,
            "phase": flow.state.current_phase,
            "workspace": ws,
        }
    )


# -- CI handoff -------------------------------------------------------


def _run_ci_check(args, adapter) -> None:
    """Handle --ci-check: monitor CI state, possibly spawn debugger."""
    from utils import get_workspace

    file_name = Path(args.file_path).stem
    workspace = get_workspace(file_name)

    ci = CIOps(adapter=adapter)

    if args.feed:
        ci.run(args.file_path, str(workspace), resume=True)

        raw = _read_feed(args)
        feed_data = _parse_feed_json(raw, args.feed)

        if args.feed == "debugger":
            ci.feed_debugger_result(feed_data)

        ci.run(args.file_path, str(workspace))

    else:
        ci.run(
            args.file_path,
            str(workspace),
            resume=True,  # poll-to-poll state lives in ci_state.json
            pr_number=args.pr_number,
        )

    _emit_ci_action(ci)


def _emit_ci_action(ci: CIOps) -> None:
    """Emit the next CI ops action as JSON to stdout."""
    ci_state = ci.state
    signal = ci_state.signal
    ws = str(ci_state.workspace) if ci_state.workspace else ""
    file_path = ci_state.file_path
    adapter = ci.adapter

    if ci_state.ci_phase == "monitor" and signal == FlowSignal.WAITING:
        _write_json(
            {
                "status": "schedule_cron",
                "phase": "ci_monitor",
                "pr_url": ci_state.pr_url,
                "pr_number": ci_state.pr_number,
                "head_sha": ci_state.head_sha,
                "ci_phase": ci_state.ci_phase,
                "fix_round": len(ci_state.fix_history),
                "on_complete": adapter.ci_wait_on_complete(file_path),
            }
        )

    elif ci_state.ci_phase == "monitor" and signal == FlowSignal.DONE:
        ci.run(file_path, str(ci_state.workspace) if ci_state.workspace else "")
        _emit_ci_action(ci)

    elif ci_state.ci_phase == "debug" and signal == FlowSignal.SPAWN_SINGLE:
        # Preflight: the debugger applies fixes by committing and pushing.
        # Fail fast with an actionable message if the harness would block it.
        blocked_git = adapter.git_preflight()
        if blocked_git:
            _emit_error_ci(ci, adapter.git_preflight_error(blocked_git, file_path))
            return

        tasks = ci.get_pending_tasks()
        if not tasks:
            _emit_error_ci(ci, "No debugger task returned")
            return
        task_specs = [adapter.ci_task_to_spec(t) for t in tasks]
        cmd = _cmd(adapter, file_path, "--ci-check", "--feed", "debugger")
        feed_dir = str(ci_state.workspace) if ci_state.workspace else "."
        feed_file = f"{feed_dir}/debugger_result.json"
        feed_cmd = f"{cmd} --feed-file {feed_file}"
        _write_json(
            {
                "status": "need_agent",
                "phase": "ci_debug",
                "ci_phase": ci_state.ci_phase,
                "fix_round": len(ci_state.fix_history),
                "failures": [
                    {"check_name": f.check_name, "bot_label": f.bot_label}
                    for f in ci_state.failures
                ],
                "tasks": task_specs,
                "on_complete": {
                    "feed_as": "debugger",
                    "command": feed_cmd,
                    "feed_file": feed_file,
                    "note": adapter.completion_note(
                        "ci_debugger",
                        feed_file=feed_file,
                        feed_cmd=feed_cmd,
                    ),
                },
            }
        )

    elif ci_state.ci_phase == "done" or signal == FlowSignal.DONE:
        _write_json(
            {
                "status": "done",
                "phase": "ci_done",
                "pr_url": ci_state.pr_url,
                "pr_number": ci_state.pr_number,
                "fix_history": ci_state.fix_history,
                "workspace": ws,
                "next_steps": adapter.ci_done_next_steps(ci_state.pr_number),
            }
        )

    else:
        _emit_error_ci(
            ci, f"Unexpected CI state: phase={ci_state.ci_phase}, signal={signal.value}"
        )


def _emit_error_ci(ci: CIOps, message: str) -> None:
    """Emit an error signal for CI ops."""
    ws = str(ci.state.workspace) if ci.state.workspace else ""
    _write_json(
        {
            "status": "error",
            "message": message,
            "phase": f"ci_{ci.state.ci_phase}",
            "workspace": ws,
        }
    )


def _write_json(obj: dict[str, Any]) -> None:
    """Write a JSON object to stdout followed by a newline."""
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── harness-neutral helpers ───────────────────────────────────────


def _cmd(adapter, *argv) -> str:
    """Build an orchestrator command with the harness selector included.

    The harness flag must round-trip on resume/feed invocations so a
    cross-process continuation re-selects the same adapter. The default
    harness ("claude") adds no flag, keeping legacy commands unchanged.
    """
    parts = ["python", "orchestrator.py"]
    if adapter.harness_name and adapter.harness_name != "claude":
        parts.append(f"--harness {adapter.harness_name}")
    parts.extend(str(a) for a in argv)
    return " ".join(parts)


def _rule_context_for(state: RefactorState) -> dict | None:
    """Return the current coder rule context for fallback re-spawn prompts."""
    tasks = state.coder_tasks or []
    idx = state.rule_index
    if idx >= len(tasks):
        return None
    ct = tasks[idx]
    return {
        "coder_id": ct.coder_id,
        "rule": ct.rule,
        "rule_description": ct.rule_description,
        "instructions": ct.instructions,
        "total_rules": len(tasks),
    }


def _feed_type_for(state: RefactorState) -> str:
    """Determine which --feed value the LLM should use for the next result.

    This is the ONLY place this logic lives — the LLM never needs to
    figure out which feed method to call.
    """
    phase = state.current_phase

    if phase == "analyze":
        return "analyst"

    if phase == "code":
        # Per-rule check loop:
        #   code sub-phase → spawn/message coder → feed as "coder"
        #   check sub-phase → spawn checker    → feed as "checker"
        #   fix sub-phase   → message coder    → feed as "coder"
        if state.rule_sub_phase == "check":
            return "checker"
        return "coder"

    if phase == "review":
        return "checker"

    if phase == "fix":
        # Review fix — coder is fixing review findings
        return "coder"

    return "coder"


# ── feedback ingest sidecar ────────────────────────────────────────


def _run_ingest(args, adapter) -> None:
    """Handle --ingest-feedback: harvest + triage + draft, emitting agent tasks."""
    ops = IngestOps(adapter=adapter)

    if args.feed:
        ops.run()
        raw = _read_feed(args)
        data = _parse_feed_json(raw, args.feed)
        if args.feed == "feedback_triage":
            ops.feed_triage_result(data)
        elif args.feed == "feedback_analyst":
            ops.feed_draft_result(data)
        ops.run()
    else:
        ops.run()

    if ops.state.phase == "done":
        ops.finalize()
        if ops.state.pending_findings:
            _persist_ingest_findings(ops)

    _emit_ingest_action(ops)


def _persist_ingest_findings(ops: "IngestOps") -> None:
    from scripts import ingest as ingest_mod
    from utils import get_ingest_workspace

    workspace = get_ingest_workspace()
    if ops.state.pending_findings:
        ingest_mod.write_findings_md(ops.state.pending_findings, workspace)


def _emit_ingest_action(ops: "IngestOps") -> None:
    from utils import get_ingest_workspace

    sm = ops.state
    adapter = ops.adapter
    if sm.signal == FlowSignal.SPAWN_SINGLE:
        tasks = ops.get_pending_tasks()
        feed_as = "feedback_triage" if sm.phase == "triage" else "feedback_analyst"
        ws = get_ingest_workspace()
        feed_file = f"{ws}/{feed_as}_result.json"
        cmd = _cmd(
            adapter, "--ingest-feedback", "--feed", feed_as, "--feed-file", feed_file
        )
        _write_json(
            {
                "status": "need_agent",
                "phase": f"ingest_{sm.phase}",
                "tasks": [adapter.ingest_task_to_spec(t) for t in tasks],
                "on_complete": {
                    "feed_as": feed_as,
                    "command": cmd,
                    "feed_file": feed_file,
                    "note": adapter.completion_note(
                        "ingest",
                        feed_file=feed_file,
                        feed_cmd=cmd,
                    ),
                },
            }
        )
    else:
        ws = get_ingest_workspace()
        findings_path = ""
        if sm.pending_findings:
            findings_path = str(
                ws / "findings" / f"PR-{sm.pending_findings[0].pr_number}.md"
            )
        _write_json(
            {
                "status": "done",
                "phase": "ingest_done",
                "findings_path": findings_path,
                "next_steps": (
                    "Review the findings file, mark findings Approved/Rejected, "
                    "then run: python orchestrator.py --apply-ingest <findings_file>"
                ),
            }
        )


def _run_apply_ingest(args, adapter) -> None:
    """Handle --apply-ingest <file>: apply approved findings to the ruleset."""
    if not Path(args.apply_ingest).exists():
        _write_json(
            {
                "status": "error",
                "message": f"Findings file not found: {args.apply_ingest}",
            }
        )
        return

    findings = _parse_findings_file(args.apply_ingest)

    if args.feed == "ruleset_editor":
        # Second pass: the editor agent has applied edits; record CHANGELOG.
        _read_feed(args)  # result content is ignored; CHANGELOG is deterministic
        from scripts import ingest as ingest_mod

        approved = [f for f in findings if f.status == "approved"]
        changelog_path = Path(__file__).resolve().parent / "CHANGELOG.md"
        ingest_mod.append_changelog(approved, changelog_path)
        _write_json(
            {
                "status": "done",
                "phase": "ingest_apply",
                "applied_count": len(approved),
                "message": "Applied approved findings and appended a CHANGELOG entry.",
            }
        )
        return

    approved = [f for f in findings if f.status == "approved"]
    if not approved:
        _write_json(
            {
                "status": "error",
                "message": f"No approved findings in {args.apply_ingest}. "
                "Mark at least one finding `[x] Approved` first.",
            }
        )
        return

    prompt = _build_apply_prompt(approved)
    task = adapter.build_ruleset_editor_task(prompt)
    from utils import get_ingest_workspace

    feed_file = f"{get_ingest_workspace()}/ruleset_editor_result.json"
    feed_cmd = _cmd(
        adapter,
        "--apply-ingest",
        args.apply_ingest,
        "--feed",
        "ruleset_editor",
        "--feed-file",
        feed_file,
    )
    _write_json(
        {
            "status": "need_agent",
            "phase": "ingest_apply",
            "tasks": [adapter.ingest_task_to_spec(task)],
            "on_complete": {
                "feed_as": "ruleset_editor",
                "command": feed_cmd,
                "feed_file": feed_file,
                "note": (
                    "After the agent edits the ruleset files, save a JSON summary "
                    '{"applied": true} to the feed_file and re-run the command '
                    "to record the CHANGELOG entry."
                ),
            },
        }
    )


def _parse_findings_file(path_str: str) -> list:
    """Parse a feedback_findings.md file back into FeedbackFinding objects.

    Extracts status from `- [x] Approved` / `- [x] Rejected` lines, and
    preserves tier, author, target_layers, and proposed_edits so the apply
    prompt carries the actual edit intents.
    """
    import re

    from state import FeedbackFinding

    text = Path(path_str).read_text(encoding="utf-8")
    findings: list[FeedbackFinding] = []
    for block in re.split(r"\n### Finding ", text)[1:]:
        header = block.split("\n", 1)[0].strip()
        m = re.match(r"^(?P<fid>\S+)\s*\((?P<tier>[^)]*)\)", header)
        fid = m.group("fid") if m else ""
        tier = (m.group("tier") if m else "") or "Major"

        status = "pending"
        if "[x] Approved" in block:
            status = "approved"
        elif "[x] Rejected" in block:
            status = "rejected"

        m = re.search(r"\*\*Summary:\*\*\s*(.+)", block)
        summary = m.group(1).strip() if m else ""
        m = re.search(r"\*\*Target layers:\*\*\s*(.+)", block)
        targets = [t.strip() for t in m.group(1).split(",") if t.strip()] if m else []

        author = ""
        m = re.search(r"\*\*Source:\*\*\s*\[([^\]]+)\]", block)
        if m:
            author = m.group(1)
        m = re.search(r"\[(.*?)\]\(([^)]+)\)", block)
        html_url = m.group(2) if m else ""

        edits = []
        for line in block.split("\n"):
            em = re.match(r"\s*-\s*`([^`]+)`\s*:\s*(.*)", line)
            if em:
                layer = em.group(1).strip()
                intent = em.group(2).strip()
                if intent:
                    edits.append({"layer": layer, "intent": intent})

        findings.append(
            FeedbackFinding(
                id=fid,
                comment_id=0,
                pr_number=0,
                author=author,
                html_url=html_url,
                tier=tier,
                summary=summary,
                target_layers=targets,
                proposed_edits=edits,
                status=status,
            )
        )
    return findings


def _build_apply_prompt(approved: list) -> str:
    items = "\n\n".join(
        f"### {f.id} ({f.tier})\n{f.summary}\n\nProposed edits:\n"
        + "\n".join(
            f"  - `{e.get('layer', '?')}`: {e.get('intent', '')}"
            for e in f.proposed_edits
        )
        for f in approved
    )
    return (
        "You are the ruleset editor for the pytorch-test-refactoring skill. "
        "Apply the following approved findings as concrete edits to the "
        "ruleset files. Read each target file first, then make minimal edits "
        "that implement the stated intent. For `verify.py` targets, implement "
        "the described `_check_*` function following the existing check style. "
        "Do not change unrelated content.\n\n"
        f"{items}"
    )


# ── entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    main()
