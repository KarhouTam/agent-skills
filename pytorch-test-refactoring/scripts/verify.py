"""Deterministic verification — checks against the refactored file."""

import json
import re
import subprocess
import sys
from pathlib import Path

from utils import (
    COMMON_METHODS_INVOCATIONS,
    DYNAMO_SKIPS_DIR,
    DYNAMO_EXPECTED_FAILURES_DIR,
    get_workspace,
    VERIFICATION_FILE,
    ASSESSMENT_FILE,
)
from state import VerificationResult, VerificationCheck


def verify(
    file_path: str,
    original_test_count: int,
    original_classes: list[str],
) -> VerificationResult:
    """Run all verification checks against the refactored file."""
    checks: list[VerificationCheck] = []

    # Derive workspace and load assessment for checks that need them
    file_name = Path(file_path).stem
    workspace = get_workspace(file_name)
    assessment = _load_assessment(workspace)

    checks.append(_check_syntax(file_path))
    checks.append(_check_test_count(file_path, original_test_count))
    checks.append(_check_class_structure(file_path, original_classes))
    checks.append(_check_decorateinfo(file_path, original_classes))
    checks.append(_check_external_refs(file_path, original_classes, workspace))
    checks.append(_check_stale_patterns(file_path, workspace, assessment))
    checks.append(_check_imports(file_path))

    # New Phase-5 checks
    checks.append(_check_dtype_integrity(file_path))
    checks.append(_check_accelerator_safety(file_path))
    checks.append(_check_coverage_preservation(file_path, workspace))
    checks.append(_check_class_split(file_path, original_classes, workspace))
    checks.append(_check_skipifmps_coverage(file_path, workspace))
    checks.append(_check_hw_classification(file_path))

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

    (workspace / VERIFICATION_FILE).write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )

    return result


