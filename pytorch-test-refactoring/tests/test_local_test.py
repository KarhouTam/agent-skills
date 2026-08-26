"""Unit tests for the post-review local test gate."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import flow
import orchestrator
from flow import RefactorFlow
from scripts.local_test import _parse_junit
from state import (
    FlowSignal,
    LocalTestFailure,
    LocalTestResult,
    ReviewFindings,
)


def test_parse_junit_classifies_outcomes(tmp_path):
    xml = tmp_path / "report.xml"
    xml.write_text(
        """<?xml version="1.0"?>
<testsuite name="x" tests="5" failures="1" errors="1" skipped="2">
  <testcase classname="test_x.TestFoo" name="test_pass" />
  <testcase classname="test_x.TestFooDeviceCPU" name="test_fail">
    <failure message="assert failed">traceback</failure>
  </testcase>
  <testcase classname="test_x.TestFooDeviceCPU" name="test_err">
    <error message="boom">traceback</error>
  </testcase>
  <testcase classname="test_x.TestFooDeviceCPU" name="test_skip">
    <skipped type="pytest.skip" message="no"/>
  </testcase>
  <testcase classname="test_x.TestFooDeviceCPU" name="test_xfail">
    <skipped type="pytest.xfail" message="expected"/>
  </testcase>
</testsuite>"""
    )
    counts, failures, whole = _parse_junit(xml, 0)
    assert counts["total"] == 5
    assert counts["passed"] == 1
    assert counts["failed"] == 1
    assert counts["errored"] == 1
    assert counts["skipped"] == 2
    assert counts["expected_failures"] == 1
    assert whole == ""
    assert [f.outcome for f in failures] == ["FAIL", "ERROR"]
    assert failures[0].device_type == "cpu"


def test_parse_junit_xpass_is_warning_not_failure(tmp_path):
    xml = tmp_path / "report.xml"
    xml.write_text(
        """<?xml version="1.0"?>
<testsuite name="x">
  <testcase classname="test_x.TestFoo" name="test_xpass">
    <failure message="[XPASS(strict)] unexpectedly passed">trace</failure>
  </testcase>
</testsuite>"""
    )
    counts, failures, whole = _parse_junit(xml, 0)
    assert counts["unexpected_successes"] == 1
    assert failures == []
    assert whole == ""


def test_build_local_test_fix_result():
    data = {
        "verdicts": [
            {"test_name": "a", "verdict": "fixed", "fix_applied": "x"},
            {"test_name": "b", "verdict": "deferred", "defer_reason": "pre-existing"},
        ]
    }
    verdicts = orchestrator._build_local_test_fix_result(data)
    assert [v.verdict for v in verdicts] == ["fixed", "deferred"]
    assert verdicts[0].fix_applied == "x"
    assert verdicts[1].defer_reason == "pre-existing"


def test_feed_type_for_test_phase():
    from state import RefactorState

    assert orchestrator._feed_type_for(RefactorState(current_phase="test")) == "coder"


def test_local_test_fix_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "agent_space" / "refactor" / "core" / "test_foo"
    workspace.mkdir(parents=True)

    runs = {"count": 0}

    def fake_run(file_path, report_dir, timeout=1200):
        runs["count"] += 1
        if runs["count"] == 1:
            return LocalTestResult(
                file_path=file_path,
                total=1,
                failed=1,
                failures=[
                    LocalTestFailure(test_name="TestFoo.test_x", outcome="FAIL")
                ],
            )
        return LocalTestResult(file_path=file_path, total=1, passed=1, failures=[])

    monkeypatch.setattr(flow, "run_local_tests", fake_run)

    refactor = RefactorFlow()
    refactor.state.file_path = "test/test_foo.py"
    refactor.state.file_name = "test_foo"
    refactor.state.workspace = workspace
    refactor.state.review_findings = ReviewFindings(all_clear=True, findings=[])

    refactor._phase_local_test()
    assert refactor.state.current_phase == "test"
    assert refactor.state.test_sub_phase == "fix"
    assert refactor.state.signal == FlowSignal.SEND_MESSAGE
    assert refactor.state.local_test is not None
    assert refactor.state.local_test.failed == 1

    refactor.feed_local_test_fix_result(
        [LocalTestFailure(test_name="TestFoo.test_x", verdict="fixed", fix_applied="x")]
    )
    assert refactor.state.test_sub_phase == "run"
    assert refactor.state.test_retry_count == 1
    assert refactor.state.signal == FlowSignal.DONE

    refactor._phase_local_test()
    assert refactor.state.test_sub_phase == "done"
    assert refactor.state.signal == FlowSignal.DONE
    assert runs["count"] == 2


def test_local_test_resume_reemits_fix(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "ws"
    workspace.mkdir()

    monkeypatch.setattr(flow, "run_local_tests", lambda *a, **k: None)

    refactor = RefactorFlow()
    refactor.state.file_path = "test/test_foo.py"
    refactor.state.file_name = "test_foo"
    refactor.state.workspace = workspace
    refactor.state.review_findings = ReviewFindings(all_clear=True, findings=[])
    refactor.state.test_sub_phase = "fix"
    refactor.state.signal = FlowSignal.DONE

    refactor._phase_local_test()
    assert refactor.state.test_sub_phase == "fix"
    assert refactor.state.signal == FlowSignal.SEND_MESSAGE
    assert refactor.state.local_test is None
