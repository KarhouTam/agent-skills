"""Deterministic verification — 7 checks against the refactored file."""

import re
import subprocess
from pathlib import Path

from utils import (
    COMMON_METHODS_INVOCATIONS,
    DYNAMO_SKIPS_DIR,
    DYNAMO_EXPECTED_FAILURES_DIR,
    get_workspace,
    VERIFICATION_FILE,
)
from state import VerificationResult, VerificationCheck


def verify(
    file_path: str,
    original_test_count: int,
    original_classes: list[str],
) -> VerificationResult:
    """Run all 7 verification checks against the refactored file."""
    checks: list[VerificationCheck] = []

    checks.append(_check_syntax(file_path))
    checks.append(_check_test_count(file_path, original_test_count))
    checks.append(_check_class_structure(file_path, original_classes))
    checks.append(_check_decorateinfo(file_path, original_classes))
    checks.append(_check_external_refs(file_path, original_classes))
    checks.append(_check_stale_patterns(file_path))
    checks.append(_check_imports(file_path))

    all_passed = all(c.passed for c in checks)

    content = Path(file_path).read_text()
    current_count = sum(
        1 for l in content.split("\n") if l.strip().startswith("def test_")
    )

    result = VerificationResult(
        all_passed=all_passed,
        checks=checks,
        original_test_count=original_test_count,
        current_test_count=current_count,
        test_count_match=current_count == original_test_count,
    )

    file_name = Path(file_path).stem
    workspace = get_workspace(file_name)
    (workspace / VERIFICATION_FILE).write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )

    return result


