#!/usr/bin/env python3
"""Deterministic orchestrator for the test refactoring workflow.

Replaces the LLM-driven while-loop in SKILL.md. The LLM's role is reduced
to a simple executor: run this script, read the JSON task spec on stdout,
execute the instructions (spawn agent or send message), extract key info
from the agent's output, and feed it back via stdin.

Usage:
    # Start a new refactoring
    python orchestrator.py test/test_ops.py

    # Feed agent result and continue
    python orchestrator.py test/test_ops.py --feed coder < result.json

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
import sys
from pathlib import Path
from typing import Any

# Ensure the skill directory is on sys.path so imports work when the
# script is invoked from any working directory.
_skill_dir = str(Path(__file__).resolve().parent)
if _skill_dir not in sys.path:
    sys.path.insert(0, _skill_dir)

from flow import RefactorFlow
from state import (
    FlowSignal,
    AnalystReport,
    CoderResult,
    ReviewFindings,
    RefactorState,
)
from utils import ANALYST_REPORT_JSON


# ── argument parsing ──────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PyTorch Test Refactoring Orchestrator"
    )
    parser.add_argument(
        "file_path",
        help="Path to the test file to refactor (e.g. test/test_ops.py)",
    )
    parser.add_argument(
        "--feed",
        choices=["analyst", "coder", "checker"],
        help=(
            "Feed agent output back to the orchestrator. "
            "Reads a JSON object from stdin describing the agent result."
        ),
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from workspace artifacts on disk (cross-process resume).",
    )
    return parser.parse_args()


# ── main entry point ──────────────────────────────────────────────


def main() -> None:
    args = _parse_args()

    flow = RefactorFlow()

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
        raw = _read_stdin()
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
    _write_json(
        {
            "status": "done",
            "phase": state.current_phase,
            "summary_path": f"{ws}/final_summary.md" if ws else "",
            "workspace": ws,
        }
    )


def _emit_tasks(
    flow: RefactorFlow,
    state: RefactorState,
    tasks: list[Any],
    signal: FlowSignal,
) -> None:
    """Emit the 'need_agent' task spec."""
    task_specs = [_task_to_spec(t, signal, state) for t in tasks]
    feed_as = _feed_type_for(state)

    # Build the resume command
    file_path = state.file_path
    cmd = f"python orchestrator.py {file_path} --feed {feed_as}"

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
                "command": cmd,
                "note": (
                    "1. Read the agent's output. "
                    "2. Extract key result into JSON (see formats below). "
                    "3. If you spawned a NEW agent (method=spawn), include "
                    '"agent_id" (from the Agent tool result) and '
                    '"agent_name" (from the task spec) in the JSON. '
                    "4. Pipe the JSON to this command:  "
                    f"echo '{{...}}' | {cmd}"
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


def _write_json(obj: dict[str, Any]) -> None:
    """Write a JSON object to stdout followed by a newline."""
    json.dump(obj, sys.stdout, indent=2)
    sys.stdout.write("\n")
    sys.stdout.flush()


# ── task spec builders ────────────────────────────────────────────


def _task_to_spec(
    task: Any, signal: FlowSignal, state: RefactorState
) -> dict[str, Any]:
    """Convert an AgentTask into a JSON spec the LLM can execute directly.

    The spec tells the LLM:
      - method:  "spawn" (new Agent tool call) or "send_message" (to running agent)
      - All parameters for the operation
      - A fallback spawn spec for send_message (agent may have died)
    """
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
        # Use the registered agent_id (e.g. "a3fa28753cd227df1") as the
        # primary target for SendMessage.  Falls back to agent_name if no
        # ID has been registered yet (triggers the fallback spawn below).
        target_id = getattr(task, "agent_id", "") or task.context.get(
            "send_message_to", task.agent_name
        )
        spec["send_to"] = target_id
        # Fallback: if the agent has died (or no ID registered yet),
        # spawn a replacement.  The LLM MUST include agent_id + agent_name
        # in the result JSON so the orchestrator can register the new ID.
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


# ── entry point ───────────────────────────────────────────────────

if __name__ == "__main__":
    main()
