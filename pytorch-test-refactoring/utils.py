"""Path constants, workspace helpers, and git utilities."""

import subprocess
from pathlib import Path

REFACTOR_WORKSPACE_ROOT = Path("agent_space/refactor")

REFERENCE_ROOT = Path(__file__).resolve().parent / "reference"
SUPPORTED_FIELDS = ("core", "distributed", "graph")
NON_CORE_FIELDS = ("distributed", "graph")
FIELD_TEST_LIST_FILE = "test_list.txt"

ASSESSMENT_FILE = "assessment.json"
ANALYST_REPORT_MD = "analyst_report.md"
ANALYST_REPORT_JSON = "analyst_report.json"
CODER_TASKS_FILE = "coder_tasks.json"
VERIFICATION_FILE = "verification.json"
REVIEW_FINDINGS_FILE = "review_findings.json"
LOCAL_TEST_FILE = "local_test.json"
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
    "create class with instantiate_device_type_tests(). "
    "Renaming to TestFooDevice is optional — agent decides based on external refs.",
    "strategy_3": "Extract accelerator-specific tests (S3) — "
    "move tests using Category C APIs into a class, "
    "give each test a `device` parameter, and register with "
    "instantiate_device_type_tests(<Class>, globals(), only_for='cuda'). "
    "Renaming to TestFooCUDA is optional — agent decides based on external refs.",
    "cleanup": "Import cleanup and external reference updates — "
    "remove stale TEST_CUDA/TEST_MPS/TEST_XPU/onlyOn imports, "
    "update DecorateInfo references in common_methods_invocations.py, "
    "rename stale entries in test/dynamo_skips/ and test/dynamo_expected_failures/",
}

RULE_ORDER = ["strategy_1", "strategy_2", "strategy_3", "cleanup"]

# HardwareClassification mapping — strategy → (hw_classification_value, import_line)
# Maps refactoring strategy + optional device to the correct HardwareClassification enum member.
# Reference: torch/testing/_internal/common_utils.py
HW_CLASSIFICATION_IMPORT = (
    "from torch.testing._internal.common_utils import HardwareClassification"
)
HW_CLASSIFICATION_MAP: dict[str, str] = {
    # S1 — no device dependency
    "GENERIC": "HardwareClassification.GENERIC",  # plain TestCase or @instantiate_parametrized_tests
    "CPU": "HardwareClassification.CPU",  # instantiate_device_type_tests(only_for="cpu")
    # S2 — device-agnostic (any accelerator)
    "ACCELERATOR": "HardwareClassification.ACCELERATOR",  # instantiate_device_type_tests(except_for=...)
    # S3 — device-specific
    "CUDA": "HardwareClassification.CUDA",  # Category C CUDA APIs
    "MPS": "HardwareClassification.MPS",  # Category C MPS APIs
    "XPU": "HardwareClassification.XPU",  # Category C XPU APIs
}

# Mapping from strategy assignment to recommended hw_classification key
STRATEGY_TO_HW_CLASSIFICATION: dict[str, str] = {
    "Strategy1": "GENERIC",
    "Strategy2": "ACCELERATOR",
    "Strategy3": "CUDA",  # default for S3; overridden per device (CUDA/MPS/XPU)
}


def compute_applicable_rules(
    strategy_assignments: dict[str, str], field: str = "core"
) -> list[str]:
    """Return list of rule IDs that apply based on strategy assignments.

    Always includes 'cleanup' (import/external-ref hygiene is always needed).
    Includes strategy_N when at least one test is assigned to that strategy.

    Non-core fields use the field-agnostic baseline: cleanup only, until a
    field-specific refactoring profile is added.
    """
    if field != "core":
        return ["cleanup"]

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


def normalize_test_path(file_path: str) -> str:
    """Normalize a test path to the repo-relative form used by field lists."""
    path = Path(file_path)
    if not path.is_absolute():
        return path.as_posix()

    try:
        git_root = Path(run_git(path.parent, "rev-parse", "--show-toplevel").strip())
        return path.relative_to(git_root).as_posix()
    except Exception:
        pass

    try:
        return path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _read_field_test_paths(field: str) -> set[str]:
    """Read and normalize one field's detection manifest."""
    list_path = REFERENCE_ROOT / field / FIELD_TEST_LIST_FILE
    if not list_path.exists():
        return set()

    paths: set[str] = set()
    for raw in list_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        paths.add(normalize_test_path(line))
    return paths


def resolve_field(file_path: str) -> str:
    """Resolve the refactoring field for a test file.

    Field membership is exact path membership in each non-core field's
    ``test_list.txt``. Unmatched files default to ``core``. If a path appears
    in more than one non-core list, the ambiguity is reported as an error.
    """
    normalized = normalize_test_path(file_path)
    matches = [
        field
        for field in NON_CORE_FIELDS
        if normalized in _read_field_test_paths(field)
    ]
    if len(matches) > 1:
        raise ValueError(
            f"Ambiguous field for {normalized}: matched {', '.join(matches)}"
        )
    return matches[0] if matches else "core"


def get_reference_dir(field: str = "core") -> str:
    """Return the reference directory for a field.

    Core keeps the historical root reference directory; non-core fields use
    ``reference/<field>/``.
    """
    if field == "core":
        return str(REFERENCE_ROOT)
    return str(REFERENCE_ROOT / field)


def get_workspace(file_name: str, field: str = "core") -> Path:
    """Return the workspace directory for a given test file name.

    Args:
        file_name: e.g. "test_ops" (without .py extension)
        field: refactoring field, e.g. "core", "distributed", or "graph"

    Returns:
        Path like agent_space/refactor/core/test_ops/
    """
    ws = REFACTOR_WORKSPACE_ROOT / field / file_name
    ws.mkdir(parents=True, exist_ok=True)
    return ws


# ── PR feedback ingest sidecar ──────────────────────────────────────

INGEST_WORKSPACE_ROOT = Path("agent_space/ingest")
INGEST_STATE_FILE = "state.json"
INGEST_FLOW_STATE_FILE = "flow_state.json"
INGEST_FINDINGS_DIR = "findings"
INGEST_RAW_DIR = "raw"
CLAUDE_BOT_LOGIN = "claude[bot]"


def get_ingest_workspace() -> Path:
    """Return the ingest sidecar workspace dir, creating it if needed."""
    ws = INGEST_WORKSPACE_ROOT
    ws.mkdir(parents=True, exist_ok=True)
    (ws / INGEST_FINDINGS_DIR).mkdir(parents=True, exist_ok=True)
    (ws / INGEST_RAW_DIR).mkdir(parents=True, exist_ok=True)
    return ws


# ── PR review queue sidecar ─────────────────────────────────────────

PR_QUEUE_FILE = Path("agent_space/pr_needs_review.txt")
PR_REVIEW_WORKSPACE_ROOT = Path("agent_space/pr_reviews")
PR_REVIEWED_ARCHIVE_FILE = "pr_reviewed.json"
PR_REVIEW_FLOW_STATE_FILE = "flow_state.json"
PR_REVIEW_ISSUE_REPO = "cosdt/pytorch-initial-pr-reviews"
PR_REVIEW_ISSUE_NUMBER = 1
PR_REVIEW_TEST_PREFIXES = ("test/", "torch/testing/")


def get_pr_review_workspace() -> Path:
    """Return the PR review queue workspace dir, creating it if needed."""
    ws = PR_REVIEW_WORKSPACE_ROOT
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
