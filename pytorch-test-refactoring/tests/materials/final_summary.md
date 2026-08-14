# Refactoring Summary: test_expanded_weights

**File:** `test/test_expanded_weights.py`
**Lines:** 1167
**Coders used:** 2

## Class Layout
- `TestContext` (line 44, 0 tests)
- `TestExpandedWeightHelperFunction` (line 48, 10 tests)
- `TestExpandedWeightFunctional` (line 227, 13 tests)
- `TestExpandedWeightModule` (line 623, 0 tests)
- `TestModule` (line 696, 6 tests)

## Verification
- :x: **syntax**: Traceback (most recent call last):
  File "<string>", line 1, in <module>
    import test.test_expanded_weights
ModuleNotFoundError: No module named 'test.test_expanded_weights'
- :white_check_mark: **test_count**: 29 tests
- :x: **class_structure**: Missing original classes: ['TestContext', 'ContextManagerTests']
- :white_check_mark: **decorateinfo_alignment**: No stale DecorateInfo references
- :white_check_mark: **external_refs**: No stale external references
- :x: **stale_patterns**: Remaining: 1x torch.cuda.get_device_capability in non-S3 class (should be migrated or moved to S3)
- :x: **import_audit**: Stale imports: TEST_CUDA
- :white_check_mark: **dtype_integrity**: No issues
- :white_check_mark: **accelerator_safety**: No issues found
- :white_check_mark: **coverage_preservation**: No scope broadening detected
- :white_check_mark: **class_split**: No class splits recommended by analyst
- :white_check_mark: **skipifmps_coverage**: All newly-MPS-exposed tests have @skipIfMPS
- :white_check_mark: **hw_classification**: All classes correctly tagged

**Test count:** 29 -> 29 (match)

## Review
:white_check_mark: All clear

## Strategy Assignments
- `TestContext` -> **Strategy1** (GENERIC)
- `TestExpandedWeightHelperFunction` -> **Strategy2** (ACCELERATOR)
- `TestExpandedWeightFunctional` -> **Strategy2** (ACCELERATOR)
- `TestExpandedWeightModule` -> **Strategy2** (ACCELERATOR)
- `ContextManagerTests` -> **Strategy2** (ACCELERATOR)