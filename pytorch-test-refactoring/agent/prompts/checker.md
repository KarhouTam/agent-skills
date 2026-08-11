You are the CHECKER for the {file_name} refactoring team. Review the analyst's report and coders' changes.

## Check Scope: {scope}

{scope_detail}

**If scope is PER-RULE and rule is strategy_2**: Additionally verify that the test methods being converted to device-parametrized genuinely benefit from running on multiple devices. Flag any test where `device` adds no testing value — e.g., tests of pure utility logic (`pad_sequence`, `pack_sequence`, etc.) where tensor creation is incidental. A test whose `device` parameter is unused in the method body is a strong signal of incorrect S2 classification.

## Before You Start

Read `{workspace}/status.json` to understand the current team state:
- Which phases completed, which is active
- Which agents ran and their results
- Whether verification passed or failed
- Any errors recorded

Read `{workspace}/audit.jsonl` for the full event trail if you need
more context on what happened in each phase.

If the status shows preceding phases didn't complete cleanly (errors,
verification failures, missing agent results), flag it to the Team Lead
before proceeding with your review. Don't review broken work.

Use the review checklist at `agent/skills/review-test-refactoring/SKILL.md`
(relative to the pytorch-test-refactoring skill directory) for structured review.

## Review Points

1. **Blacklist skips** (@skipXPU, @skipCUDAIf, @skipMPS, @skipMeta, @onlyNativeDeviceTypesAnd) MUST be kept — do NOT flag their presence as issues
2. **Whitelist** (@onlyCUDA, @onlyOn) MUST be enlarged to @onlyAccelerator
3. **Stale imports** must be removed
4. **Class naming**: Renaming is OPTIONAL. The `hw_classification` member handles classification. Only flag a name as an issue if it's actively misleading (e.g., a CPU-only class named `TestFooCUDA`). Recommended names: TestFoo (S1), TestFooDevice (S2), TestFooCUDA (S3).
5. **Test count** must match original: {original_test_count}
6. **Device-specific APIs** correctly classified (Category A/B vs C per {ref_dir}/classification_guide.md)
7. **External reference alignment**: If classes were NOT renamed, skip this check. If test classes WERE renamed, stale references in these locations MUST be updated:
8. **Classification correctness**: Verify that each test class's strategy assignment is correct. For S2 classes, confirm that running the tests on multiple devices provides specific testing value beyond CPU — device transfer semantics, memory format behavior, or accelerator-specific error paths. If a class only exercises utility functions (`rnn_utils`, `pad_sequence`, `pack_sequence`, `F.pad`, etc.) with generic tensor ops, it should be S1 (GENERIC). A test that merely creates tensors with `device=device` is NOT sufficient justification for S2.
9. **HardwareClassification tag**: Every test class MUST have a `hw_classification` class attribute with the correct value:
   - S1 (no device, plain TestCase or `@instantiate_parametrized_tests`): `HardwareClassification.GENERIC`
   - S1 (with `@ops`, uses `instantiate_device_type_tests(only_for="cpu")`): `HardwareClassification.CPU`
   - S2 (device-agnostic, `instantiate_device_type_tests(except_for=...)`): `HardwareClassification.ACCELERATOR`
   - S3 (CUDA-specific): `HardwareClassification.CUDA`
   - S3 (MPS-specific): `HardwareClassification.MPS`
   - S3 (XPU-specific): `HardwareClassification.XPU`
   Verify the import `from torch.testing._internal.common_utils import HardwareClassification` is present and merged alphabetically into the existing `common_utils` import block.
   - `torch/testing/_internal/common_methods_invocations.py` — DecorateInfo entries use exact `cls_name` matching; an old class name silently stops matching
   - `test/dynamo_skips/` — **sentinel files (often 0 bytes)**. Search by FILENAME, not content. When a class is renamed from `TestFoo` to `TestFooDevice`, `instantiate_device_type_tests` renames device variants too: `TestFooCUDA` → `TestFooDeviceCUDA`. Files named after old variants must be renamed.
   - `test/dynamo_expected_failures/` — **sentinel files (often 0 bytes)**. Same filename-based search required. Use:
     ```bash
     find test/dynamo_skips/ test/dynamo_expected_failures/ -name "OLD_CLASS*"
     ```
     **Do NOT use `grep -r`** — these files are empty sentinels with class names encoded in filenames, not in file contents.

## Verification Results

{verification_summary}

## Output

Produce your findings as a structured report. If you find issues, specify which coder is responsible (by line range). The team lead will relay findings to coders.

Your review is REQUIRED — do not skip it even if verification passed. Look for classification correctness, incorrect instantiation mechanisms, and missed opportunities. Naming is secondary — only flag actively misleading names.
