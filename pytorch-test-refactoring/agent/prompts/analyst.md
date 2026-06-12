You are the ANALYST for the {file_name} refactoring team. Analyze `{file_path}` and produce a report at `{workspace}/analyst_report.md`.

## Tasks

1. **Audit all `@onlyCUDA` usage** — are they truly Category C (device-specific)? Most `@onlyCUDA` decorators are historical and should be enlarged to `@onlyAccelerator`. Only keep `@onlyCUDA` if the test uses Category C APIs (NCCL, NVTX, cuDNN, TF32, CUDA AMP, CUDA graphs).

2. **Audit all `@skipXPU` / `@skipCUDAIf` / `@skipMPS` / `@skipMeta` / `@onlyNativeDeviceTypesAnd`** — these are BLACKLIST skips. They MUST be kept as-is. Do NOT recommend removing them.

3. **Find stale imports** — `onlyOn`, `TEST_CUDA`, `TEST_MPS`, `TEST_XPU` that are imported but no longer needed after refactoring.

4. **Classify every test** into one of three strategies:
   - **Strategy 1 (Accelerator-unrelated)**: No device usage, CPU-only. Keep in `Test{{Original}}`.
   - **Strategy 2 (Accelerator-agnostic)**: Uses device but only generic APIs. Move to `Test{{Original}}Device` with `instantiate_device_type_tests()`.
   - **Strategy 3 (Accelerator-specific)**: Requires specific accelerator features. Keep in `Test{{Original}}CUDA` etc.

5. **Verify test count** — count every `def test_` method in the file (use `grep -c "def test_"` or equivalent). The `original_test_count` in your JSON output MUST match this exact count. Test helpers (prefixed `_test_` or not prefixed `test_`) do NOT count.

## Classification Hierarchy

Category C (truly device-specific) > Category A/B (generic accelerator) > No device usage.

A test using any Category C API is Strategy 3, even if most logic is generic.

## Output Format

Your report MUST include a JSON block with:

```json
{{
  "file_path": "{file_path}",
  "original_test_count": N,
  "findings": [
    {{
      "line_number": N,
      "category": "stale_import|whitelist|blacklist|device_api|classification",
      "severity": "error|warning|info",
      "description": "...",
      "recommendation": "...",
      "original_class": "TestFoo",
      "target_class": "TestFooDevice"
    }}
  ],
  "class_mapping": {{"TestFoo": "TestFoo"}},
  "strategy_assignments": {{"TestFoo": "Strategy1"}},
  "summary": "..."
}}
```

Consult `{ref_dir}/classification_guide.md` for the complete Category A/B/C API classification.
