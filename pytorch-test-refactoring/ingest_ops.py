"""State machine for the PR feedback ingest sidecar module.

Mirrors CIOps: deterministic harvest, then AI-agent phases for triage
and drafting, emitting FlowSignal values that the orchestrator turns
into Agent/SendMessage tool calls. Separate from RefactorFlow and
CIOps — this module does not touch the refactoring phases.

Unlike the single-shot plan sketch, the transient machine state is
persisted to agent_space/ingest/flow_state.json and `harvest` defers
marking comments processed until `finalize()`. This is what lets the
triage -> draft handoff resume correctly across orchestrator invocations
(the daily cron runs a fresh `python orchestrator.py --ingest-feedback`
each time).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from state import FlowSignal, FeedbackComment, FeedbackFinding
from scripts import ingest
from utils import get_ingest_workspace, INGEST_FLOW_STATE_FILE
from agent.adapter import AgentTask
from agent.claude_code import ClaudeCodeAdapter


class IngestStateMachine(BaseModel):
    """Transient runtime position of the ingest state machine (persisted)."""

    phase: str = "harvest"  # "harvest" | "triage" | "draft" | "done"
    fresh_comments: list[FeedbackComment] = []
    triaged: dict[int, dict] = {}  # comment_id -> triage decision
    draft_queue: list[int] = []  # comment_ids awaiting a draft agent
    pending_findings: list[FeedbackFinding] = []
    signal: FlowSignal = FlowSignal.DONE


class IngestOps:
    """State machine for the feedback ingest sidecar. Same pattern as CIOps."""

    def __init__(self) -> None:
        self.state = IngestStateMachine()
        self.adapter = ClaudeCodeAdapter()

    def run(self) -> IngestStateMachine:
        """Advance the state machine through deterministic work until an AI step."""
        self._load_machine_state()

        if self.state.phase == "harvest":
            fresh, _ = ingest.harvest()
            self.state.fresh_comments = fresh
            if not fresh:
                self.state.phase = "done"
                self.state.signal = FlowSignal.DONE
            else:
                self.state.phase = "triage"
                self.state.signal = FlowSignal.SPAWN_SINGLE
        elif self.state.phase == "triage":
            self.state.signal = FlowSignal.SPAWN_SINGLE
        elif self.state.phase == "draft":
            if self.state.draft_queue:
                self.state.signal = FlowSignal.SPAWN_SINGLE
            else:
                self.state.phase = "done"
                self.state.signal = FlowSignal.DONE
        else:  # done
            self.state.signal = FlowSignal.DONE

        self._save_machine_state()
        return self.state

    def get_pending_tasks(self) -> list[AgentTask]:
        """Return the AgentTask(s) for the current AI step."""
        if self.state.phase == "triage":
            return [self.adapter.build_feedback_triage_task(self.state.fresh_comments)]
        if self.state.phase == "draft" and self.state.draft_queue:
            cid = self.state.draft_queue[0]
            comment = self._find_comment(cid)
            if comment is not None:
                triage = self.state.triaged.get(cid, {})
                return [
                    self.adapter.build_feedback_analyst_task(
                        {"comment": comment, "triage": triage}
                    )
                ]
        return []

    def feed_triage_result(self, data: dict[str, Any]) -> None:
        """Consume triage output; build the draft queue of relevant comments."""
        decisions = data.get("decisions", [])
        relevant_ids: list[int] = []
        for d in decisions:
            cid = d.get("comment_id", 0)
            if cid:
                self.state.triaged[cid] = d
            if d.get("relevant") and not d.get("already_fixed"):
                if self._find_comment(cid) is not None:
                    relevant_ids.append(cid)
        self.state.draft_queue = relevant_ids
        if relevant_ids:
            self.state.phase = "draft"
            self.state.signal = FlowSignal.SPAWN_SINGLE
        else:
            self.state.phase = "done"
            self.state.signal = FlowSignal.DONE
        self._save_machine_state()

    def feed_draft_result(self, data: dict[str, Any]) -> None:
        """Consume one analyst draft; append a finding and advance the queue."""
        payload = data.get("finding", data)
        finding = FeedbackFinding(**payload)
        finding.status = "pending"
        self.state.pending_findings.append(finding)
        if self.state.draft_queue:
            self.state.draft_queue = self.state.draft_queue[1:]
        if self.state.draft_queue:
            self.state.phase = "draft"
            self.state.signal = FlowSignal.SPAWN_SINGLE
        else:
            self.state.phase = "done"
            self.state.signal = FlowSignal.DONE
        self._save_machine_state()

    def finalize(self) -> None:
        """Mark harvested comments processed and clear the machine state.

        Called once the pipeline reaches DONE. `finalize_harvest` bumps the
        per-PR cursor and processed ids, then the persisted flow_state.json
        is removed so the next run starts fresh at harvest.
        """
        if self.state.fresh_comments:
            ingest.finalize_harvest(self.state.fresh_comments)
        path = get_ingest_workspace() / INGEST_FLOW_STATE_FILE
        if path.exists():
            path.unlink()

    def _find_comment(self, comment_id: int) -> FeedbackComment | None:
        for c in self.state.fresh_comments:
            if c.comment_id == comment_id:
                return c
        return None

    def _load_machine_state(self) -> None:
        path = get_ingest_workspace() / INGEST_FLOW_STATE_FILE
        if not path.exists():
            return
        try:
            self.state = IngestStateMachine.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            self.state = IngestStateMachine()

    def _save_machine_state(self) -> None:
        path = get_ingest_workspace() / INGEST_FLOW_STATE_FILE
        path.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
