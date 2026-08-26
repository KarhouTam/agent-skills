# Analyst Report: `test/test_expanded_weights.py`

## Overview

This file tests `torch.nn.utils._expanded_weights` (per-sample gradients via `ExpandedWeight` / `call_for_per_sample_grads`). It is **already almost entirely device-agnostic**: all test classes are instantiated via `instantiate_device_type_tests`, and every test method already carries a `device` parameter (plus `dtype`/`op`/`module_info` where applicable). No `@onlyCPU`, `@onlyCUDA`, `@onlyOn`, `@onlyAccelerator`, or blacklist skip decorators exist. The refactoring work here is minimal and mostly mechanical.

## Test Count

- `grep -c "def test_"` → **29**
- Nested (non-test) `def test_fn` helpers inside `test_set_grad_sample_if_exists` (line 143) and `test_set_grad_sample_if_exists_failure` (line 162) → **-2**
- **`original_test_count` = 27** (top-level class methods only).

Breakdown:

| Class | Tests | Count |
|---|---|---|
| `TestExpandedWeightHelperFunction` | test_forward_helper, test_forward_helper_failure_args, test_set_grad_sample_if_exists, test_set_grad_sample_if_exists_failure, test_unpack_expanded_weight_or_tensor, test_unpack_expanded_weight_or_tensor_with_custom_function, test_unpack_expanded_weight_or_tensor_failure, test_sum_over_all_but_batch_and_last_n | 8 |
| `TestExpandedWeightFunctional` | test_expanded_weight_per_sample_grad_sum, test_expanded_weight_per_sample_grad_mean, test_expanded_weights_per_sample_grad_input_no_grad, test_unsupported_expand_weights, test_expanded_weight_forward, test_expanded_weight_error, test_cnn_model_sum, test_cnn_model_mean, test_instance_norm_model, test_group_norm_model, test_layer_norm_model, test_embedding_model, test_group_norm_error | 13 |
| `TestExpandedWeightModule` | test_module, test_per_sample_api_failing, test_per_sample_api_compute_batch_size, test_per_sample_api_compute_batch_size_not_pytreeable | 4 |
| `ContextManagerTests` | test_context_manager, test_context_manager_multiple_inputs | 2 |
| **Total** | | **27** |

Note: the module-level loop (lines 1022-1055) dynamically `setattr`s CPU and CUDA-named variants onto `TestExpandedWeightModule` (`<name>`, `<name>_multiple_inputs`, `<name>_cuda_double`). These are not static `def test_` methods and are not counted; they are the legacy nn-tests harness (see Findings).

## 1. `@onlyCUDA` Audit

**No `@onlyCUDA` decorators exist in this file.** Nothing needs enlarging to `@onlyAccelerator`. All device-scoped tests are plain `device`-parametrized methods inside `instantiate_device_type_tests` classes, so they already run on every available device. This finding also applies to `@onlyOn` (none present).

## 2. Blacklist Skip Audit

