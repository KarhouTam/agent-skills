"""RefactorFlow — state machine for test refactoring (Claude Code only).

7 phases: assess -> analyze -> distribute -> code -> verify -> review -> finalize

The Flow stops on SPAWN_SINGLE/RELAY_FINDINGS signals. Claude reads the
signal, spawns agents via the Agent tool, feeds results back, and re-enters.

All events are logged to workspace/audit.jsonl and workspace/status.json
for team coordination — the Checker and Team Lead read these to understand
progress and intervene if needed.
"""

import json
from pathlib import Path

from state import (
    RefactorState,
    FlowSignal,
    AnalystReport,
    CoderTask,
    ReviewFindings,
    ClassInfo,
    BoundedRange,
    CoderResult,
    VerificationResult,
)
from utils import (
    get_workspace,
    ANALYST_REPORT_JSON,
    CODER_TASKS_FILE,
    ASSESSMENT_FILE,
    VERIFICATION_FILE,
    REVIEW_FINDINGS_FILE,
    REFACTOR_RULES,
    compute_applicable_rules,
)
from scripts.assess import assess_file
from scripts.verify import verify
from scripts.report import generate_report
from scripts.logger import RefactorLogger
from agent.adapter import BaseAdapter
from agent.claude_code import ClaudeCodeAdapter


_REF_DIR = str(Path(__file__).parent / "reference")


def _finding_matches_rule(finding, rule_id: str) -> bool:
    """Check whether an analyst finding is relevant to a refactoring rule."""
    cat = finding.category
    tgt = finding.target_class
    if rule_id == "strategy_1":
        return bool(
            tgt
            and "Device" not in tgt
            and "CUDA" not in tgt
            and "MPS" not in tgt
            and "XPU" not in tgt
        )
    if rule_id == "strategy_2":
        return cat in ("whitelist", "device_api") or (tgt and "Device" in tgt)
    if rule_id == "strategy_3":
        return (
            cat == "classification"
            and bool(tgt)
            and any(d in tgt for d in ("CUDA", "MPS", "XPU"))
        )
    if rule_id == "cleanup":
        return cat == "stale_import"
    return False


