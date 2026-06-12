"""Deterministic file assessment — no AI needed."""

import subprocess
from pathlib import Path

from utils import (
    get_file_info,
    compute_coder_count,
    compute_line_ranges,
    get_workspace,
    ASSESSMENT_FILE,
)
from state import AssessmentResult, ClassInfo, BoundedRange


def assess_file(file_path: str) -> AssessmentResult:
    """Analyze a test file and return structured assessment.

    coder_count and line_ranges are pre-distribution estimates based on
    file size. The actual coder_count is set during the distribute phase
    based on applicable refactoring rules. Line ranges remain available
    as informational context for the analyst.
    """
    path = Path(file_path)
    file_name = path.stem
    line_count, test_count, class_names = get_file_info(file_path)

    coder_count = compute_coder_count(line_count)
    class_layout = _extract_class_layout(path)
    raw_ranges = compute_line_ranges(line_count, coder_count, class_layout)
    line_ranges = [BoundedRange(start=r[0], end=r[1]) for r in raw_ranges]

    # When class-boundary alignment collapses ranges (e.g. a single class
    # file where no split is possible), sync coder_count to reality.
    if len(line_ranges) < coder_count:
        coder_count = len(line_ranges)

    git_dirty = _check_git_dirty(path)

    result = AssessmentResult(
        file_path=str(file_path),
        file_name=file_name,
        file_size=line_count,
        coder_count=coder_count,
        line_ranges=line_ranges,
        class_layout=class_layout,
        total_test_count=test_count,
        git_dirty=git_dirty,
    )

    workspace = get_workspace(file_name)
    (workspace / ASSESSMENT_FILE).write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )

    return result


def _extract_class_layout(file_path: Path) -> list[ClassInfo]:
    """Parse class definitions and count their test methods."""
    content = file_path.read_text()
    lines = content.split("\n")
    class_infos = []
    current_class = None
    current_class_line = 0
    current_test_count = 0

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if stripped.startswith("class ") and (
            "TestCase" in stripped
            or stripped.split("class ")[1].split("(")[0].split(":")[0].startswith(
                "Test"
            )
        ):
            if current_class is not None:
                class_infos.append(
                    ClassInfo(
                        name=current_class,
                        line_number=current_class_line,
                        end_line=i - 1,
                        test_count=current_test_count,
                    )
                )
            current_class = stripped.split("class ")[1].split("(")[0].split(":")[0]
            current_class_line = i
            current_test_count = 0
        elif stripped.startswith("def test_") and current_class is not None:
            current_test_count += 1

    if current_class is not None:
        class_infos.append(
            ClassInfo(
                name=current_class,
                line_number=current_class_line,
                end_line=len(lines),
                test_count=current_test_count,
            )
        )

    return class_infos


def _check_git_dirty(file_path: Path) -> bool:
    """Check if the file has uncommitted changes."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", str(file_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(result.stdout.strip())
    except subprocess.CalledProcessError:
        return False