**No `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, or `@onlyNativeDeviceTypesAnd` decorators exist.** The only skip decorator is `@skipIfTorchDynamo` (line 330, `test_unsupported_expand_weights`) which is a dynamo skip and is unrelated to device. Nothing to preserve or remove.

## 3. Stale Symbols

| Symbol | Status | Detail |
|---|---|---|
| `device_type` (module global) | Not present | No module-level `device_type` variable exists. |
| `TEST_CUDA` | **Keep** (not stale) | Imported line 20; used at exactly one site (line 1049) inside the legacy module-level test-generation loop. It is not referenced by any test being converted to device-agnostic, so the CPU-only→device-agnostic conversion does not orphan it. Removable only if the legacy harness is modernized (flagged as follow-up). |
| `TEST_MPS`, `TEST_XPU` | Not present | Not imported. |
| `onlyOn` | Not present | Not imported. |
| `tf32_off` | **Keep** | Imported line 20; used by 6 `@tf32_off()` decorators on device-agnostic tests. |
| `TestContext` | **Stale (dead code)** | Empty `class TestContext: pass` (lines 44-45), no tests, never referenced. Recommend removal. |

All other imports are used and must stay: `instantiate_device_type_tests`, `OpDTypes`, `ops`, `op_db`, `SampleInput`, `module_db`, `modules`, `get_new_module_tests`, `module_tests`, `TestBase`, `make_tensor`, `freeze_rng_state`, `parametrize`, `skipIfTorchDynamo`, `ExpandedWeight`, the `expanded_weights_utils` helpers, `call_for_per_sample_grads`, `tree_map_only`.

## 4. Per-Class Strategy Classification

### `TestExpandedWeightHelperFunction` — device-agnostic (ACCELERATOR)

8 tests exercising the `expanded_weights_utils` helpers (`forward_helper`, `standard_kwargs`, `set_grad_sample_if_exists`, `unpack_expanded_weight_or_tensor`, `sum_over_all_but_batch_and_last_n`). Although these are utility-function tests (which the brief says default to CPU-only), they are **already inside an `instantiate_device_type_tests` class and already carry `device`** — per the Scope Guard, a test in an instantiated class without `@onlyCPU` is already device-agnostic and must not be reclassified CPU-only. No changes needed beyond what exists.

### `TestExpandedWeightFunctional` — device-agnostic (ACCELERATOR)

13 tests: 5 `@ops` per-sample-gradient tests (device/dtype/op), `test_unsupported_expand_weights` (`@skipIfTorchDynamo` + `@ops`), `test_expanded_weight_forward` (`@ops`, with in-body CUDA skip — see Findings), `test_expanded_weight_error` (make_tensor with `device`), 5 CNN/norm-model tests (`@tf32_off`, via `_compute_tolerances` helper), `test_embedding_model`, `test_group_norm_error` (lacks `device` param — see Findings). All device-agnostic model/op tests; device-agnostic.

### `TestExpandedWeightModule` — device-agnostic (ACCELERATOR)

4 static tests: `test_module` (`@modules` + `@tf32_off`, fully device-parametrized RNN per-sample-grad test), plus 3 per-sample-API error/compute tests that **lack a `device` parameter** (`test_per_sample_api_failing`, `test_per_sample_api_compute_batch_size`, `test_per_sample_api_compute_batch_size_not_pytreeable`). They are in an `instantiate_device_type_tests` class and lack `@onlyCPU`, so they are already device-agnostic; the coder only needs to add `device` to the signatures and thread it into tensor creation. The legacy dynamically-attached tests stay on this class as-is.

### `ContextManagerTests` — device-agnostic (ACCELERATOR)

A legacy `TestBase` harness. Its two methods (`test_context_manager`, `test_context_manager_multiple_inputs`) are **device-aware templates** with signature `(self, test_case, device)`; they are consumed by the module-level `setattr` loop (line 1022-1055) which attaches CPU and CUDA variants to `TestExpandedWeightModule`. They exercise device transfer (`.to(device)`, `.to(**kwargs)`), so device-agnostic. They are not directly instantiated by `instantiate_device_type_tests`.

### `TestContext` — CPU-only (GENERIC), dead code

Empty placeholder; recommend removal.

## 5. `@onlyCPU` Evaluations

**No `@onlyCPU` decorators exist in this file**, so `onlycpu_evaluations` is an empty list. Every test is either already device-parametrized or (for the 4 tests lacking a `device` param listed above) already device-agnostic per the Scope Guard.

## 6. Class Splits / Renames

**No class splits are recommended** — there are no CPU-only tests mixed into device-agnostic classes; the file is uniformly device-agnostic.

**No class renames are recommended.** `TestExpandedWeightFunctional` is referenced by a `DecorateInfo` in `torch/testing/_internal/common_methods_invocations.py` (line 21822) that skips `test_expanded_weight_forward` for the `nn.functional.embedding` OpInfo. `DecorateInfo` matches the generic class name (the `ops` framework passes `generic_cls.__name__`), so renaming would silently disable that skip unless the DecorateInfo is updated. Since renaming is optional and the file is already uniformly device-agnostic, keep all original class names.

## 7. Findings (non-classification)

1. **`_compute_tolerances` uses `torch.cuda.get_device_capability(0)`** (line 482) — listed in the catalog under both Category A (same-name, replaceable) and Category C (CUDA device properties), and `torch.accelerator.get_device_capability` returns `dict[str, Any]` on some backends vs `tuple[int,int]` on CUDA. Recommend a device-type-aware guard rather than a blind replacement. The 5 calling tests stay device-agnostic.
2. **In-body CUDA skip** in `test_expanded_weight_forward` (lines 383-389) — a device conditional `if "cuda" in device ... self.skipTest(...)`. Per the guide, this does not make the test device-specific; keep it.
3. **Legacy module-level harness** (lines 1022-1055) hardcodes `'cpu'`/`'cuda'` and gates CUDA variants on `TEST_CUDA`. Keeps `TEST_CUDA` live. Recommend modernizing to ModuleInfo as a follow-up (matches the in-file TODO at line 1017).

## Summary / TL;DR

- **27** top-level test methods (`grep -c "def test_"` = 29 minus 2 nested `def test_fn`).
- **All 27 are device-agnostic** (already in `instantiate_device_type_tests` classes with `device`); no CPU-only, no device-specific, no `@onlyCUDA`, no blacklist skips.
- Mechanical-only work for the coder: add `device` to `test_per_sample_api_failing`, `test_per_sample_api_compute_batch_size`, `test_per_sample_api_compute_batch_size_not_pytreeable`, `test_group_norm_error`.
- **No class splits, no renames** (external `DecorateInfo` reference on `TestExpandedWeightFunctional`).
- Stale symbol: only the dead empty `TestContext` class. `TEST_CUDA`/`tf32_off` stay; `TEST_MPS`/`TEST_XPU`/`onlyOn`/`device_type` are absent.
- Flagged for coder: `_compute_tolerances` (`torch.cuda.get_device_capability`) and the in-body CUDA skip in `test_expanded_weight_forward`.