def _load_assessment(workspace: Path) -> dict | None:
    """Load assessment.json from the workspace if it exists."""
    assessment_path = workspace / ASSESSMENT_FILE
    if assessment_path.exists():
        try:
            return json.loads(assessment_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _check_syntax(file_path: str) -> VerificationCheck:
    """Verify the file can be fully imported (catches ImportError).

    Replaces the previous py_compile-based check which only caught syntax
    errors.  Runs a full Python import so that ImportError from stale module
    references is also caught.
    """
    path = Path(file_path).resolve()

    # Find git repo root to compute the dotted module path
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(path.parent),
            timeout=5,
        )
        if result.returncode != 0:
            raise RuntimeError("Not in a git repo")
        repo_root = Path(result.stdout.strip())
    except Exception:
        repo_root = path.parent.parent  # best-effort fallback

    try:
        rel_path = path.relative_to(repo_root)
    except ValueError:
        repo_root = path.parent
        rel_path = path.name

    module_path = ".".join(rel_path.with_suffix("").parts)

    try:
        proc = subprocess.run(
            [sys.executable, "-c", f"import {module_path}"],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30,
        )
        if proc.returncode == 0:
            return VerificationCheck(
                name="syntax",
                passed=True,
                details=f"Successfully imported as {module_path}",
            )
        else:
            return VerificationCheck(
                name="syntax",
                passed=False,
                details=proc.stderr.strip()[:500],
            )
    except subprocess.TimeoutExpired:
        return VerificationCheck(
            name="syntax",
            passed=False,
            details="Import timed out after 30s",
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
    file_path: str, original_classes: list[str], workspace: Path | None = None
) -> VerificationCheck:
    """Check dynamo_skips/ and dynamo_expected_failures/ for stale class references.

    When test classes are renamed (e.g., TestFoo -> TestFooDevice), entries in
    test/dynamo_skips/ and test/dynamo_expected_failures/ that reference the old
    class name silently stop matching and tests that were previously skipped or
    expected to fail will now run unguarded.

    Also cross-checks that sentinel files exist in BOTH directories -- when a
    file exists for a renamed class in one directory but not the other, it is
    flagged as a mismatch (M2).
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

    # M2: Cross-check both dynamo directories for matching sentinel files
    mismatches: list[str] = []
    orig_to_new: dict[str, str] = {}
    for orig in renamed_classes:
        matching_new = [c for c in current_classes if c.startswith(orig)]
        if matching_new:
            orig_to_new[orig] = matching_new[0]

    if orig_to_new:
        skip_dir = Path(DYNAMO_SKIPS_DIR)
        expected_dir = Path(DYNAMO_EXPECTED_FAILURES_DIR)
        skip_files: dict[str, set[str]] = {}
        expected_files: dict[str, set[str]] = {}

        for new_name in orig_to_new.values():
            skip_suffixes: set[str] = set()
            if skip_dir.exists():
                for f in skip_dir.iterdir():
                    if f.is_file() and f.name.startswith(new_name):
                        skip_suffixes.add(f.name[len(new_name):])

            expected_suffixes: set[str] = set()
            if expected_dir.exists():
                for f in expected_dir.iterdir():
                    if f.is_file() and f.name.startswith(new_name):
                        expected_suffixes.add(f.name[len(new_name):])

            only_in_skip = skip_suffixes - expected_suffixes
            only_in_expected = expected_suffixes - skip_suffixes

            for suffix in only_in_skip:
                mismatches.append(
                    f"dynamo_skips/{new_name}{suffix} has no counterpart "
                    f"in dynamo_expected_failures"
                )
            for suffix in only_in_expected:
                mismatches.append(
                    f"dynamo_expected_failures/{new_name}{suffix} has no counterpart "
                    f"in dynamo_skips"
                )

    cmd = (
        f"find {DYNAMO_SKIPS_DIR} {DYNAMO_EXPECTED_FAILURES_DIR} "
        f'-name "{renamed_classes[0]}*" 2>/dev/null || true'
        if renamed_classes
        else "true"
    )

    combined_issues = stale + mismatches
    passed = len(combined_issues) == 0

    detail_parts: list[str] = []
    if stale:
        detail_parts.append(
            f"STALE ({len(stale)}): {'; '.join(stale[:10])}"
            + (f" and {len(stale) - 10} more" if len(stale) > 10 else "")
        )
    if mismatches:
        detail_parts.append(
            f"MISMATCH ({len(mismatches)}): {'; '.join(mismatches[:5])}"
            + (f" and {len(mismatches) - 5} more" if len(mismatches) > 5 else "")
        )

    return VerificationCheck(
        name="external_refs",
        passed=passed,
        details="No stale external references"
        if passed
        else "; ".join(detail_parts),
        command=cmd,
    )


def _check_stale_patterns(
    file_path: str,
    workspace: Path | None = None,
    assessment: dict | None = None,
) -> VerificationCheck:
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

        # ── M1: @onlyCPU decorator (should be migrated) ──
        if re.search(r"@onlyCPU\b", stripped) and not stripped.startswith("#"):
            key = "@onlyCPU decorator (should be removed or replaced)"
            stale_counts[key] = stale_counts.get(key, 0) + 1

        # ── m1: _cuda suffix in test method names (non-S3 classes) ──
        method_match = re.match(r"def\s+(test_\w*_cuda\w*)\(", stripped)
        if method_match:
            key = f"_cuda suffix in method '{method_match.group(1)}' (rename or move to S3)"
            stale_counts[key] = stale_counts.get(key, 0) + 1

        # ── M5 item 2: @unittest.skipIf(not TEST_CUDA, ...) in non-S3 ──
        if re.search(r"@unittest\.skipIf\(not\s+TEST_CUDA", stripped):
            key = "@unittest.skipIf(not TEST_CUDA, ...) in non-S3 class (should be @onlyAccelerator)"
            stale_counts[key] = stale_counts.get(key, 0) + 1

        # ── M5 item 4: torch.cuda.* calls in non-S3 classes ──
        torch_cuda_calls = re.findall(r"torch\.cuda\.\w+", stripped)
        if torch_cuda_calls and not stripped.startswith("#"):
            for call in torch_cuda_calls:
                key = f"{call} in non-S3 class (should be migrated or moved to S3)"
                stale_counts[key] = stale_counts.get(key, 0) + 1

        # ── m2: device == 'cuda' / 'xpu' bare string comparison ──
        if re.search(
            r"device\s*==\s*['\"](?:cuda|xpu)['\"]", stripped
        ) and not stripped.startswith("#"):
            key = "device == 'cuda'/'xpu' (should use device.type == ...)"
            stale_counts[key] = stale_counts.get(key, 0) + 1

        # ── m2: device_type='cuda' in autocast calls ──
        if re.search(
            r"device_type\s*=\s*['\"]cuda['\"]", stripped
        ) and not stripped.startswith("#"):
            key = "device_type='cuda' in autocast (should use device_type=device)"
            stale_counts[key] = stale_counts.get(key, 0) + 1

        # ── m2: Module-level device_type global variable assignments ──
        if re.match(r"device_type\s*=", stripped) and not stripped.startswith("#"):
            key = "Module-level device_type global variable (should use local variable)"
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


# ── B1: dtype-integrity check ──────────────────────────────────────────


def _check_dtype_integrity(file_path: str) -> VerificationCheck:
    """Flag @unittest.expectedFailure methods containing for-dtype loops.

    Collapsing @dtypes into a manual for-dtype loop inside an expected-failure
    method is a probable semantic change -- the loop may execute dtypes that
    were not originally covered by @dtypes.  Returns WARN-level findings.
    """
    content = Path(file_path).read_text()
    lines = content.split("\n")
    findings: list[str] = []

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped in ("@unittest.expectedFailure", "@expectedFailure"):
            # Find the method definition (look ahead up to 5 lines)
            method_line = None
            for j in range(i + 1, min(i + 5, len(lines))):
                if re.match(r"\s*def\s+", lines[j]):
                    method_line = j
                    break

            if method_line is not None:
                mname_match = re.match(r"\s*def\s+(\w+)", lines[method_line])
                method_name = mname_match.group(1) if mname_match else "unknown"

                # Collect method body until we reach a new def/class at
                # the same or lesser indentation level
                method_indent = len(lines[method_line]) - len(
                    lines[method_line].lstrip()
                )
                body_lines: list[str] = []
                for k in range(method_line + 1, len(lines)):
                    if not lines[k].strip():
                        continue
                    k_stripped = lines[k].strip()
                    if k_stripped.startswith("def ") or k_stripped.startswith("class "):
                        break
                    k_indent = len(lines[k]) - len(lines[k].lstrip())
                    if (
                        k_indent <= method_indent
                        and not k_stripped.startswith("@")
                        and not k_stripped.startswith("#")
                    ):
                        if k_stripped:
                            break
                    body_lines.append(lines[k])

                body = "\n".join(body_lines)
                if re.search(r"for\s+\w+\s+in\s+\w*dtype", body, re.IGNORECASE):
                    findings.append(
                        f"WARN: {method_name} has @unittest.expectedFailure "
                        f"with for-dtype loop (probable semantic change)"
                    )

        i += 1

    passed = len(findings) == 0
    return VerificationCheck(
        name="dtype_integrity",
        passed=passed,
        details="No issues" if passed else "; ".join(findings[:10]),
    )


# ── B3+B4: Accelerator safety check ────────────────────────────────────


def _check_accelerator_safety(file_path: str) -> VerificationCheck:
    """Check MPS dtype safety (B3) and accelerator type safety (B4).

    B3: If allow_mps=True or @onlyAccelerator is present AND a @dtypes
    decorator contains torch.double / torch.float64 / torch.complex128 /
    torch.cdouble, flag as FAIL unless a mitigating decorator
    (@skipIfMPS / @dtypesIfMPS / @expectedFailureMPS) is also present.

    B4: Scan for torch.accelerator.current_device_index() compared with
    string-typed values, and torch.accelerator.set_device_index() called
    with a non-int argument.  Flag as FAIL.
    """
    content = Path(file_path).read_text()
    finding_details: list[str] = []

    # ── B3: MPS dtype safety ────────────────────────────────────────────
    has_mps_exposure = "allow_mps=True" in content or "@onlyAccelerator" in content

    if has_mps_exposure:
        problematic_dtypes = [
            "torch.double",
            "torch.float64",
            "torch.complex128",
            "torch.cdouble",
        ]
        has_problematic = False
        for dt_match in re.finditer(r"@dtypes?\(([^)]*)\)", content):
            dtype_args = dt_match.group(1)
            if any(dt in dtype_args for dt in problematic_dtypes):
                has_problematic = True
                break

        has_mitigation = bool(
            re.search(
                r"@skipIfMPS|@dtypesIfMPS|@expectedFailureMPS", content
            )
        )

        if has_problematic and not has_mitigation:
            finding_details.append(
                "FAIL: MPS-unsafe dtypes (double/float64/complex128/cdouble) "
                "with onlyAccelerator/allow_mps=True but no @skipIfMPS, "
                "@dtypesIfMPS, or @expectedFailureMPS"
            )

    # ── B4: Accelerator type safety ──────────────────────────────────────
    # torch.accelerator.current_device_index() compared with a string value
    if re.search(
        r"torch\.accelerator\.current_device_index\(\)\s*(?:==|!=)\s*['\"]",
        content,
    ):
        finding_details.append(
            "FAIL: torch.accelerator.current_device_index() compared "
            "with string value"
        )

    # torch.accelerator.set_device_index() with a string literal
    string_set_device = re.findall(
        r"torch\.accelerator\.set_device_index\([\'\"][^)]*[\'\"]", content
    )
    if string_set_device:
        finding_details.append(
            f"FAIL: torch.accelerator.set_device_index() called with "
            f"{len(string_set_device)} non-int argument(s)"
        )

    passed = len(finding_details) == 0
    return VerificationCheck(
        name="accelerator_safety",
        passed=passed,
        details="No issues found" if passed else "; ".join(finding_details[:10]),
    )


# ── M9: Coverage preservation check ────────────────────────────────────


def _check_coverage_preservation(file_path: str, workspace: Path) -> VerificationCheck:
    """Compare per-method device decorator sets between original and refactored file.

    Uses 'git show HEAD:<path>' to retrieve the original committed version.
    Flags any test whose device scope was BROADENED (e.g., @onlyCPU removed)
    as a WARN -- the new scope needs manual verification.
    """
    content = Path(file_path).read_text()

    # Retrieve original file content from git
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{file_path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return VerificationCheck(
                name="coverage_preservation",
                passed=True,
                details="Could not retrieve original file from git",
            )
        original_content = proc.stdout
    except (subprocess.TimeoutExpired, OSError):
        return VerificationCheck(
            name="coverage_preservation",
            passed=True,
            details="Could not retrieve original file from git (timeout/error)",
        )

    original_decorators = _extract_method_decorators(original_content)
    current_decorators = _extract_method_decorators(content)

    # Only examine methods present in both versions
    broadenings: list[str] = []
    for method in set(original_decorators.keys()) & set(current_decorators.keys()):
        orig_set = set(original_decorators[method])
        curr_set = set(current_decorators[method])
        # "only"-family decorators that were present originally but are now
        # removed indicate a broadened device scope.
        only_removed = [
            d
            for d in (orig_set - curr_set)
            if d.startswith("@only") or d.startswith("@skip")
        ]
        if only_removed:
            broadenings.append(
                f"{method}: removed {', '.join(only_removed)}"
            )

    passed = len(broadenings) == 0
    return VerificationCheck(
        name="coverage_preservation",
        passed=passed,
        details="No scope broadening detected"
        if passed
        else f"WARN: Possible scope broadening: {'; '.join(broadenings[:10])}",
    )


# ── Helpers ────────────────────────────────────────────────────────────


def _extract_method_decorators(content: str) -> dict[str, list[str]]:
    """Extract per-method decorator lists from Python source.

    Returns a dict mapping method name -> list of decorator strings found
    on the lines immediately preceding the method definition.
    """
    lines = content.split("\n")
    methods: dict[str, list[str]] = {}

    for i, line in enumerate(lines):
        stripped = line.strip()
        m = re.match(r"def\s+(test_\w+)", stripped)
        if m:
            method_name = m.group(1)
            decorators: list[str] = []
            j = i - 1
            while j >= 0:
                prev = lines[j].strip()
                if prev.startswith("@") and not prev.startswith("@@"):
                    decorators.insert(0, prev)
                elif prev == "" or prev.startswith("#"):
                    j -= 1
                    continue
                else:
                    break
                j -= 1
            methods[method_name] = decorators

    return methods


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

# Module-level symbols that become stale after S2 conversion.
_STALE_SYMBOLS = [
    "device_type",
]


# ── P1: Class split verification ─────────────────────────────────────

def _check_class_split(
    file_path: str, original_classes: list[str], workspace: Path | None = None
) -> VerificationCheck:
    """Verify that class extraction matches analyst recommendations.

    Reads analyst_report.json from workspace and checks:
    1. If new_classes were recommended, the new classes exist in the file
    2. The recommended tests were actually moved out of the original class
    3. New classes have correct instantiation (no instantiate_device_type_tests for S1)
    """
    if workspace is None:
        file_name = Path(file_path).stem
        workspace = get_workspace(file_name)

    analyst_path = workspace / "analyst_report.json"
    if not analyst_path.exists():
        return VerificationCheck(
            name="class_split",
            passed=True,
            details="No analyst report found, skipping class split check",
        )

    try:
        analyst = json.loads(analyst_path.read_text())
    except (json.JSONDecodeError, OSError):
        return VerificationCheck(
            name="class_split",
            passed=True,
            details="Could not parse analyst report, skipping",
        )

    new_classes = analyst.get("new_classes", [])
    if not new_classes:
        return VerificationCheck(
            name="class_split",
            passed=True,
            details="No class splits recommended by analyst",
        )

    content = Path(file_path).read_text()
    issues: list[str] = []

    for nc in new_classes:
        class_name = nc.get("name", "")
        tests_to_move = set(nc.get("tests", []))
        strategy = nc.get("strategy", "")

        # Check the new class exists
        class_pattern = rf"class {class_name}\b"
        if not re.search(class_pattern, content):
            issues.append(f"Recommended class '{class_name}' not found in file")
            continue

        # Check tests were moved out of the original class
        class_start_pattern = r"class (\w+)\(.*TestCase"
        class_starts = list(re.finditer(class_start_pattern, content))

        for i, m in enumerate(class_starts):
            cls_name = m.group(1)
            start_pos = m.start()
            end_pos = class_starts[i + 1].start() if i + 1 < len(class_starts) else len(content)
            class_body = content[start_pos:end_pos]

            # Only check original (non-new) classes
            if cls_name == class_name:
                continue

            # Check if any test that should have been moved is still here
            for test_name in tests_to_move:
                if f"def {test_name}" in class_body:
                    issues.append(
                        f"'{test_name}' still in '{cls_name}' "
                        f"(should be in '{class_name}')"
                    )

        # Check correct instantiation for S1 classes
        if strategy == "Strategy1":
            if f"instantiate_device_type_tests({class_name}" in content:
                issues.append(
                    f"S1 class '{class_name}' uses instantiate_device_type_tests "
                    f"— should use plain TestCase or @instantiate_parametrized_tests"
                )

    passed = len(issues) == 0
    return VerificationCheck(
        name="class_split",
        passed=passed,
        details="All recommended class splits applied correctly"
        if passed
        else "; ".join(issues[:10]),
    )


# ── P2: @skipIfMPS coverage check ────────────────────────────────────

def _check_skipifmps_coverage(
    file_path: str, workspace: Path | None = None
) -> VerificationCheck:
    """Verify @skipIfMPS is present on tests newly exposed to MPS.

    When @onlyCPU is removed or @onlyCUDA/@onlyOn is enlarged, the test
    runs on MPS for the first time.  This check ensures @skipIfMPS is
    added as a safety measure on those tests.
    """
    # Try to get original file from git for comparison
    try:
        proc = subprocess.run(
            ["git", "show", f"HEAD:{file_path}"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode != 0:
            return VerificationCheck(
                name="skipifmps_coverage",
                passed=True,
                details="Could not retrieve original file from git",
            )
        original_content = proc.stdout
    except (subprocess.TimeoutExpired, OSError):
        return VerificationCheck(
            name="skipifmps_coverage",
            passed=True,
            details="Could not retrieve original file from git (timeout/error)",
        )

    current_content = Path(file_path).read_text()
    missing_mps: list[str] = []

    # Find tests that were @onlyCPU in original but no longer are
    orig_methods = _extract_method_decorators(original_content)
    curr_methods = _extract_method_decorators(current_content)

    for method in set(orig_methods.keys()) & set(curr_methods.keys()):
        orig_decos = set(orig_methods[method])
        curr_decos = set(curr_methods[method])

        # Test had @onlyCPU originally (was CPU-only, never on MPS)
        had_onlycpu = any(d.startswith("@onlyCPU") for d in orig_decos)
        # Test already had skipIfMPS
        had_skipmps = any("@skipIfMPS" in d for d in orig_decos)

        # If test had @onlyCPU removed (now runs on MPS for first time)
        has_onlycpu_now = any(d.startswith("@onlyCPU") for d in curr_decos)
        if had_onlycpu and not has_onlycpu_now and not had_skipmps:
            # Check if @skipIfMPS was added
            has_skipmps_now = any("@skipIfMPS" in d for d in curr_decos)
            has_dtypesifmps = any("@dtypesIfMPS" in d for d in curr_decos)
            if not has_skipmps_now and not has_dtypesifmps:
                missing_mps.append(method)

    passed = len(missing_mps) == 0
    return VerificationCheck(
        name="skipifmps_coverage",
        passed=passed,
        details="All newly-MPS-exposed tests have @skipIfMPS"
        if passed
        else f"Missing @skipIfMPS on: {', '.join(missing_mps[:15])}"
        + (f" and {len(missing_mps) - 15} more" if len(missing_mps) > 15 else ""),
    )



# ── H1: HardwareClassification tag check ──────────────────────────────────

_HW_CLASSIFICATION_VALUES = {
    "GENERIC", "ACCELERATOR", "CPU", "CUDA", "MPS", "XPU"
}


def _check_hw_classification(file_path: str) -> VerificationCheck:
    """Verify every test class has the correct hw_classification attribute.

    For each TestCase subclass in the file:
    1. Extract the class body
    2. Check for hw_classification = HardwareClassification.<VALUE>
    3. Determine the expected value from the class mechanism
    4. Report missing, mismatched, or unrecognized classes
    """
    content = Path(file_path).read_text()
    classes = _find_test_classes_with_bodies(content)

    if not classes:
        return VerificationCheck(
            name="hw_classification",
            passed=True,
            details="No test classes found",
        )

    findings: list[str] = []
    for cls_name, cls_body, cls_start_line in classes:
        actual = _extract_hw_classification(cls_body)
        expected = _infer_hw_classification(cls_name, cls_body, content)

        if actual is None:
            findings.append(
                f"MISSING: {cls_name} (line {cls_start_line}) has no "
                f"hw_classification — expected {expected}"
            )
        elif expected and actual != expected:
            findings.append(
                f"MISMATCH: {cls_name} (line {cls_start_line}) has "
                f"{actual}, expected {expected}"
            )
        elif not expected:
            findings.append(
                f"UNRECOGNIZED: {cls_name} (line {cls_start_line}) has "
                f"{actual} but class mechanism could not be determined"
            )

    # Also check the import exists
    has_import = "HardwareClassification" in content
    if not has_import:
        findings.insert(
            0,
            "MISSING IMPORT: HardwareClassification not imported from "
            "torch.testing._internal.common_utils",
        )

    passed = len(findings) == 0
    return VerificationCheck(
        name="hw_classification",
        passed=passed,
        details="All classes correctly tagged"
        if passed
        else "; ".join(findings[:15]),
        command=f"grep -n 'hw_classification\\|HardwareClassification' {file_path}",
    )


def _find_test_classes_with_bodies(content: str) -> list[tuple[str, str, int]]:
    """Find all TestCase subclasses and return (name, body, start_line).

    Uses regex to find class definitions, then extracts the body between
    this class and the next top-level class/function definition.
    """
    lines = content.split("\n")
    class_pattern = re.compile(
        r"^class\s+(\w+)\s*\(.*(?:TestCase).*\)\s*:"
    )
    results: list[tuple[str, str, int]] = []

    for i, line in enumerate(lines):
        m = class_pattern.match(line.strip())
        if not m:
            continue
        cls_name = m.group(1)
        cls_start = i + 1  # 1-indexed

        # Find class end: next line at same/lesser indent that starts a
        # class, def, or top-level statement
        cls_indent = len(line) - len(line.lstrip())
        body_lines: list[str] = []
        for j in range(i + 1, len(lines)):
            stripped = lines[j].strip()
            if not stripped or stripped.startswith("#"):
                body_lines.append(lines[j])
                continue
            line_indent = len(lines[j]) - len(lines[j].lstrip())
            if line_indent <= cls_indent and (
                stripped.startswith("class ")
                or stripped.startswith("def ")
                or stripped.startswith("@")
                or stripped.startswith("if __name__")
            ):
                break
            body_lines.append(lines[j])

        results.append((cls_name, "\n".join(body_lines), cls_start))

    return results


def _extract_hw_classification(cls_body: str) -> str | None:
    """Extract the HardwareClassification value from a class body.

    Returns the enum member name (e.g. "ACCELERATOR") or None if not found.
    """
    m = re.search(
        r"hw_classification\s*=\s*HardwareClassification\.(\w+)",
        cls_body,
    )
    if m:
        value = m.group(1)
        if value in _HW_CLASSIFICATION_VALUES:
            return value
        return f"UNKNOWN({value})"
    return None


def _infer_hw_classification(
    cls_name: str, cls_body: str, full_content: str
) -> str | None:
    """Infer the expected HardwareClassification value from class structure.

    Checks, in priority order:
    1. instantiate_device_type_tests(ClassName, ..., only_for="X") → X (upper)
    2. instantiate_device_type_tests(ClassName, ..., except_for=...) → ACCELERATOR
    3. @instantiate_parametrized_tests (class decorator) → GENERIC
    4. setUp guard with device.is_available() → device-specific classification
    5. Plain TestCase (no device mechanism) → GENERIC
    """
    # Check for instantiate_device_type_tests call for this class
    # Pattern: instantiate_device_type_tests(ClassName, globals(), ...)
    inst_pattern = rf"instantiate_device_type_tests\(\s*{cls_name}\s*,"
    inst_match = re.search(inst_pattern, full_content)
    if inst_match:
        # Find the full argument list
        start = inst_match.start()
        # Extract the full call (balance parens)
        depth = 0
        end = start
        for k in range(start, len(full_content)):
            if full_content[k] == "(":
                depth += 1
            elif full_content[k] == ")":
                depth -= 1
                if depth == 0:
                    end = k + 1
                    break
        call_str = full_content[start:end]

        # Check only_for
        only_for_m = re.search(r"only_for\s*=\s*['\"]([^'\"]+)['\"]", call_str)
        if only_for_m:
            device = only_for_m.group(1).upper()
            if device in _HW_CLASSIFICATION_VALUES:
                return device
            return None

        # Check except_for → ACCELERATOR
        except_for_m = re.search(r"except_for\s*=", call_str)
        if except_for_m:
            return "ACCELERATOR"

        # No only_for/except_for but has instantiate_device_type_tests
        # (legacy pattern — defaults to all device types incl CPU)
        return "ACCELERATOR"

    # Check for @instantiate_parametrized_tests class decorator
    # (look in full_content for the decorator before the class definition)
    class_def_pat = rf"(@(?:instantiate_parametrized_tests|unittest\.skipIf)\s*\n\s*)*class\s+{cls_name}\b"
    class_def_m = re.search(class_def_pat, full_content)
    if class_def_m:
        decorator_block = class_def_m.group(0)
        if "@instantiate_parametrized_tests" in decorator_block:
            return "GENERIC"

    # Check for setUp guard with device availability check (S3 pattern)
    if re.search(r"torch\.cuda\.is_available\(\)", cls_body):
        return "CUDA"
    if re.search(r"torch\.mps\.is_available\(\)", cls_body):
        return "MPS"
    if re.search(r"torch\.xpu\.is_available\(\)", cls_body):
        return "XPU"

    # Check for device parameter in test methods (S2 without
    # instantiate_device_type_tests — unusual but possible)
    if re.search(r"def\s+test_\w+\(self,\s*device\b", cls_body):
        # Has device param but no instantiate_device_type_tests call found
        # Could be a class that uses device_type_tests indirectly
        return "ACCELERATOR"

    # Plain TestCase, no device mechanism → GENERIC
    return "GENERIC"


def _check_imports(file_path: str) -> VerificationCheck:
    """Verify no stale device-specific imports remain.

    Checks for imports that indicate the file still has device-specific
    coupling: TEST_CUDA, TEST_MPS, TEST_XPU, onlyOn, onlyCUDA.
    ``onlyCUDA`` is exempted when it is actively used as a decorator
    (``@onlyCUDA``), which is legitimate for S3 device-specific classes.
    """
    content = Path(file_path).read_text()
    findings = [imp for imp in _STALE_IMPORTS if imp in content]
    # Also detect module-level stale symbol assignments
    for sym in _STALE_SYMBOLS:
        if re.search(rf"^{sym}\s*=", content, re.MULTILINE):
            findings.append(f"{sym} (module-level variable)")

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
