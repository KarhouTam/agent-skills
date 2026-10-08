"""End-to-end workflow coverage driven by the checked-in materials snapshot.

The materials in tests/materials/ are the workspace artifacts from a real
test_expanded_weights.py refactoring. These tests replay them through the
state machine so we cover the phases, feed dispatch, persistence/resume, and
artifact generation - not just the adapter contract covered by test_harness.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.harnesses import get_harness
from state import (
    AnalystReport,
    AssessmentResult,
    CoderTask,
    FlowSignal,
    ReviewFinding,
    ReviewFindings,
    VerificationResult,
)
from flow import RefactorFlow


MATERIALS = Path(__file__).resolve().parent / "materials"

# 29 tests split across the 5 classes from the materials' class_mapping.
SYNTHETIC_TEST_COUNTS = {
    "TestContext": 0,
    "TestExpandedWeightHelperFunction": 10,
    "TestExpandedWeightFunctional": 13,
    "TestExpandedWeightModule": 0,
    "ContextManagerTests": 6,
}


def _write_synthetic_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ['"""Synthetic refactored test fixture for workflow replay tests."""', ""]
    for cls, count in SYNTHETIC_TEST_COUNTS.items():
        lines.append(f"class {cls}(TestCase):")
        lines.append("    hw_classification = HardwareClassification.GENERIC")
        for i in range(count):
            lines.append(f"    def test_{i}(self):")
            lines.append("        self.assertTrue(True)")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _install_materials(workspace: Path, *names: str) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copyfile(MATERIALS / name, workspace / name)


# ── 1. Every artifact parses into its Pydantic model ───────────────


def test_materials_parse_into_models():
    assessment = AssessmentResult.model_validate_json(
        (MATERIALS / "assessment.json").read_text()
    )
    assert assessment.total_test_count == 29
    assert {c.name for c in assessment.class_layout} == {
        "TestContext",
        "TestExpandedWeightHelperFunction",
        "TestExpandedWeightFunctional",
        "TestExpandedWeightModule",
        "TestModule",
    }

    analyst = AnalystReport.model_validate_json(
        (MATERIALS / "analyst_report.json").read_text()
    )
    assert set(analyst.class_mapping) == set(analyst.strategy_assignments)

    coder_tasks = [
        CoderTask.model_validate(d)
        for d in json.loads((MATERIALS / "coder_tasks.json").read_text())
    ]
    assert [t.rule for t in coder_tasks] == ["cpu_only", "device_agnostic", "cleanup"]

    verification = VerificationResult.model_validate_json(
        (MATERIALS / "verification.json").read_text()
    )
    assert verification.test_count_match is True
    assert {c.name for c in verification.checks} >= {
        "syntax",
        "test_count",
        "class_structure",
    }

    import orchestrator

    coder_result = orchestrator._build_coder_result(
        json.loads((MATERIALS / "coder_result.json").read_text())
    )
    assert coder_result.success is True
    assert coder_result.coder_id == "coder"

    review = orchestrator._build_review_findings(
        json.loads((MATERIALS / "checker_result.json").read_text())
    )
    assert review.all_clear is True

    flow_state = json.loads((MATERIALS / "flow_state.json").read_text())
    assert flow_state["current_phase"] == "review"
    assert set(flow_state["agent_ids"]) == {"coder", "checker"}


# ── 2. Cross-process resume reconstructs state from the snapshot ────


def test_flow_resumes_from_materials(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_synthetic_file(Path("test/test_expanded_weights.py"))
    workspace = Path("agent_space/refactor/core/test_expanded_weights")
    _install_materials(
        workspace,
        "assessment.json",
        "analyst_report.json",
        "coder_tasks.json",
        "verification.json",
        "flow_state.json",
    )

    flow = RefactorFlow()
    state = flow.run("test/test_expanded_weights.py", resume=True)

    assert state.current_phase == "review"
    assert state.signal == FlowSignal.SPAWN_SINGLE
    assert [t.rule for t in state.coder_tasks] == [
        "cpu_only",
        "device_agnostic",
        "cleanup",
    ]
    assert state.agent_ids == {
        "coder": "a8f5e1d2742c0dd02",
        "checker": "a146404c507c7489e",
    }
    assert state.total_test_count == 29
    assert state.verification is not None
    assert state.analyst_report is not None


# ── 3. Full phase-machine replay to finalize ───────────────────────


def test_full_flow_replay_to_finalize(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_synthetic_file(Path("test/test_expanded_weights.py"))

    import orchestrator

    emitted = []
    monkeypatch.setattr(orchestrator, "_write_json", lambda obj: emitted.append(obj))

    flow = RefactorFlow()
    state = flow.run("test/test_expanded_weights.py")

    # Phase 1/2 boundary: assessment is deterministic; analysis needs an agent.
    assert state.current_phase == "analyze"
    assert state.signal == FlowSignal.SPAWN_SINGLE

    orchestrator._emit_next_action(flow, state, get_harness("codex"))
    first = emitted[-1]
    assert first["status"] == "need_agent"
    assert first["tasks"][0]["tool"] == "spawn_agent"
    assert "model" not in first["tasks"][0]
    assert first["tasks"][0]["fork_turns"] == "all"

    # The analyst feed loads analyst_report.json from the workspace.
    _install_materials(flow.state.workspace, "analyst_report.json")

    steps = 0
    while True:
        feed_as = orchestrator._feed_type_for(state)
        if feed_as == "analyst":
            ok = orchestrator._dispatch_feed(
                flow, "analyst", {"agent_id": "id-analyst", "agent_name": "analyst"}
            )
        elif feed_as == "coder":
            ok = orchestrator._dispatch_feed(
                flow,
                "coder",
                {
                    "agent_id": "id-coder",
                    "agent_name": "coder",
                    "success": True,
                    "tests_moved": [],
                },
            )
        else:  # checker
            ok = orchestrator._dispatch_feed(
                flow,
                "checker",
                {
                    "agent_id": "id-checker",
                    "agent_name": "checker",
                    "passed": True,
                    "all_clear": True,
                },
            )
        assert ok, f"feed {feed_as} failed at phase={state.current_phase}"

        state = flow.run("test/test_expanded_weights.py")
        if state.current_phase == "finalize" and state.signal == FlowSignal.DONE:
            break
        steps += 1
        assert steps < 50, "replay did not converge"

    # Phase 3 distributed three rules, each verified and finally reviewed.
    assert [t.rule for t in state.coder_tasks] == [
        "cpu_only",
        "device_agnostic",
        "cleanup",
    ]
    assert state.review_findings is not None and state.review_findings.all_clear
    assert state.final_summary and "Refactoring Summary" in state.final_summary
    assert "coder" in state.agent_ids

    ws = state.workspace
    for artifact in (
        "assessment.json",
        "analyst_report.json",
        "coder_tasks.json",
        "verification.json",
        "final_summary.md",
        "audit.jsonl",
        "status.json",
        "flow_state.json",
    ):
        assert (ws / artifact).exists(), f"missing artifact {artifact}"


# ── 4. Transient state and review outcome survive a process boundary ─


def test_flow_state_roundtrip_preserves_pending_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()

    first = RefactorFlow()
    first.state.workspace = ws
    first.state.current_phase = "fix"
    first.state.signal = FlowSignal.RELAY_FINDINGS
    first.state.lint_gate_pending = True
    first.state.lint_retry_count = 2
    first.state.agent_ids = {"coder": "c1"}
    first._save_flow_state()

    second = RefactorFlow()
    second.state.workspace = ws
    second._load_flow_state()

    assert second.state.current_phase == "fix"
    assert second.state.signal == FlowSignal.RELAY_FINDINGS
    assert second.state.lint_gate_pending is True
    assert second.state.lint_retry_count == 2
    assert second.state.agent_ids == {"coder": "c1"}


def test_review_findings_persist_and_resolve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    flow = RefactorFlow()
    flow.state.workspace = ws
    flow.state.file_path = "test/test_x.py"
    flow.state.current_phase = "review"

    flow.feed_review_findings(
        ReviewFindings(
            all_clear=False,
            findings=[
                ReviewFinding(severity="Major", category="lint", description="d")
            ],
        )
    )
    assert flow.state.current_phase == "fix"
    assert flow.state.signal == FlowSignal.RELAY_FINDINGS
    path = ws / "review_findings.json"
    assert path.exists()

    def fake_verify():
        flow.state.current_phase = "verify"
        flow.state.verification = VerificationResult(
            all_passed=True,
            checks=[],
            original_test_count=1,
            current_test_count=1,
            test_count_match=True,
        )

    monkeypatch.setattr(flow, "_phase_verify", fake_verify)
    flow.feed_fix_complete()

    assert flow.state.review_findings.all_clear is True
    assert len(flow.state.review_findings.findings) == 1
    assert json.loads(path.read_text())["all_clear"] is True


def test_assess_captures_git_dirty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from scripts import assess as assess_mod

    monkeypatch.setattr(assess_mod, "_check_git_dirty", lambda path: True)
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "test_x.py").write_text(
        "class TestX(TestCase):\n    def test_a(self):\n        pass\n"
    )
    flow = RefactorFlow()
    flow.state.file_path = "test/test_x.py"
    flow.state.workspace = Path("agent_space/refactor/core/test_x")
    flow.state.workspace.mkdir(parents=True)

    flow._phase_assess()
    assert flow.state.git_dirty is True
