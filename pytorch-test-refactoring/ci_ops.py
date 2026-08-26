"""CIOps - state machine for CI automation (Phase 8).

2 phases: monitor -> debug (loop) -> done

PR creation and pushing is handled by the user. This state machine
only monitors CI, classifies failures, and spawns debugger agents.

Follows the same conventions as flow.py: a class with run(),
get_pending_tasks(), and feed_*() methods. Reads/writes the
same workspace as the refactoring workflow.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from state import (
    CIState,
    CICheckRun,
    CIBotHints,
    CIFailure,
    FlowSignal,
    RefactorState,
)
from scripts import ci as ci_ops_module
from agent.tasks import build_debugger_task

_CI_STATE_FILE = "ci_state.json"


class CIOps:
    """State machine for CI automation. Same pattern as RefactorFlow."""

    def __init__(self):
        self.state: CIState = CIState()

    def run(
        self,
        file_path: str,
        workspace_dir: str,
        refactor_state: RefactorState | None = None,
        resume: bool = False,
        pr_number: int | None = None,
        pr_branch: str = "",
        head_sha: str = "",
    ) -> CIState:
        """Run the CI ops state machine.

        On first call, detects or accepts PR info and transitions to monitor.
        On subsequent calls (--ci-check cron fires), monitors CI state.

        Set resume=True to load previously saved CI state from workspace.
        Pass pr_number/pr_branch/head_sha to skip auto-detection.
        """
        self.state.file_path = file_path
        self.state.workspace = Path(workspace_dir)
        self.state.workspace.mkdir(parents=True, exist_ok=True)

        if resume:
            self._load_ci_state()

        # Accept externally provided PR info (from CLI args or detection)
        if pr_number is not None and not self.state.pr_number:
            self.state.pr_number = pr_number
        if pr_branch and not self.state.pr_branch:
            self.state.pr_branch = pr_branch
        if head_sha and not self.state.head_sha:
            self.state.head_sha = head_sha

        # Auto-detect PR info from git if still missing
        if not self.state.pr_number or not self.state.pr_branch:
            self._detect_pr_info()

        self._run_phases()

        if self.state.signal != FlowSignal.DONE:
            self._save_ci_state()

        return self.state

    def get_pending_tasks(self) -> list:
        """Return AgentTask objects for the current phase."""
        workspace = str(self.state.workspace) if self.state.workspace else ""
        file_path = self.state.file_path

        if self.state.ci_phase == "debug":
            # Write intermediate JSON files for the debugger to read.
            # This keeps the agent prompt lean — the agent reads these
            # files on demand instead of carrying all data inline.
            ws_path = self.state.workspace
            if ws_path and self.state.failures:
                ci_ops_module.write_ci_failures_json(self.state.failures, ws_path)
            if ws_path and self.state.bot_hints:
                ci_ops_module.write_bot_comment_json(self.state.bot_hints, ws_path)

            return [
                build_debugger_task(
                    file_path=file_path,
                    workspace=workspace,
                )
            ]

        return []

    def feed_debugger_result(self, result: dict) -> None:
        """Called after the debugger agent completes.

        Records fixes and loops back to monitor phase.
        """
        if self.state.ci_phase != "debug":
            raise RuntimeError(
                f"feed_debugger_result must be called in phase 'debug', "
                f"got '{self.state.ci_phase}'"
            )

        fixes = result.get("fixes_applied", [])
        for fix in fixes:
            self.state.fix_history.append(fix.get("change", "unknown fix"))

        # Get new HEAD SHA after debugger push
        try:
            new_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.state.head_sha = new_sha
        except Exception:
            pass

        if len(self.state.fix_history) >= self.state.max_fix_rounds:
            self.state.ci_phase = "done"
            self.state.signal = FlowSignal.DONE
        else:
            self.state.ci_phase = "monitor"
            self.state.signal = FlowSignal.DONE

    # -- Private phase methods --

    def _run_phases(self):
        """Run through CI phases with guard checks."""
        if self.state.ci_phase == "monitor":
            self._phase_monitor()
            if self.state.signal != FlowSignal.DONE:
                return

        if self.state.ci_phase == "debug":
            self._phase_debug()
            if self.state.signal != FlowSignal.DONE:
                return

    def _detect_pr_info(self) -> None:
        """Auto-detect PR number, branch, and head SHA from git.

        Uses the current branch to find an open PR.  Non-fatal: if
        detection fails, the state is left unchanged and the caller
        should provide the info explicitly.
        """
        try:
            current_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        except Exception:
            return

        if current_branch and current_branch != "HEAD":
            if not self.state.pr_branch:
                self.state.pr_branch = current_branch

        # Detect PR number from branch
        if not self.state.pr_number and self.state.pr_branch:
            try:
                result = subprocess.run(
                    [
                        "gh",
                        "pr",
                        "list",
                        "--head",
                        self.state.pr_branch,
                        "--json",
                        "number,url",
                        "--jq",
                        ".[0]",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if result.stdout.strip():
                    pr_data = json.loads(result.stdout)
                    self.state.pr_number = pr_data.get("number")
                    self.state.pr_url = pr_data.get("url", "")
            except Exception:
                pass

        # Detect HEAD SHA
        if not self.state.head_sha:
            try:
                self.state.head_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout.strip()
            except Exception:
                pass

    def _phase_monitor(self):
        """8b: Fetch CI state and classify."""
        self.state.ci_phase = "monitor"

        check_runs = ci_ops_module.get_check_runs(self.state.head_sha)
        self.state.check_runs = check_runs

        if self.state.pr_number:
            raw = ci_ops_module.get_bot_comment(self.state.pr_number)
            self.state.bot_hints = ci_ops_module.parse_bot_comment(raw)
        else:
            self.state.bot_hints = CIBotHints()

        verdict, failures = ci_ops_module.classify_ci_state(
            check_runs, self.state.bot_hints
        )
        self.state.failures = failures

        if verdict in ("all_pass", "all_excused"):
            self.state.ci_phase = "done"
            self.state.signal = FlowSignal.DONE
        elif verdict == "failures":
            self.state.ci_phase = "debug"
            self.state.signal = FlowSignal.SPAWN_SINGLE
        elif verdict == "pending":
            self.state.ci_phase = "monitor"
            self.state.signal = FlowSignal.WAITING
        else:
            self.state.ci_phase = "done"
            self.state.signal = FlowSignal.DONE

    def _phase_debug(self):
        """8c: Need to debug failures."""
        self.state.ci_phase = "debug"
        self.state.signal = FlowSignal.SPAWN_SINGLE

    # -- Persistence --

    def _save_ci_state(self) -> None:
        """Persist CI state to workspace for cross-process resume."""
        ws = self.state.workspace
        if ws is None:
            return
        payload = {
            "file_path": self.state.file_path,
            "pr_number": self.state.pr_number,
            "pr_url": self.state.pr_url,
            "pr_branch": self.state.pr_branch,
            "head_sha": self.state.head_sha,
            "ci_phase": self.state.ci_phase,
            "fix_history": self.state.fix_history,
            "max_fix_rounds": self.state.max_fix_rounds,
            "cron_job_id": self.state.cron_job_id,
            "signal": self.state.signal.value,
        }
        try:
            (ws / _CI_STATE_FILE).write_text(
                json.dumps(payload, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _load_ci_state(self) -> None:
        """Restore CI state from workspace."""
        ws = self.state.workspace
        if ws is None:
            return
        path = ws / _CI_STATE_FILE
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except Exception:
            return

        if self.state.pr_number is None:
            self.state.pr_number = data.get("pr_number")
        if not self.state.pr_url:
            self.state.pr_url = data.get("pr_url", "")
        if not self.state.pr_branch:
            self.state.pr_branch = data.get("pr_branch", "")
        if not self.state.head_sha:
            self.state.head_sha = data.get("head_sha", "")
        if self.state.ci_phase == "monitor" and not self.state.pr_number:
            # First load: restore saved phase (may be deeper in the flow)
            self.state.ci_phase = data.get("ci_phase", "monitor")
        if not self.state.fix_history:
            self.state.fix_history = data.get("fix_history", [])
        if self.state.max_fix_rounds == 5:
            self.state.max_fix_rounds = data.get("max_fix_rounds", 5)
        self.state.cron_job_id = data.get("cron_job_id")
