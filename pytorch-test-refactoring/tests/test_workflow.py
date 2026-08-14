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

import pytest

from agent.codex import CodexAdapter
from state import (
    AnalystReport,
    AssessmentResult,
    CoderTask,
    FlowSignal,
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
    assert [t.rule for t in coder_tasks] == ["strategy_1", "strategy_2", "cleanup"]

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
    workspace = Path("agent_space/refactor/test_expanded_weights")
    _install_materials(
        workspace,
        "assessment.json",
        "analyst_report.json",
        "coder_tasks.json",
        "verification.json",
        "flow_state.json",
    )

    flow = RefactorFlow(adapter=CodexAdapter())
    state = flow.run("test/test_expanded_weights.py", resume=True)

    assert state.current_phase == "review"
    assert state.signal == FlowSignal.SPAWN_SINGLE
    assert [t.rule for t in state.coder_tasks] == [
        "strategy_1",
        "strategy_2",
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

    flow = RefactorFlow(adapter=CodexAdapter())
    state = flow.run("test/test_expanded_weights.py")

    # Phase 1/2 boundary: assessment is deterministic; analysis needs an agent.
    assert state.current_phase == "analyze"
    assert state.signal == FlowSignal.SPAWN_SINGLE

    orchestrator._emit_next_action(flow, state)
    first = emitted[-1]
    assert first["status"] == "need_agent"
    assert first["tasks"][0]["tool"] == "spawn_agent"
    assert first["tasks"][0]["model"] == "deepseek-v4-pro"

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
        "strategy_1",
        "strategy_2",
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
