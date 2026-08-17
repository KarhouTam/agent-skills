"""Generate final summary report after refactoring is complete."""

from utils import get_workspace, FINAL_SUMMARY_FILE
from state import RefactorState


def generate_report(state: RefactorState) -> str:
    """Generate a markdown summary of the refactoring."""
    lines = [
        f"# Refactoring Summary: {state.file_name}",
        "",
        f"**File:** `{state.file_path}`",
        f"**Lines:** {state.file_size}",
        f"**Coders used:** {state.coder_count}",
        "",
        "## Class Layout",
    ]

    for cls in state.class_layout:
        lines.append(f"- `{cls.name}` (line {cls.line_number}, {cls.test_count} tests)")

    lines.append("")
    lines.append("## Verification")

    if state.verification:
        for check in state.verification.checks:
            status = ":white_check_mark:" if check.passed else ":x:"
            lines.append(f"- {status} **{check.name}**: {check.details}")

        lines.append("")
        lines.append(
            f"**Test count:** {state.verification.original_test_count}"
            f" -> {state.verification.current_test_count}"
            f" ({'match' if state.verification.test_count_match else 'MISMATCH'})"
        )

    if state.review_findings:
        lines.append("")
        lines.append("## Review")
        if state.review_findings.all_clear:
            lines.append(":white_check_mark: All clear")
        else:
            for f_item in state.review_findings.findings:
                lines.append(
                    f"- **[{f_item.severity}]** {f_item.category}: {f_item.description}"
                )

    if state.local_test:
        lines.append("")
        lines.append("## Local Test")
        local_test = state.local_test
        if local_test.whole_run_failure:
            lines.append(
                f":warning: Whole-run failure: `{local_test.whole_run_failure}`"
            )
            lines.append(f"- Command: `{local_test.command}`")
        else:
            lines.append(
                f"- Total: {local_test.total}, passed: {local_test.passed}, "
                f"failed: {local_test.failed}, errored: {local_test.errored}, "
                f"skipped: {local_test.skipped}, "
                f"expected failures: {local_test.expected_failures}, "
                f"unexpected successes: {local_test.unexpected_successes}"
            )
            if not local_test.accelerator_available:
                lines.append(
                    ":information_source: No accelerator present — S2/S3 tests "
                    "were not locally exercised."
                )
            if local_test.failures:
                lines.append("- Failures:")
                for failure in local_test.failures:
                    verdict = f" [{failure.verdict}]" if failure.verdict else ""
                    lines.append(f"  - `{failure.test_name}`{verdict}")
            if state.deferred_failures:
                lines.append("- Deferred (pre-existing/environmental):")
                for deferred in state.deferred_failures:
                    reason = deferred.defer_reason or "deferred"
                    lines.append(f"  - `{deferred.test_name}` — {reason}")

    if state.analyst_report:
        lines.append("")
        lines.append("## Strategy Assignments")
        for cls, strategy in state.analyst_report.strategy_assignments.items():
            hw_cls = state.analyst_report.hw_classifications.get(cls, "")
            hw_suffix = f" ({hw_cls})" if hw_cls else ""
            lines.append(f"- `{cls}` -> **{strategy}**{hw_suffix}")

    report = "\n".join(lines)

    workspace = get_workspace(state.file_name)
    (workspace / FINAL_SUMMARY_FILE).write_text(report, encoding="utf-8")

    return report