class RefactorFlow:
    """State machine for test file refactoring. Claude Code only."""

    MAX_RETRIES = 3

    def __init__(self, adapter: BaseAdapter | None = None):
        self.adapter = adapter or ClaudeCodeAdapter()
        self.state = RefactorState()
        self.log: RefactorLogger | None = None

    # ── Phase validation ──────────────────────────────────────────
    #
    # Every feed_* method validates that it is called in the correct
    # phase and sub_phase.  This catches orchestrator bugs and prevents
    # the LLM from accidentally skipping a step (e.g. calling
    # feed_coder_result when a checker was expected).

    @staticmethod
    def _validate_phase(expected: str, actual: str, method: str) -> None:
        if actual != expected:
            raise RuntimeError(
                f"{method} must be called in phase '{expected}', got '{actual}'"
            )

    @staticmethod
    def _validate_sub_phase(expected: str, actual: str, method: str) -> None:
        if actual != expected:
            raise RuntimeError(
                f"{method} must be called in sub_phase '{expected}', got '{actual}'"
            )

    def run(self, file_path: str, resume: bool = False) -> RefactorState:
        """Run the flow. Returns when AI spawning is needed; re-enter after.

        On first call, runs phases from the beginning. On subsequent calls
        (after feed_* methods), resumes from the first incomplete phase via
        guard checks on in-memory state.

        Set resume=True to load previously saved artifacts from the workspace,
        enabling cross-process resume (e.g., after a restart).
        """
        self.state.file_path = file_path
        self.state.file_name = Path(file_path).stem
        self.state.workspace = get_workspace(self.state.file_name)
        self.log = RefactorLogger(self.state.workspace)

        # Load artifacts from disk for cross-process resume
        if resume:
            self._load_existing_artifacts()

        is_new_run = not self.state.line_ranges
        if is_new_run:
            self.log.run_start(file_path, self.state.file_name)

        try:
            self._run_phases()
        except Exception as e:
            self.log.error(
                self.state.current_phase,
                type(e).__name__,
                str(e),
            )
            raise
        finally:
            if self.state.signal == FlowSignal.DONE:
                self.log.run_end(self.state.current_phase)

        # Persist transient state-machine position for cross-process resume.
        # Only needed when we're waiting for AI (not when the workflow is done).
        if self.state.signal != FlowSignal.DONE:
            self._save_flow_state()

        return self.state

    # ── Flow state persistence (transient fields) ──────────────────

    _FLOW_STATE_FILE = "flow_state.json"

    def _save_flow_state(self) -> None:
        """Persist transient state-machine fields so the orchestrator can
        resume across process boundaries.

        Saves: current_phase, rule_index, rule_sub_phase, rule_retry,
        retry_count, and signal.
        """
        ws = self.state.workspace
        if ws is None:
            return
        payload = {
            "current_phase": self.state.current_phase,
            "rule_index": self.state.rule_index,
            "rule_sub_phase": self.state.rule_sub_phase,
            "rule_retry": self.state.rule_retry,
            "retry_count": self.state.retry_count,
            "signal": self.state.signal.value,
            "agent_ids": self.state.agent_ids,
        }
        try:
            (ws / self._FLOW_STATE_FILE).write_text(
                json.dumps(payload), encoding="utf-8"
            )
        except Exception:
            pass

    def _load_flow_state(self) -> None:
        """Restore transient state-machine fields from disk.

        Only restores fields that haven't been set yet (guard pattern —
        won't overwrite state from the current session).
        """
        ws = self.state.workspace
        if ws is None:
            return
        path = ws / self._FLOW_STATE_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except Exception:
            return
        # Only restore if the field hasn't been set (guard pattern)
        if self.state.current_phase == "assess":
            self.state.current_phase = data.get("current_phase", "assess")
        if self.state.rule_index == 0:
            self.state.rule_index = data.get("rule_index", 0)
        if self.state.rule_sub_phase == "code":
            self.state.rule_sub_phase = data.get("rule_sub_phase", "code")
        if self.state.rule_retry == 0:
            self.state.rule_retry = data.get("rule_retry", 0)
        if self.state.retry_count == 0:
            self.state.retry_count = data.get("retry_count", 0)
        if not self.state.agent_ids:
            self.state.agent_ids = data.get("agent_ids", {})

    # ── End flow state persistence ─────────────────────────────────

    def _load_existing_artifacts(self):
        """Load previously saved artifacts from workspace.

        Enables resuming a refactoring across process boundaries.
        Only loads artifacts that are not already present in the in-memory
        state (i.e., won't overwrite data from the current session).
        Silently skips artifacts that don't exist or fail to parse.
        """
        ws = self.state.workspace
        if ws is None:
            return

        # Transient state-machine position
        self._load_flow_state()

        # Assessment
        if not self.state.line_ranges:
            assessment_path = ws / ASSESSMENT_FILE
            if assessment_path.exists():
                try:
                    data = json.loads(assessment_path.read_text())
                    self.state.file_size = data.get("file_size", 0)
                    self.state.coder_count = data.get("coder_count", 0)
                    self.state.total_test_count = data.get("total_test_count", 0)
                    self.state.line_ranges = [
                        BoundedRange(**r) for r in data.get("line_ranges", [])
                    ]
                    self.state.class_layout = [
                        ClassInfo(**c) for c in data.get("class_layout", [])
                    ]
                except Exception:
                    pass

        # Analyst report
        if self.state.analyst_report is None:
            report_path = ws / ANALYST_REPORT_JSON
            if report_path.exists():
                try:
                    self.state.analyst_report = AnalystReport.model_validate_json(
                        report_path.read_text()
                    )
                except Exception:
                    pass

        # Coder tasks
        if not self.state.coder_tasks:
            tasks_path = ws / CODER_TASKS_FILE
            if tasks_path.exists():
                try:
                    data = json.loads(tasks_path.read_text())
                    self.state.coder_tasks = [CoderTask(**d) for d in data]
                except Exception:
                    pass

        # Verification
        if self.state.verification is None:
            verif_path = ws / VERIFICATION_FILE
            if verif_path.exists():
                try:
                    self.state.verification = VerificationResult.model_validate_json(
                        verif_path.read_text()
                    )
                except Exception:
                    pass

        # Review findings
        if self.state.review_findings is None:
            review_path = ws / REVIEW_FINDINGS_FILE
            if review_path.exists():
                try:
                    self.state.review_findings = ReviewFindings.model_validate_json(
                        review_path.read_text()
                    )
                except Exception:
                    pass

    def _run_phases(self):
        # Phase 1: Assess (no AI) — skip if already computed
        if not self.state.line_ranges:
            self.log.phase_start("assess")
            self._phase_assess()
            self.log.phase_end(
                "assess",
                status="ok",
                file_size=self.state.file_size,
                coder_count=self.state.coder_count,
                class_count=len(self.state.class_layout),
                total_test_count=sum(c.test_count for c in self.state.class_layout),
                git_dirty=self.state.class_layout[0].test_count > 0
                if self.state.class_layout
                else False,
            )

        # Phase 2: Analyze (spawn analyst)
        if self.state.analyst_report is None:
            self.log.phase_start("analyze")
            self._phase_analyze()
            if self.state.signal != FlowSignal.DONE:
                tasks = self.get_pending_tasks()
                self.log.signal(self.state.signal.value, "analyze", tasks)
                return
            self.log.phase_end(
                "analyze",
                status="ok",
                findings_count=len(self.state.analyst_report.findings),
                classes_classified=len(self.state.analyst_report.strategy_assignments),
            )

        # Phase 3: Distribute (no AI)
        if not self.state.coder_tasks:
            self.log.phase_start("distribute")
            self._phase_distribute()
            self.log.phase_end(
                "distribute",
                status="ok",
                coder_count=self.state.coder_count,
                tasks_distributed=len(self.state.coder_tasks or []),
            )

        # Phase 4: Code-Check loop (per-rule: code → check → fix → check ...)
        if self.state.rule_index < len(self.state.coder_tasks or []):
            if self.state.rule_index == 0 and self.state.rule_sub_phase == "code":
                self.log.phase_start("code")
            self._phase_code()
            if self.state.signal != FlowSignal.DONE:
                tasks = self.get_pending_tasks()
                self.log.signal(self.state.signal.value, "code", tasks)
                return
            success_count = sum(
                1 for r in (self.state.coder_results or {}).values() if r.success
            )
            self.log.phase_end(
                "code",
                status="ok",
                coders_total=len(self.state.coder_results or {}),
                coders_success=success_count,
            )

        # Phase 5: Verify (no AI)
        if self.state.verification is None:
            self.log.phase_start("verify")
            self._phase_verify()
            v = self.state.verification
            self.log.phase_end(
                "verify",
                status="ok" if (v and v.all_passed) else "failed",
                all_passed=v.all_passed if v else False,
                test_count_match=v.test_count_match if v else False,
                checks={c.name: c.passed for c in v.checks} if v else {},
            )

        # Phase 6: Review (spawn checker)
        if self.state.review_findings is None:
            self.log.phase_start("review")
            self._phase_review()
            if self.state.signal != FlowSignal.DONE:
                tasks = self.get_pending_tasks()
                self.log.signal(self.state.signal.value, "review", tasks)
                return
            self.log.phase_end(
                "review",
                status="ok" if self.state.review_findings.all_clear else "issues_found",
                all_clear=self.state.review_findings.all_clear,
                findings_count=len(self.state.review_findings.findings),
            )

        # Phase 7: Finalize (no AI)
        if self.state.final_summary is None:
            self.log.phase_start("finalize")
            self._phase_finalize()
            self.log.phase_end(
                "finalize",
                status="ok",
                summary_length=len(self.state.final_summary or ""),
            )

    # --- Feed methods (called by Claude after agents complete) ---

    def feed_analyst_result(self, report: AnalystReport | None):
        """Called after the analyst agent completes.

        Accepts None if the agent failed. On failure, attempts to load
        an existing analyst report from the workspace as a fallback.
        """
        self._validate_phase("analyze", self.state.current_phase, "feed_analyst_result")
        if report is None:
            self.log.error(
                "analyze", "AgentFailure", "Analyst agent returned no result"
            )
            # Fallback: load from disk if manually placed
            report_path = self.state.workspace / ANALYST_REPORT_JSON
            if report_path.exists():
                try:
                    report = AnalystReport.model_validate_json(report_path.read_text())
                    self.log.agent_completed(
                        "analyze",
                        "analyst",
                        success=True,
                        summary=f"(loaded from disk) {len(report.findings)} findings",
                    )
                except Exception as e:
                    self.log.error("analyze", "FallbackLoadError", str(e))
                    raise RuntimeError(
                        "Analyst agent failed and no valid fallback report "
                        f"found at {report_path}"
                    ) from e
            else:
                raise RuntimeError(
                    "Analyst agent failed and no fallback report found at "
                    f"{report_path}. Place the analyst report there and retry."
                )

        self.state.analyst_report = report
        self.state.signal = FlowSignal.DONE

        # Persist report to disk for cross-process resume
        report_path = self.state.workspace / ANALYST_REPORT_JSON
        try:
            report_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
        except Exception:
            pass

        if self.log:
            self.log.agent_completed(
                "analyze",
                "analyst",
                success=True,
                summary=f"{len(report.findings)} findings, "
                f"{len(report.strategy_assignments)} classes classified",
            )

    def feed_coder_result(self, coder_id: str, result: "CoderResult | None"):
        """Called after a coder completes one rule. Transitions to check sub-phase."""
        self._validate_phase("code", self.state.current_phase, "feed_coder_result")
        self._validate_sub_phase("code", self.state.rule_sub_phase, "feed_coder_result")
        if result is None:
            self.log.error(
                "code",
                "AgentFailure",
                f"Coder {coder_id} returned no result",
            )
            result = CoderResult(
                coder_id=coder_id, success=False, errors=["No result returned"]
            )

        if self.state.coder_results is None:
            self.state.coder_results = {}
        self.state.coder_results[coder_id] = result

        if self.log:
            self.log.agent_completed(
                "code",
                coder_id,
                success=result.success,
                summary=f"{len(result.tests_moved)} tests moved"
                if result.success
                else "; ".join(result.errors[:3]),
            )

        self.state.rule_sub_phase = "check"
        self.state.signal = FlowSignal.DONE

    def feed_rule_check_result(self, passed: bool):
        """Called after per-rule checker completes.

        If passed and more rules: advance rule_index, signal SEND_MESSAGE
          for the orchestrator to message the coder with the next rule.
        If passed and done: transition to verify phase, signal DONE.
        If failed: enter fix sub-phase, signal SEND_MESSAGE for fix request.
        """
        self._validate_phase("code", self.state.current_phase, "feed_rule_check_result")
        self._validate_sub_phase(
            "check", self.state.rule_sub_phase, "feed_rule_check_result"
        )
        if passed:
            self.state.rule_retry = 0
            self.state.rule_index += 1
            self.state.rule_sub_phase = "code"
            if self.state.rule_index >= len(self.state.coder_tasks or []):
                self.state.current_phase = "verify"
                self.state.signal = FlowSignal.DONE
            else:
                # Orchestrator will message the coder with next rule
                self.state.signal = FlowSignal.DONE
        else:
            self.state.rule_retry += 1
            if self.state.rule_retry > self.MAX_RETRIES:
                self.log.error(
                    "code",
                    "MaxRetries",
                    f"Rule {self.state.rule_index} failed after "
                    f"{self.MAX_RETRIES} fix attempts; continuing",
                )
                self.state.rule_retry = 0
                self.state.rule_index += 1
                self.state.rule_sub_phase = "code"
                if self.state.rule_index >= len(self.state.coder_tasks or []):
                    self.state.current_phase = "verify"
                self.state.signal = FlowSignal.DONE
            else:
                self.state.rule_sub_phase = "fix"
                self.state.signal = FlowSignal.DONE

    def feed_rule_fix_result(self, coder_id: str, result: "CoderResult | None"):
        """Called after coder fixes per-rule checker findings. Goes back to check."""
        self._validate_phase("code", self.state.current_phase, "feed_rule_fix_result")
        self._validate_sub_phase(
            "fix", self.state.rule_sub_phase, "feed_rule_fix_result"
        )
        if result is None:
            result = CoderResult(
                coder_id=coder_id, success=False, errors=["No result returned"]
            )
        if self.state.coder_results is None:
            self.state.coder_results = {}
        self.state.coder_results[coder_id] = result
        self.state.rule_sub_phase = "check"
        self.state.signal = FlowSignal.DONE

    def feed_review_findings(self, findings: ReviewFindings):
        """Called after the checker completes."""
        self._validate_phase("review", self.state.current_phase, "feed_review_findings")
        self.state.review_findings = findings
        if self.log:
            self.log.agent_completed(
                "review",
                "checker",
                success=True,
                summary="all clear"
                if findings.all_clear
                else f"{len(findings.findings)} issues",
            )
            for f_item in findings.findings:
                self.log.review_finding(
                    f_item.severity,
                    f_item.category,
                    f_item.description,
                    f_item.coder_responsible,
                )
            self.log.phase_end(
                "review",
                status="ok" if findings.all_clear else "issues_found",
                all_clear=findings.all_clear,
                findings_count=len(findings.findings),
            )

        if findings.all_clear:
            self.state.signal = FlowSignal.DONE
        else:
            self.state.current_phase = "fix"
            self.state.signal = FlowSignal.RELAY_FINDINGS

    def feed_agent_spawned(self, agent_name: str, agent_id: str):
        """Register an agent ID after spawning.

        Must be called BEFORE the corresponding feed_*_result method
        so that subsequent SEND_MESSAGE signals can use the correct ID.

        Idempotent: if the agent_name already has a registered ID, the
        new ID overwrites it (handles re-spawn on fallback).
        """
        self.state.agent_ids[agent_name] = agent_id
        # Persist immediately so cross-process resume has the ID
        self._save_flow_state()

    def feed_fix_complete(self):
        """Called after coders finish fixing review issues."""
        self._validate_phase("fix", self.state.current_phase, "feed_fix_complete")
        self.state.retry_count += 1
        self._phase_verify()
        v = self.state.verification
        passed = v.all_passed if v else False
        if self.log:
            self.log.fix_round(self.state.retry_count, passed)
        if passed:
            self.state.signal = FlowSignal.DONE
        elif self.state.retry_count < self.MAX_RETRIES:
            self._phase_review()
        else:
            if self.log:
                self.log.error(
                    "review", "MaxRetries", f"Max retries ({self.MAX_RETRIES}) exceeded"
                )
            self.state.signal = FlowSignal.DONE

    # --- Private phase methods ---

    def _phase_assess(self):
        self.state.current_phase = "assess"
        result = assess_file(self.state.file_path)
        self.state.file_size = result.file_size
        self.state.coder_count = result.coder_count
        self.state.total_test_count = result.total_test_count
        self.state.line_ranges = result.line_ranges
        self.state.class_layout = result.class_layout
        self.state.signal = FlowSignal.DONE

    def _phase_analyze(self):
        self.state.current_phase = "analyze"
        self.state.signal = FlowSignal.SPAWN_SINGLE

    def _phase_distribute(self):
        """Distribute: own sharding — convert analyst strategy assignments into coder tasks.

        Sharding is deterministic (no AI): one coder per applicable refactoring rule.
        The analyst owns classification (what strategy each test needs);
        distribute owns sharding (how to divide the work across coders).
        """
        self.state.current_phase = "distribute"
        if self.state.analyst_report is None:
            report_path = self.state.workspace / ANALYST_REPORT_JSON
            if report_path.exists():
                self.state.analyst_report = AnalystReport.model_validate_json(
                    report_path.read_text()
                )

        if self.state.analyst_report is None:
            raise RuntimeError("Analyst report not available for distribute phase")

        report = self.state.analyst_report
        rules = compute_applicable_rules(report.strategy_assignments)
        self.state.coder_count = len(rules)

        coder_tasks: list[CoderTask] = []
        for i, rule_id in enumerate(rules):
            rule_desc = REFACTOR_RULES.get(rule_id, rule_id)
            rule_findings = [
                f for f in report.findings if _finding_matches_rule(f, rule_id)
            ]
            if rule_findings:
                instructions = "\n".join(
                    f"- L{f.line_number}: [{f.severity}] {f.description}"
                    f" -> {f.recommendation}"
                    for f in rule_findings
                )
            else:
                instructions = (
                    f"No specific findings for rule '{rule_id}'. "
                    f"Apply the rule across the entire file."
                )
            coder_tasks.append(
                CoderTask(
                    coder_id=f"coder-{i + 1}",
                    rule=rule_id,
                    rule_description=rule_desc,
                    action_items=rule_findings,
                    instructions=instructions,
                )
            )

        self.state.coder_tasks = coder_tasks

        (self.state.workspace / CODER_TASKS_FILE).write_text(
            json.dumps([ct.model_dump() for ct in coder_tasks], indent=2, default=str),
            encoding="utf-8",
        )

        self.state.signal = FlowSignal.DONE

    def _phase_code(self):
        self.state.current_phase = "code"
        if not self.state.coder_tasks:
            raise RuntimeError("No coder tasks to execute")

        if self.state.rule_sub_phase == "code":
            # Apply rule: spawn coder (first rule) or message existing (subsequent)
            if self.state.rule_index == 0:
                self.state.signal = FlowSignal.SPAWN_SINGLE
            else:
                self.state.signal = FlowSignal.SEND_MESSAGE
        elif self.state.rule_sub_phase == "check":
            # Per-rule check: spawn checker agent to verify this rule
            self.state.signal = FlowSignal.SPAWN_SINGLE
        elif self.state.rule_sub_phase == "fix":
            # Per-rule fix: message coder with fix instructions
            self.state.signal = FlowSignal.SEND_MESSAGE

    def _phase_verify(self):
        self.state.current_phase = "verify"
        # Use assessment's count (deterministic) over analyst's (may undercount).
        # Fall back to analyst report only if assessment didn't run (resume edge case).
        original_count = self.state.total_test_count or (
            self.state.analyst_report.original_test_count
            if self.state.analyst_report
            else 0
        )
        original_classes = (
            list(self.state.analyst_report.class_mapping.keys())
            if self.state.analyst_report
            else []
        )

        result = verify(self.state.file_path, original_count, original_classes)
        self.state.verification = result
        self.state.signal = FlowSignal.DONE

    def _phase_review(self):
        self.state.current_phase = "review"
        self.state.review_findings = (
            None  # clear prior findings so guard re-opens on retry
        )
        self.state.signal = FlowSignal.SPAWN_SINGLE

    def _phase_finalize(self):
        self.state.current_phase = "finalize"
        self.state.final_summary = generate_report(self.state)
        self.state.signal = FlowSignal.DONE

    # --- Helper: get agent tasks for current phase ---

    def get_pending_tasks(self) -> list:
        """Return AgentTask objects for the current phase.

        Called after the flow stops on a spawn signal.
        Claude reads these tasks and spawns the corresponding agents.
        """
        workspace = str(self.state.workspace)
        file_path = self.state.file_path

        if self.state.current_phase == "analyze":
            return [
                self.adapter.build_analyst_task(
                    file_path,
                    workspace,
                    _REF_DIR,
                )
            ]

        elif self.state.current_phase == "code":
            tasks = self.state.coder_tasks or []
            idx = self.state.rule_index
            if idx >= len(tasks):
                return []

            ct = tasks[idx]

            if self.state.rule_sub_phase == "code":
                if idx == 0:
                    # First rule: spawn coder agent (name="coder")
                    return self.adapter.build_coder_tasks(
                        file_path,
                        workspace,
                        [ct],
                        self.state.analyst_report.strategy_assignments
                        if self.state.analyst_report
                        else {},
                        first_spawn=True,
                        total_rules=len(tasks),
                    )
                else:
                    # Subsequent rule: message existing coder
                    return [
                        self.adapter.build_send_message(
                            to="coder",
                            agent_id=self.state.agent_ids.get("coder", ""),
                            message_type="next_rule",
                            rule=ct.rule,
                            rule_description=ct.rule_description,
                            instructions=ct.instructions,
                        )
                    ]

            elif self.state.rule_sub_phase == "check":
                result = (self.state.coder_results or {}).get(ct.coder_id)
                result_summary = (
                    f"{len(result.tests_moved)} tests moved"
                    if result and result.success
                    else ("; ".join(result.errors[:3]) if result else "no result")
                )
                return [
                    self.adapter.build_checker_task(
                        file_path,
                        workspace,
                        _REF_DIR,
                        self.state.analyst_report.original_test_count
                        if self.state.analyst_report
                        else 0,
                        "",
                        scope="rule",
                        rule_context={
                            "rule": ct.rule,
                            "rule_description": ct.rule_description,
                            "instructions": ct.instructions,
                            "result_summary": result_summary,
                        },
                    )
                ]

            elif self.state.rule_sub_phase == "fix":
                return [
                    self.adapter.build_send_message(
                        to="coder",
                        agent_id=self.state.agent_ids.get("coder", ""),
                        message_type="fix",
                        rule=ct.rule,
                        rule_description=ct.rule_description,
                        instructions=ct.instructions,
                    )
                ]

        elif self.state.current_phase == "review":
            verification_summary = ""
            if self.state.verification:
                verification_summary = "\n".join(
                    f"- [{'PASS' if c.passed else 'FAIL'}] {c.name}: {c.details}"
                    for c in self.state.verification.checks
                )
            original_count = (
                self.state.analyst_report.original_test_count
                if self.state.analyst_report
                else 0
            )
            return [
                self.adapter.build_checker_task(
                    file_path,
                    workspace,
                    _REF_DIR,
                    original_count,
                    verification_summary,
                )
            ]

        elif self.state.current_phase == "fix":
            if self.state.review_findings:
                return self.adapter.build_fix_tasks(
                    file_path,
                    workspace,
                    self.state.review_findings.findings,
                    agent_ids=self.state.agent_ids,
                )
            return []

        return []