def _check_syntax(file_path: str) -> VerificationCheck:
    cmd = f"python -c \"import py_compile; py_compile.compile('{file_path}', doraise=True)\""
    try:
        subprocess.run(
            [
                "python",
                "-c",
                f"import py_compile; py_compile.compile('{file_path}', doraise=True)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return VerificationCheck(name="syntax", passed=True, command=cmd)
    except subprocess.CalledProcessError as e:
        return VerificationCheck(
            name="syntax",
            passed=False,
            details=e.stderr.strip()[:500],
            command=cmd,
        )


def _check_test_count(file_path: str, original: int) -> VerificationCheck:
    content = Path(file_path).read_text()
    current = sum(1 for l in content.split("\n") if l.strip().startswith("def test_"))
    passed = current == original
    cmd = f"grep -c 'def test_' {file_path}"
    return VerificationCheck(
        name="test_count",
        passed=passed,
        details=f"Original: {original}, Current: {current}"
        if not passed
        else f"{current} tests",
        command=cmd,
    )


def _check_class_structure(
    file_path: str, original_classes: list[str]
) -> VerificationCheck:
    """Verify no classes were lost; check for expected naming patterns."""
    content = Path(file_path).read_text()
    current_classes = [
        l.strip().split("class ")[1].split("(")[0].split(":")[0]
        for l in content.split("\n")
        if l.strip().startswith("class ")
        and (
            "TestCase" in l
            or l.strip()
            .split("class ")[1]
            .split("(")[0]
            .split(":")[0]
            .startswith("Test")
        )
    ]
    cmd = f"grep '^class ' {file_path}"

    unmatched = []
    for orig in original_classes:
        found = any(c == orig or c.startswith(orig) for c in current_classes)
        if not found:
            unmatched.append(orig)

    passed = len(unmatched) == 0
    return VerificationCheck(
        name="class_structure",
        passed=passed,
        details=f"Classes: {current_classes}"
        if passed
        else f"Missing original classes: {unmatched}",
        command=cmd,
    )


def _check_decorateinfo(
    file_path: str, original_classes: list[str]
) -> VerificationCheck:
    """Check if DecorateInfo references need updating after class renames.

    Only flags references when the renamed class's test methods appear in
    the DecorateInfo entries, reducing false positives from class name
    collisions across test files.
    """
    cminv_path = Path(COMMON_METHODS_INVOCATIONS)
    cmd = f"grep -n '{'|'.join(original_classes)}' {COMMON_METHODS_INVOCATIONS} | grep -i decorate || true"

    if not cminv_path.exists():
        return VerificationCheck(
            name="decorateinfo_alignment",
            passed=True,
            details="common_methods_invocations.py not found, skipping",
            command=cmd,
        )

    file_content = Path(file_path).read_text()
    current_classes = [
        l.strip().split("class ")[1].split("(")[0].split(":")[0]
        for l in file_content.split("\n")
        if l.strip().startswith("class ")
        and (
            "TestCase" in l
            or l.strip()
            .split("class ")[1]
            .split("(")[0]
            .split(":")[0]
            .startswith("Test")
        )
    ]

    # Collect test method names from the refactored file for renamed classes
    renamed_classes = {orig for orig in original_classes if orig not in current_classes}
    renamed_methods: dict[str, set[str]] = {}
    if renamed_classes:
        for orig in renamed_classes:
            # Find which new class maps to this original (e.g. TestFoo -> TestFooDevice)
            matching_new = [c for c in current_classes if c.startswith(orig)]
            for new_cls in matching_new:
                # Extract test method names from this class
                cls_pattern = rf"class {new_cls}\b.*\n((?:.*\n)*?)(?=^class |\Z)"
                cls_match = re.search(cls_pattern, file_content, re.MULTILINE)
                if cls_match:
                    methods = set(re.findall(r"def (test_\w+)", cls_match.group(1)))
                    renamed_methods[orig] = methods

    cminv_content = cminv_path.read_text()
    stale_refs = []
    for orig in original_classes:
        if orig not in current_classes and orig in cminv_content:
            test_methods = renamed_methods.get(orig, set())
            for match in re.finditer(
                rf'DecorateInfo\([^)]*"{orig}"[^)]*\)', cminv_content
            ):
                line_no = cminv_content[: match.start()].count("\n") + 1
                match_text = match.group()
                # Only flag if the DecorateInfo references a test method that
                # exists in the renamed class (i.e., the reference is actionable)
                if test_methods:
                    has_matching_method = any(
                        f'"{m}"' in match_text for m in test_methods
                    )
                    if not has_matching_method:
                        continue
                stale_refs.append(f"Line {line_no}: {match_text[:100]}")

    passed = len(stale_refs) == 0
    return VerificationCheck(
        name="decorateinfo_alignment",
        passed=passed,
        details="No stale DecorateInfo references"
        if passed
        else f"STALE: {'; '.join(stale_refs)}",
        command=cmd,
    )


def _check_external_refs(
    file_path: str, original_classes: list[str]
) -> VerificationCheck:
    """Check dynamo_skips/ and dynamo_expected_failures/ for stale class references.

    When test classes are renamed (e.g., TestFoo -> TestFooDevice), entries in
    test/dynamo_skips/ and test/dynamo_expected_failures/ that reference the old
    class name silently stop matching and tests that were previously skipped or
    expected to fail will now run unguarded.
    """
    file_content = Path(file_path).read_text()
    current_classes = [
        l.strip().split("class ")[1].split("(")[0].split(":")[0]
        for l in file_content.split("\n")
        if l.strip().startswith("class ")
        and (
            "TestCase" in l
            or l.strip()
            .split("class ")[1]
            .split("(")[0]
            .split(":")[0]
            .startswith("Test")
        )
    ]

    renamed_classes = [orig for orig in original_classes if orig not in current_classes]

    if not renamed_classes:
        return VerificationCheck(
            name="external_refs",
            passed=True,
            details="No class renames detected",
        )

    stale: list[str] = []
    for scan_dir, label in [
        (DYNAMO_SKIPS_DIR, "dynamo_skips"),
        (DYNAMO_EXPECTED_FAILURES_DIR, "dynamo_expected_failures"),
    ]:
        dir_path = Path(scan_dir)
        if not dir_path.exists() or not dir_path.is_dir():
            continue
        for orig in renamed_classes:
            # Use prefix without trailing dot to catch device-variant
            # filenames created by instantiate_device_type_tests.
            # E.g. TestShapeOps renames to TestShapeOpsDevice, and
            # file "TestShapeOpsCUDA.test_foo" must be renamed to
            # "TestShapeOpsDeviceCUDA.test_foo". A prefix of
            # "TestShapeOps." would miss it because the dot comes
            # AFTER "CUDA", not after "TestShapeOps".
            prefix = f"{orig}"
            matches = sorted(
                f.name
                for f in dir_path.iterdir()
                if f.is_file() and f.name.startswith(prefix)
            )
            for m in matches:
                stale.append(f"{label}/{m}")

    cmd = (
        f"find {DYNAMO_SKIPS_DIR} {DYNAMO_EXPECTED_FAILURES_DIR} "
        f'-name "{renamed_classes[0]}*" 2>/dev/null || true'
        if renamed_classes
        else "true"
    )

    passed = len(stale) == 0
    return VerificationCheck(
        name="external_refs",
        passed=passed,
        details="No stale external references"
        if passed
        else f"STALE ({len(stale)}): {'; '.join(stale[:10])}"
        + (f" and {len(stale) - 10} more" if len(stale) > 10 else ""),
        command=cmd,
    )


def _check_stale_patterns(file_path: str) -> VerificationCheck:
    """Scan for remaining device-specific patterns.

    Uses word boundaries and context to avoid false positives:
    - .cuda() is only flagged when not preceded by 'torch' (avoids
      matching torch.cuda.is_available() etc.)
    - .cuda() calls inside CUDA-guarded classes (Strategy 3) are skipped
    - device=\"cuda\" only flags hardcoded defaults, not equality checks
      against a variable named device.
    """
    content = Path(file_path).read_text()
    lines = content.split("\n")

    # Build set of line ranges that are inside CUDA-guarded classes.
    # A class is CUDA-guarded if its class-level decorator contains
    # "cuda.is_available()" or "torch.cuda" (but not "not torch.cuda").
    cuda_class_ranges: set[int] = set()
    _mark_cuda_class_ranges(lines, cuda_class_ranges)

    findings = []
    stale_counts: dict[str, int] = {}

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()

        # onlyOn( decorator usage
        if "onlyOn(" in stripped and not stripped.startswith("#"):
            key = "onlyOn decorator (should be onlyAccelerator)"
            stale_counts[key] = stale_counts.get(key, 0) + 1

        # Skip all device-specific pattern checks inside CUDA-guarded classes
        # (Strategy 3 tests legitimately use CUDA-specific APIs)
        if i in cuda_class_ranges:
            continue

        # .cuda() that is NOT torch.cuda.* (e.g., tensor.cuda())
        cuda_calls = re.findall(r"(?<!torch)\.cuda\(\)", stripped)
        if cuda_calls and not stripped.startswith("#"):
            key = ".cuda() call (should use device parameter)"
            stale_counts[key] = stale_counts.get(key, 0) + len(cuda_calls)

        # Hardcoded device="cuda" in .to() calls
        if re.search(r'\.to\([^)]*device\s*=\s*"cuda"', stripped):
            key = 'device="cuda" in .to() call (should use device variable)'
            stale_counts[key] = stale_counts.get(key, 0) + 1

        # Hardcoded device="cuda" in tensor constructors
        if re.search(
            r'(?:randn?|zeros|ones|empty|full|tensor|arange)\([^)]*device\s*=\s*"cuda"',
            stripped,
        ):
            key = 'device="cuda" in constructor (should use device variable)'
            stale_counts[key] = stale_counts.get(key, 0) + 1

    for desc, count in stale_counts.items():
        findings.append(f"{count}x {desc}")

    passed = len(findings) == 0
    return VerificationCheck(
        name="stale_patterns",
        passed=passed,
        details="Clean" if passed else f"Remaining: {'; '.join(findings)}",
        command=f"grep -nE 'onlyOn\\(|(?<!torch)\\.cuda\\(\\)|device\\s*=\\s*\"cuda\"' {file_path}",
    )


def _mark_cuda_class_ranges(lines: list[str], cuda_ranges: set[int]) -> None:
    """Populate cuda_ranges with line numbers inside device-specific test classes.

    A class is considered device-specific (Strategy 3) if:
    - Its name contains CUDA, MPS, or XPU (S3 naming convention: TestFooCUDA)
    - It has a class-level decorator with ``cuda.is_available()`` or ``torch.cuda``
    Lines from the class start through its end are added to cuda_ranges.
    """
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("class ") and (
            "TestCase" in stripped
            or stripped.split("class ")[1]
            .split("(")[0]
            .split(":")[0]
            .startswith("Test")
        ):
            class_name = stripped.split("class ")[1].split("(")[0].split(":")[0]
            is_s3 = False

            # Check class name for S3 device suffix (TestFooCUDA, TestFooMPS, etc.)
            if any(class_name.endswith(d) for d in ("CUDA", "MPS", "XPU")):
                is_s3 = True

            # Check preceding line(s) for a class-level CUDA guard decorator
            j = i - 1
            while j >= 0 and lines[j].strip().startswith("@"):
                if "cuda.is_available()" in lines[j] or "torch.cuda" in lines[j]:
                    is_s3 = True
                    break
                j -= 1

            if is_s3:
                class_end = _find_class_end(lines, i)
                for ln in range(i + 1, class_end + 1):
                    cuda_ranges.add(ln)
        i += 1


def _find_class_end(lines: list[str], class_line: int) -> int:
    """Find the end line of a class definition (exclusive).

    Uses indentation: the class body ends when we encounter a non-empty,
    non-comment line at the same or lesser indentation level as the class
    declaration, excluding decorators on sibling classes.
    """
    class_indent = len(lines[class_line]) - len(lines[class_line].lstrip())
    for i in range(class_line + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        line_indent = len(lines[i]) - len(lines[i].lstrip())
        # A new class definition, function, or top-level code at same/lesser indent
        if line_indent <= class_indent and (
            stripped.startswith("class ")
            or stripped.startswith("def ")
            or stripped.startswith("if ")
            or stripped.startswith("@")
        ):
            return i
    return len(lines)


_STALE_IMPORTS = [
    "TEST_CUDA",
    "TEST_MPS",
    "TEST_XPU",
    "onlyOn",
    "onlyCUDA",
]


def _check_imports(file_path: str) -> VerificationCheck:
    """Verify no stale device-specific imports remain.

    Checks for imports that indicate the file still has device-specific
    coupling: TEST_CUDA, TEST_MPS, TEST_XPU, onlyOn, onlyCUDA.
    ``onlyCUDA`` is exempted when it is actively used as a decorator
    (``@onlyCUDA``), which is legitimate for S3 device-specific classes.
    """
    content = Path(file_path).read_text()
    findings = [imp for imp in _STALE_IMPORTS if imp in content]

    # Exempt onlyCUDA when actively used as a decorator (S3 classes)
    if "onlyCUDA" in findings and re.search(r"@onlyCUDA\b", content):
        findings.remove("onlyCUDA")

    passed = len(findings) == 0
    return VerificationCheck(
        name="import_audit",
        passed=passed,
        details="Clean" if passed else f"Stale imports: {', '.join(findings)}",
        command=f"grep -n 'TEST_CUDA\\|TEST_MPS\\|TEST_XPU\\|onlyOn\\|onlyCUDA' {file_path}",
    )
