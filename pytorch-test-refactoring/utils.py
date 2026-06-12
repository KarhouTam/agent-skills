"""Path constants, workspace helpers, and git utilities."""

import subprocess
from pathlib import Path

REFACTOR_WORKSPACE_ROOT = Path("agent_space/refactor")

ASSESSMENT_FILE = "assessment.json"
ANALYST_REPORT_MD = "analyst_report.md"
ANALYST_REPORT_JSON = "analyst_report.json"
CODER_TASKS_FILE = "coder_tasks.json"
VERIFICATION_FILE = "verification.json"
REVIEW_FINDINGS_FILE = "review_findings.json"
FINAL_SUMMARY_FILE = "final_summary.md"
AUDIT_LOG = "audit.jsonl"
STATUS_FILE = "status.json"

COMMON_METHODS_INVOCATIONS = "torch/testing/_internal/common_methods_invocations.py"
DYNAMO_SKIPS_DIR = "test/dynamo_skips"
DYNAMO_EXPECTED_FAILURES_DIR = "test/dynamo_expected_failures"

# Refactoring rules for rule-based coder distribution.
# Each coder is assigned one rule and applies it across the entire file.
REFACTOR_RULES: dict[str, str] = {
    "strategy_1": "Extract accelerator-unrelated tests (S1) — "
    "move CPU-only tests into the original-named class with "
    "@instantiate_parametrized_tests or plain TestCase",
    "strategy_2": "Convert to device-agnostic tests (S2) — "
    "enlarge @onlyCUDA/@onlyOn to @onlyAccelerator, replace .cuda()/.to('cuda') "
    "with .to(device), replace Category A/B APIs with accelerator equivalents, "
    "create TestFooDevice with instantiate_device_type_tests()",
    "strategy_3": "Extract accelerator-specific tests (S3) — "
    "move tests using Category C APIs into TestFooCUDA with setUp guards "
    "and @instantiate_parametrized_tests or plain TestCase",
    "cleanup": "Import cleanup and external reference updates — "
    "remove stale TEST_CUDA/TEST_MPS/TEST_XPU/onlyOn imports, "
    "update DecorateInfo references in common_methods_invocations.py, "
    "rename stale entries in test/dynamo_skips/ and test/dynamo_expected_failures/",
}

RULE_ORDER = ["strategy_1", "strategy_2", "strategy_3", "cleanup"]


def compute_applicable_rules(strategy_assignments: dict[str, str]) -> list[str]:
    """Return list of rule IDs that apply based on strategy assignments.

    Always includes 'cleanup' (import/external-ref hygiene is always needed).
    Includes strategy_N when at least one test is assigned to that strategy.
    """
    rules: list[str] = []
    strategies = set(strategy_assignments.values())
    if "Strategy1" in strategies:
        rules.append("strategy_1")
    if "Strategy2" in strategies:
        rules.append("strategy_2")
    if "Strategy3" in strategies:
        rules.append("strategy_3")
    rules.append("cleanup")
    return rules


def get_workspace(file_name: str) -> Path:
    """Return the workspace directory for a given test file name.

    Args:
        file_name: e.g. "test_ops" (without .py extension)

    Returns:
        Path like agent_space/refactor/test_ops/
    """
    ws = REFACTOR_WORKSPACE_ROOT / file_name
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def run_git(repo: Path | str, *args: str) -> str:
    """Run a git command in the given repo and return stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def get_file_info(file_path: str) -> tuple[int, int, list[str]]:
    """Return (total_lines, test_count, class_names) for a test file."""
    path = Path(file_path)
    content = path.read_text()
    lines = content.split("\n")
    line_count = len(lines)

    test_count = 0
    class_names = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("class ") and "TestCase" in stripped:
            class_name = stripped.split("class ")[1].split("(")[0].split(":")[0]
            class_names.append(class_name)
        if stripped.startswith("def test_"):
            test_count += 1

    return line_count, test_count, class_names


def compute_coder_count(file_size: int) -> int:
    """Determine coder count from file size (lines).

    | File Size       | Coders |
    |-----------------|--------|
    | < 1000 lines    | 2      |
    | 1000-3000 lines | 3      |
    | 3000-6000 lines | 4-5    |
    | > 6000 lines    | 5+     |
    """
    if file_size < 1000:
        return 2
    elif file_size < 3000:
        return 3
    elif file_size < 6000:
        return 5
    else:
        return max(5, min(10, file_size // 1000))


def compute_line_ranges(
    file_size: int, coder_count: int, class_layout: list | None = None
) -> list[tuple[int, int]]:
    """Split file into contiguous, non-overlapping line ranges.

    Returns list of (start_line, end_line) tuples, 1-indexed.
    When class_layout is provided, boundaries are adjusted to avoid
    splitting test classes across coder assignments.
    """
    chunk_size = file_size // coder_count
    ranges = []
    for i in range(coder_count):
        start = i * chunk_size + 1
        if i == coder_count - 1:
            end = file_size
        else:
            end = (i + 1) * chunk_size
        ranges.append((start, end))

    if class_layout:
        ranges = _align_to_class_boundaries(ranges, class_layout, file_size)

    return ranges


def _align_to_class_boundaries(
    ranges: list[tuple[int, int]],
    class_layout: list,
    file_size: int,
) -> list[tuple[int, int]]:
    """Adjust range boundaries to align with class start/end lines.

    If a boundary splits a class, the range is expanded or contracted
    to the nearest class boundary so no class is split across coders.
    Ranges are then adjusted to ensure full file coverage with no gaps.
    """
    # Build sorted list of (start, end) for each class
    class_ranges = sorted(
        [(ci.line_number, ci.end_line) for ci in class_layout],
        key=lambda x: x[0],
    )

    adjusted = []
    prev_end = 0

    for i, (start, end) in enumerate(ranges):
        # Snap start: if start falls inside a class, snap to class start
        for cls_start, cls_end in class_ranges:
            if cls_start <= start <= cls_end:
                start = cls_start
                break

        # Snap end: if end falls inside a class, snap to class end
        for cls_start, cls_end in class_ranges:
            if cls_start <= end <= cls_end:
                end = cls_end
                break

        # Ensure we don't overlap with previous range
        if prev_end >= start:
            start = prev_end + 1

        if start <= end:
            adjusted.append((start, end))
            prev_end = end

    # Ensure last range covers end of file
    if adjusted and adjusted[-1][1] < file_size:
        last_start, _ = adjusted[-1]
        adjusted[-1] = (last_start, file_size)

    return adjusted
