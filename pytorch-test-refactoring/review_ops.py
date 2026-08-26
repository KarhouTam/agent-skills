"""State machine for the PR review queue sidecar module.

select -> review -> publish. The review phase is harness-dependent:

* Claude Code: the orchestrator emits one reviewer sub-agent per PR, in waves
  of up to WAVE_SIZE, because Claude's Agent tool delivers the delegated task
  correctly.
* Codex: the orchestrator emits ONE inline instruction that the executor (the
  main agent) follows itself. Codex MultiAgentV2 records the spawn_agent task
  `message` as an assistant/commentary mailbox envelope rather than a user/task
  message (openai/codex#25458), so spawned reviewers ignore their assignment
  and re-run the orchestrator.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agent.harness import AgentTask
from agent.tasks import build_reviewer_task, _load_prompt
from scripts import review_queue
from state import FlowSignal, PrReviewResult, ReviewOpsState
from utils import PR_REVIEW_FLOW_STATE_FILE, get_pr_review_workspace

_REVIEW_SKILL = (
    Path(__file__).resolve().parent
    / "agent"
    / "skills"
    / "review-test-refactoring"
    / "SKILL.md"
)
WAVE_SIZE = 4
BATCH_RESULT_FILE = "_review_batch_done.json"


class ReviewOps:
    """State machine for the daily PR review queue."""

    def __init__(self, limit: int = 10, supports_delegated_agents: bool = True) -> None:
        self.limit = limit
        self.mode = "subagents" if supports_delegated_agents else "inline"
        self.state = ReviewOpsState()

    # ── advance ────────────────────────────────────────────────────

    def run(self, resume: bool = False) -> ReviewOpsState:
        """Advance through deterministic work until an AI step or completion."""
        if resume:
            self._load_machine_state()
        self._advance()
        self._save_machine_state()
        return self.state

    def _advance(self) -> None:
        while True:
            if self.state.phase == "select":
                self._select()
                if self.state.phase == "review":
                    return  # agent step needed (inline instruction or sub-agent wave)
                continue  # publish or done
            if self.state.phase == "review":
                if self.mode == "inline":
                    self.state.signal = FlowSignal.SPAWN_SINGLE
                    return
                self._review_tick()
                if self.state.signal in (
                    FlowSignal.WAITING,
                    FlowSignal.SPAWN_PARALLEL,
                ):
                    return
                continue  # phase became publish
            if self.state.phase == "publish":
                self._publish()
                return
            self.state.signal = FlowSignal.DONE
            return

    def _select(self) -> None:
        sel = review_queue.select_pending(limit=self.limit)
        self.state.review_queue = sel.review_queue
        self.state.not_applicable = sel.not_applicable
        self.state.failed = sel.failed
        if self.state.review_queue:
            self.state.phase = "review"
            if self.mode == "subagents":
                self._take_wave()
            else:
                self.state.signal = FlowSignal.SPAWN_SINGLE
        elif self.state.not_applicable:
            self.state.phase = "publish"
            self.state.signal = FlowSignal.DONE
        else:
            self.state.phase = "done"
            self.state.signal = FlowSignal.DONE

    def _review_tick(self) -> None:
        if self.state.in_flight:
            self.state.signal = FlowSignal.WAITING
        elif self.state.review_queue:
            self._take_wave()
        else:
            self.state.phase = "publish"
            self.state.signal = FlowSignal.DONE

    def _take_wave(self) -> None:
        wave = self.state.review_queue[:WAVE_SIZE]
        self.state.review_queue = self.state.review_queue[WAVE_SIZE:]
        self.state.in_flight = wave
        self.state.signal = FlowSignal.SPAWN_PARALLEL

    # ── tasks (sub-agent mode) ─────────────────────────────────────

    def get_pending_tasks(self) -> list[AgentTask]:
        """Return one reviewer task per in-flight PR (Claude mode only)."""
        ws = str(get_pr_review_workspace())
        tasks: list[AgentTask] = []
        for item in self.state.in_flight:
            result_file = str(
                get_pr_review_workspace() / f"pr_{item.pr_number}_result.json"
            )
            tasks.append(build_reviewer_task(item, ws, result_file))
        return tasks

    # ── inline instruction (Codex mode) ────────────────────────────

    def get_inline_instruction(self, feed_file: str, feed_cmd: str) -> str:
        """Return the executor-facing instruction for reviewing the whole batch."""
        pr_list = "\n".join(
            f"- PR #{item.pr_number} — {item.title} — @{item.author} — {item.url}"
            for item in self.state.review_queue
        )
        return _load_prompt("reviewer_batch").format(
            pr_list=pr_list,
            workspace=str(get_pr_review_workspace()),
            review_skill_path=str(_REVIEW_SKILL),
            feed_file=feed_file,
            feed_cmd=feed_cmd,
        )

    # ── feed & publish ─────────────────────────────────────────────

    def feed_reviewer_result(
        self,
        data: dict[str, Any],
        feed_file: str = "",
    ) -> None:
        """Consume reviewer output according to the active review mode."""
        if self.mode == "inline":
            self._feed_inline_result()
        else:
            self._feed_subagent_result(data, feed_file)
        self._save_machine_state()

    def _feed_inline_result(self) -> None:
        workspace = get_pr_review_workspace()
        for item in self.state.review_queue:
            result = self._load_result(
                workspace / f"pr_{item.pr_number}_result.json",
                item.pr_number,
            )
            if result is not None and result.success:
                self.state.results[str(item.pr_number)] = result
        self.state.review_queue = []
        self.state.phase = "publish"
        self.state.signal = FlowSignal.DONE

    def _feed_subagent_result(self, data: dict[str, Any], feed_file: str) -> None:
        result = self._parse_result(data, feed_file)
        if result.pr_number and result.success:
            self.state.results[str(result.pr_number)] = result
        if result.pr_number:
            self.state.in_flight = [
                item
                for item in self.state.in_flight
                if item.pr_number != result.pr_number
            ]
        elif self.state.in_flight:
            self.state.in_flight = self.state.in_flight[1:]

    def _parse_result(self, data: dict[str, Any], feed_file: str) -> PrReviewResult:
        try:
            return PrReviewResult.model_validate(data if isinstance(data, dict) else {})
        except Exception:
            pass
        m = re.search(r"pr_(\d+)_result\.json", feed_file or "")
        if m:
            return PrReviewResult(
                pr_number=int(m.group(1)), success=False, error="invalid_result_json"
            )
        return PrReviewResult(success=False, error="invalid_result_json")

    def _load_result(self, path: Path, pr_number: int) -> PrReviewResult | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        data.setdefault("pr_number", pr_number)
        try:
            return PrReviewResult.model_validate(data)
        except Exception:
            return None

    def _publish(self) -> None:
        workspace = get_pr_review_workspace()
        if not self.state.comment_url:
            reviewed = [
                self.state.results[key] for key in sorted(self.state.results, key=int)
            ]
            published = review_queue.publish_batch(
                workspace,
                reviewed,
                self.state.not_applicable,
            )
            self.state.comment_url = published["comment_url"]
            self.state.comment_path = published["comment_path"]
        self.state.phase = "done"
        self.state.signal = FlowSignal.DONE

    def finalize(self) -> None:
        """Clear the persisted machine state after a completed run."""
        path = get_pr_review_workspace() / PR_REVIEW_FLOW_STATE_FILE
        if path.exists():
            path.unlink()

    # ── persistence ────────────────────────────────────────────────

    def _load_machine_state(self) -> None:
        path = get_pr_review_workspace() / PR_REVIEW_FLOW_STATE_FILE
        if not path.exists():
            return
        try:
            self.state = ReviewOpsState.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            self.state = ReviewOpsState()

    def _save_machine_state(self) -> None:
        path = get_pr_review_workspace() / PR_REVIEW_FLOW_STATE_FILE
        path.write_text(self.state.model_dump_json(indent=2), encoding="utf-8")
