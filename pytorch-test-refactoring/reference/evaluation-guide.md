# Evaluation Guide: test_reductions.py

## Methodology

This guide measures whether a workflow change improves or regresses
refactoring quality on `test_reductions.py`. An agent follows this
guide after running the pytorch-test-refactoring skill to completion.

### What is evaluated

1. **Analyst classification accuracy** — does the analyst assign the
   correct strategy (CPU-only/device-agnostic/device-specific) to each test?
2. **Class split detection** — does the analyst recommend extracting
   CPU-only tests into a separate class when appropriate?
3. **Verification results** — do all 12 verification checks pass?
4. **Review findings** — what issues does the checker find in the
   final review?

### What counts as improvement

- Higher classification accuracy
- Correct class split recommendation
- Fewer verification failures
- Fewer or less-severe review findings

### What counts as regression

- Lower classification accuracy (drop >3 percentage points)
- Previously detected class split now missed
- New verification failure (check that passed before now fails)
- New category of review finding (something broken that worked before)

### What is noise (ignore)

- Minor wording differences in agent output
- Code formatting variance between runs
- Differences in non-classification analyst findings that don't
  affect correctness
- Checker findings about code style rather than correctness

## Gold Labels

Gold labels represent the **correct** classification — what the
workflow should ideally produce. Derived from analysis of landed
PR #185881 (545b05f → 341a9a2) and CHANGELOG gap analysis.

### Per-test Classification

Each test method in `test_reductions.py` has an expected strategy and
target class. There are **155 standalone test methods** (top-level
`def test_*` inside classes) plus **6 nested inner `def test_*` helper
functions** that are not standalone tests — the "161 total" from the
task brief is 155 + 6. The table below lists only the 155 standalone
tests; the 6 nested helpers are documented at the end of this section
and must NOT be classified as separate tests.

All gold labels are CPU-only or device-agnostic. **device-specific = 0** — no test in this file uses
Category C device-specific APIs (no NCCL, cuDNN, NVTX, GDS, etc.).

| # | Test method | Strategy | Target class | Notes |
|---|------------|----------|-------------|-------|
| 1 | test_dim_default | device-agnostic | TestReductions | Already device-agnostic |
| 2 | test_dim_default_keepdim | device-agnostic | TestReductions | Already device-agnostic |
| 3 | test_dim_none | device-agnostic | TestReductions | Already device-agnostic |
| 4 | test_dim_none_keepdim | device-agnostic | TestReductions | Already device-agnostic |
| 5 | test_dim_single | device-agnostic | TestReductions | Already device-agnostic |
| 6 | test_dim_single_keepdim | device-agnostic | TestReductions | Already device-agnostic |
| 7 | test_dim_empty | device-agnostic | TestReductions | Already device-agnostic |
| 8 | test_dim_empty_keepdim | device-agnostic | TestReductions | Already device-agnostic |
| 9 | test_dim_multi | device-agnostic | TestReductions | Already device-agnostic |
| 10 | test_dim_multi_keepdim | device-agnostic | TestReductions | Already device-agnostic |
| 11 | test_dim_multi_unsorted | device-agnostic | TestReductions | Already device-agnostic |
| 12 | test_dim_multi_unsorted_keepdim | device-agnostic | TestReductions | Already device-agnostic |
| 13 | test_dim_multi_duplicate | device-agnostic | TestReductions | Already device-agnostic |
| 14 | test_dim_multi_unsupported | device-agnostic | TestReductions | Already device-agnostic |
| 15 | test_dim_offbounds | device-agnostic | TestReductions | Already device-agnostic |
| 16 | test_dim_ndim_limit | device-agnostic | TestReductions | Already device-agnostic |
| 17 | test_identity | device-agnostic | TestReductions | Already device-agnostic |
| 18 | test_nan_policy_propagate | device-agnostic | TestReductions | Already device-agnostic |
| 19 | test_nan_policy_omit | device-agnostic | TestReductions | Already device-agnostic |
| 20 | test_result_dtype | device-agnostic | TestReductions | Already device-agnostic |
| 21 | test_empty_tensor_empty_slice | device-agnostic | TestReductions | Already device-agnostic |
| 22 | test_empty_tensor_nonempty_slice | device-agnostic | TestReductions | Already device-agnostic |
| 23 | test_noncontiguous_innermost | device-agnostic | TestReductions | Already device-agnostic |
| 24 | test_noncontiguous_outermost | device-agnostic | TestReductions | Already device-agnostic |
| 25 | test_noncontiguous_all | device-agnostic | TestReductions | Already device-agnostic |
| 26 | test_noncontiguous_transposed | device-agnostic | TestReductions | Already device-agnostic |
| 27 | test_noncontiguous_expanded | device-agnostic | TestReductions | Already device-agnostic |
| 28 | test_ref_scalar_input | device-agnostic | TestReductions | Already device-agnostic |
| 29 | test_ref_small_input | device-agnostic | TestReductions | Already device-agnostic |
| 30 | test_ref_large_input_1D | device-agnostic | TestReductions | Already device-agnostic |
| 31 | test_ref_large_input_2D | device-agnostic | TestReductions | Already device-agnostic |
| 32 | test_ref_large_input_64bit_indexing | device-agnostic | TestReductions | Already device-agnostic |
| 33 | test_ref_duplicate_values | device-agnostic | TestReductions | Already device-agnostic |
| 34 | test_ref_extremal_values | device-agnostic | TestReductions | Already device-agnostic |
| 35 | test_var_unbiased | device-agnostic | TestReductions | Already device-agnostic |
| 36 | test_var_stability | device-agnostic | TestReductions | Already device-agnostic |
| 37 | test_sum_dim_reduction_uint8_overflow | device-agnostic | TestReductions | Already device-agnostic |
| 38 | test_dim_reduction_less_than_64 | device-agnostic | TestReductions | Already device-agnostic |
| 39 | test_dim_reduction_lastdim | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 40 | test_logsumexp | device-agnostic | TestReductions | Already device-agnostic |
| 41 | test_logsumexp_integral_promotion | device-agnostic | TestReductions | Already device-agnostic |
| 42 | test_logcumsumexp_complex | device-agnostic | TestReductions | Already device-agnostic |
| 43 | test_sum_parallel | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` (trivial `.to(device)`) |
| 44 | test_max_elementwise | CPU-only | TestReductionsOnCPU | Uses `_testCSelection` → CPU-only `Tensor.map2_`; not mechanically convertible |
| 45 | test_min_elementwise | CPU-only | TestReductionsOnCPU | Same `map2_` CPU-only helper |
| 46 | test_all_any | device-agnostic | TestReductions | Already device-agnostic |
| 47 | test_all_any_with_dim | device-agnostic | TestReductions | Already device-agnostic |
| 48 | test_numpy_named_args | device-agnostic | TestReductions | Helper `_test_dim_ops` converted to `device` param |
| 49 | test_sum_dim | device-agnostic | TestReductions | `@slowTest @onlyCPU` → `@slowTest @skipIfMPS` |
| 50 | test_mean_dim | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 51 | test_std_dim | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 52 | test_var_dim | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 53 | test_logsumexp_dim | device-agnostic | TestReductions | `@onlyCPU @skipIfNoSciPy` → `@skipIfNoSciPy @skipIfMPS` |
| 54 | test_mean_int_with_optdtype | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 55 | test_mean_out_is_alias_of_return | device-agnostic | TestReductions | `@onlyCPU` → `@dtypesIfMPS(...)` (dtype-narrowing, not skip) |
| 56 | test_sum_integer_upcast | CPU-only | TestReductionsOnCPU | `get_all_math_dtypes('cpu')` — CPU-specific dtype enumeration |
| 57 | test_prod_integer_upcast | CPU-only | TestReductionsOnCPU | Same helper |
| 58 | test_cumsum_integer_upcast | CPU-only | TestReductionsOnCPU | Same helper |
| 59 | test_cumprod_integer_upcast | CPU-only | TestReductionsOnCPU | Same helper |
| 60 | test_mode | device-agnostic | TestReductions | Already device-agnostic |
| 61 | test_mode_large | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 62 | test_mode_boolean | device-agnostic | TestReductions | Already device-agnostic |
| 63 | test_mode_wrong_dtype | device-agnostic | TestReductions | Already device-agnostic |
| 64 | test_mode_wrong_device | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 65 | test_accreal_type | CPU-only | TestReductionsOnCPU | `@onlyCPU`; "TODO: make work on CUDA, too" — accreal semantics CPU-only |
| 66 | test_var_mean_some_dims | device-agnostic | TestReductions | Already device-agnostic |
| 67 | test_all_any_empty | device-agnostic | TestReductions | Already device-agnostic |
| 68 | test_all_issue117215 | device-agnostic | TestReductions | Already device-agnostic |
| 69 | test_max_with_inf | device-agnostic | TestReductions | Already device-agnostic |
| 70 | test_min_with_inf | device-agnostic | TestReductions | Already device-agnostic |
| 71 | test_max | device-agnostic | TestReductions | Already device-agnostic |
| 72 | test_min | device-agnostic | TestReductions | Already device-agnostic |
| 73 | test_amin | device-agnostic | TestReductions | Already device-agnostic |
| 74 | test_amax | device-agnostic | TestReductions | Already device-agnostic |
| 75 | test_aminmax | device-agnostic | TestReductions | Already device-agnostic |
| 76 | test_invalid_0dim_aminmax | device-agnostic | TestReductions | Already device-agnostic |
| 77 | test_bincount | device-agnostic | TestReductions | Already device-agnostic |
| 78 | test_var_stability2 | device-agnostic | TestReductions | Already device-agnostic |
| 79 | test_sum_noncontig_lowp | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 80 | test_sum_all | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 81 | test_sum_out | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 82 | test_prod_gpu | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 83 | test_prod | device-agnostic | TestReductions | `@onlyCPU @dtypes(torch.float)` → `@dtypes(torch.float) @skipIfMPS` |
| 84 | test_prod_lowp | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 85 | test_prod_bool | device-agnostic | TestReductions | Already device-agnostic |
| 86 | test_max_mixed_devices | device-agnostic | TestReductions | `@onlyCPU` → `@onlyAccelerator @skipIfMPS`; uses `.to(device)` |
| 87 | test_min_mixed_devices | device-agnostic | TestReductions | `@onlyCPU` → `@onlyAccelerator @skipIfMPS` |
| 88 | test_bucketization | device-agnostic | TestReductions | Already device-agnostic |
| 89 | test_nansum | device-agnostic | TestReductions | Already device-agnostic |
| 90 | test_count_nonzero | device-agnostic | TestReductions | Already device-agnostic |
| 91 | test_sum_vs_numpy | device-agnostic | TestReductions | Already device-agnostic |
| 92 | test_nansum_vs_numpy | device-agnostic | TestReductions | Already device-agnostic |
| 93 | test_nansum_complex | CPU-only | TestReductionsOnCPU | `@onlyCPU`; CPU-specific error-message assertion |
| 94 | test_nansum_out_dtype | device-agnostic | TestReductions | Already device-agnostic |
| 95 | test_nansum_int_out_dtype_float_input | device-agnostic | TestReductions | Already device-agnostic |
| 96 | test_nansum_int_out_dtype_matches_inductor | device-agnostic | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 97 | test_argminmax_multiple | device-agnostic | TestReductions | Already device-agnostic |
| 98 | test_all_any_vs_numpy | device-agnostic | TestReductions | Already device-agnostic |
| 99 | test_repeated_dim | device-agnostic | TestReductions | Already device-agnostic |
| 100 | test_var | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 101 | test_var_large_input | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 102 | test_sum_noncontig | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 103 | test_min_max_nan | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 104 | test_sum_cpu_device_mismatch | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 105 | test_minmax_illegal_dtype | device-agnostic | TestReductions | Already device-agnostic |
| 106 | test_dim_arg_reduction_scalar | device-agnostic | TestReductions | Already device-agnostic |
| 107 | test_dim_reduction | device-agnostic | TestReductions | Already device-agnostic |
| 108 | test_nanmean_integral_types | device-agnostic | TestReductions | `@onlyCPU @dtypes(...)` → `@dtypes(...) @skipIfMPS` |
| 109 | test_dim_reduction_fns | device-agnostic | TestReductions | Already device-agnostic |
| 110 | test_reduction_split | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 111 | test_reduction_vectorize_along_input_corner | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 112 | test_reduction_vectorize_along_output | device-agnostic | TestReductions | `@onlyCUDA` → `@onlyAccelerator @skipIfMPS` (only `@onlyCUDA` in file) |
| 113 | test_argminmax_large_axis | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 114 | test_argminmax_axis_with_dim_one | device-agnostic | TestReductions | Already device-agnostic |
| 115 | test_median_real_values | device-agnostic | TestReductions | Already device-agnostic |
| 116 | test_median_nan_values | device-agnostic | TestReductions | Already device-agnostic |
| 117 | test_median_corner_cases | device-agnostic | TestReductions | Already device-agnostic |
| 118 | test_quantile | device-agnostic | TestReductions | Already device-agnostic |
| 119 | test_quantile_backward | device-agnostic | TestReductions | Already device-agnostic |
| 120 | test_quantile_error | device-agnostic | TestReductions | Already device-agnostic |
| 121 | test_quantile_large_input | device-agnostic | TestReductions | Already device-agnostic |
| 122 | test_quantile_size_limit | device-agnostic | TestReductions | Already device-agnostic |
| 123 | test_quantile_partial_selection | device-agnostic | TestReductions | Already device-agnostic |
| 124 | test_quantile_partial_selection_autograd | device-agnostic | TestReductions | Already device-agnostic |
| 125 | test_std_mean | device-agnostic | TestReductions | Already device-agnostic |
| 126 | test_std_mean_all_dims | device-agnostic | TestReductions | Already device-agnostic |
| 127 | test_var_mean | device-agnostic | TestReductions | Already device-agnostic |
| 128 | test_var_mean_all_dims | device-agnostic | TestReductions | Already device-agnostic |
| 129 | test_std_mean_some_dims | device-agnostic | TestReductions | Already device-agnostic |
| 130 | test_var_vs_numpy | device-agnostic | TestReductions | Already device-agnostic |
| 131 | test_std_vs_numpy | device-agnostic | TestReductions | Already device-agnostic |
| 132 | test_var_correction_vs_numpy | device-agnostic | TestReductions | Already device-agnostic |
| 133 | test_std_correction_vs_numpy | device-agnostic | TestReductions | Already device-agnostic |
| 134 | test_std_mean_correction | device-agnostic | TestReductions | Already device-agnostic |
| 135 | test_var_mean_correction | device-agnostic | TestReductions | Already device-agnostic |
| 136 | test_warn_invalid_degrees_of_freedom | device-agnostic | TestReductions | Already device-agnostic |
| 137 | test_amin_amax_some_dims | device-agnostic | TestReductions | Already device-agnostic |
| 138 | test_histc | device-agnostic | TestReductions | Already device-agnostic |
| 139 | test_histc_lowp | CPU-only | TestReductionsOnCPU | `@onlyCPU` + low-precision histc; dtype loop over `(bfloat16, half)` |
| 140 | test_histc_min_max_errors | device-agnostic | TestReductions | Already device-agnostic |
| 141 | test_histc_min_max_corner_cases | device-agnostic | TestReductions | Already device-agnostic |
| 142 | test_histc_value_corner_cases | CPU-only* | TestReductions | `@onlyCPU` (CPU-only signal); landed PR kept it `@onlyCPU` in `TestReductions`. Accept either this or extraction to `TestReductionsOnCPU` |
| 143 | test_histc_min_max_corner_cases_cuda | device-agnostic | TestReductions | **Renamed** `_cuda`→`_device`; `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 144 | test_histogram | CPU-only | TestReductionsOnCPU | numpy-bound, no GPU value |
| 145 | test_histogramdd | CPU-only | TestReductionsOnCPU | numpy-bound |
| 146 | test_histogram_error_handling | CPU-only | TestReductionsOnCPU | CPU-only error messages; explicit `device="cpu"` |
| 147 | test_tensor_compare_ops_empty | device-agnostic | TestReductions | Already device-agnostic |
| 148 | test_tensor_compare_ops_argmax_argmix_kthvalue_dim_empty | device-agnostic | TestReductions | Already device-agnostic |
| 149 | test_tensor_reduce_ops_empty | device-agnostic | TestReductions | Already device-agnostic |
| 150 | test_reduction_empty_any_all | device-agnostic | TestReductions | Already device-agnostic |
| 151 | test_reduce_dtype | device-agnostic | TestReductions | Already device-agnostic |
| 152 | test_reference_masked | device-agnostic | TestReductions | Already device-agnostic |
| 153 | test_reductions_large_half_tensors | device-agnostic | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator` (no `@skipIfMPS`; uses `@dtypesIfXPU`) |
| 154 | test_scalar_tensor_as_dim_argument | device-agnostic | TestReductions | Already device-agnostic |
| 155 | test_scalar_tensor_dim_compiled_mode | device-agnostic | TestReductions | Already device-agnostic |

Strategy summary (155 standalone tests):

| Strategy | Count | Target class |
|----------|-------|--------------|
| CPU-only | 12 | TestReductionsOnCPU |
| CPU-only* (borderline) | 1 | TestReductions (retained `@onlyCPU`) |
| device-agnostic | 142 | TestReductions |
| device-specific | 0 | — |

The **6 nested inner `def test_*` helper functions** (not standalone tests; already counted in their parent tests):

| Pre line | Nested helper | Parent test (classification) |
|----------|---------------|------------------------------|
| 1103 | `test_for_dtypes(x_ty, v_ty, i_ty, message)` | `test_mode_wrong_dtype` (device-agnostic) |
| 1692 | `test_output_dtype(dtype, is_int32)` | `test_bucketization` (device-agnostic) |
| 1728 | `test_dtype_bfloat16(...)` | `test_bucketization` (device-agnostic) |
| 2413 | `test_multidim(x, dim)` | `test_dim_reduction_fns` (device-agnostic) |
| 3385 | `test_against_np(tensor, bins=100, ...)` | `test_histc` (device-agnostic) |
| 3990 | `test_reduction(op, has_no_dim, ...)` | `test_reduce_dtype` (device-agnostic) |

### Expected Class Splits

The analyst should recommend extracting CPU-only tests into a new class:

| New class | Strategy | Base class | Instantiation |
|-----------|----------|-----------|---------------|
| TestReductionsOnCPU | CPU-only | TestCase | None — plain `TestCase`, discovered by `run_tests()`. MUST NOT use `instantiate_device_type_tests` (CPU-only/device-specific convention) |

Note: the task brief called this class `TestReductionsCPU`. The landed
PR #185881 uses **`TestReductionsOnCPU`** — the gold label uses the
landed name to avoid false negatives. A plain `TestCase` (no
instantiation) is the gold mechanism here; `@instantiate_parametrized_tests`
would also be acceptable per the CPU-only convention if the tests were
parametrized, but the landed PR does not instantiate this class.

**Tests to move to TestReductionsOnCPU** (12 total):

`test_max_elementwise`, `test_min_elementwise`, `test_sum_integer_upcast`,
`test_prod_integer_upcast`, `test_cumsum_integer_upcast`,
`test_cumprod_integer_upcast`, `test_accreal_type`, `test_nansum_complex`,
`test_histc_lowp`, `test_histogram`, `test_histogramdd`,
`test_histogram_error_handling`.

Mechanism details from the landed PR: `@onlyCPU` and `device`/`dtype`
params are dropped; dtype-driven iteration uses plain `for` loops over
dtypes (e.g. `test_histc_lowp` loops `(torch.bfloat16, torch.half)`) or
fixed `torch.float32` tensor construction; `test_max_elementwise` gained
`@torch.compile`.

**Tests to remain in TestReductions** (143 total):

All device-agnostic tests (110 unchanged + 18 `@onlyCPU`→device-agnostic conversions + 14
`@onlyOn`/`@onlyCUDA`→`@onlyAccelerator` conversions incl. the renamed
`test_histc_min_max_corner_cases_device`) plus `test_histc_value_corner_cases`
(retained `@onlyCPU`). Instantiation unchanged:
`instantiate_device_type_tests(TestReductions, globals(), allow_xpu=True, allow_mps=True)`.

**`@skipIfMPS` gold check**: exactly **30** `@skipIfMPS` additions across
converted tests (29 in the decorator diff + 1 on the renamed
`test_histc_min_max_corner_cases_device`), matching `_check_skipifmps_coverage()`.
Exceptions: `test_mean_out_is_alias_of_return` gains `@dtypesIfMPS(...)`
instead, and `test_reductions_large_half_tensors` gains `@onlyAccelerator`
only.

### Expected Stale Symbols

After refactoring, these should be flagged for removal / added:

| Symbol | Location | Expected action |
|--------|----------|-----------------|
| device_type (module-level) | Lines 35-37: `device_type = (acc.type if (acc := torch.accelerator.current_accelerator(True)) else "cpu")` | **REMOVE** — all usages (line 848 `_test_dim_ops`; 1599-1600 `test_max_mixed_devices`; 1610-1611 `test_min_mixed_devices`) converted to a `device` param. Do NOT confuse with `self.device_type` (instance attr from `instantiate_device_type_tests`), which is legitimately still used (lines 1661/1727/2200/2817/2858 pre) |
| TEST_CUDA | Import | **NOT PRESENT** in this file — no `TEST_CUDA`/`TEST_MPS`/`TEST_XPU` import exists. The task brief's assumption does not hold for `test_reductions.py`; do not expect or flag this symbol |
| onlyCPU | Import (line 29) | **RETAIN** — `test_histc_value_corner_cases` (3445) keeps `@onlyCPU`, so the import must stay. (The brief assumed full `@onlyCPU` conversion; that is not what the landed PR did.) |
| onlyOn | Import (line 29) | **REMOVE** — all 13 `@onlyOn(["cuda","xpu"])` tests widened to `@onlyAccelerator` (+ the renamed histc test). Landed PR drops `onlyOn` |
| onlyCUDA | Import (line 29) | **REMOVE** — used only by `test_reduction_vectorize_along_output` (2566), widened to `@onlyAccelerator`. Landed PR drops `onlyCUDA` |
| onlyAccelerator | Not imported in pre | **ADD** — landed PR adds it to the `common_device_type` import |

Additional gold facts (imports/global structure):

- **One rename**: `test_histc_min_max_corner_cases_cuda` →
  `test_histc_min_max_corner_cases_device`. **Zero add/delete** — test
  count (155) preserved, satisfying verification check #2.
- File: 4110 lines pre → 4112 lines post.
- Two classes post: `TestReductions` (line 102) + `TestReductionsOnCPU` (line 3666).

## Agent Runbook

Follow these steps to evaluate a workflow run.

### Prerequisites

- A clean checkout of `test_reductions.py` at commit 545b05f
  (before the refactoring PR)
- The pytorch-test-refactoring skill available

### Steps

1. **Reset the test file to pre-refactoring state**

```bash
cd /root/pytorch && git checkout 545b05f5cc4 -- test/test_reductions.py
```

2. **Invoke the pytorch-test-refactoring skill**

Use the Skill tool:
- skill: "pytorch-test-refactoring"
- args: "test/test_reductions.py"

Follow the SKILL.md loop:
- Run orchestrator → read JSON → spawn/message agents → feed results → repeat
- Continue until the orchestrator emits "done" with phase="finalize"

3. **Read the output artifacts**

```
agent_space/refactor/test_reductions/
├── analyst_report.json    ← primary: classification accuracy
├── verification.json      ← primary: check pass/fail
├── review_findings.json   ← primary: finding count/severity
├── final_summary.md       ← secondary: class layout, test counts
└── coder_tasks.json       ← secondary: rule distribution
```

4. **Compare analyst classifications against gold labels**

For each test method in the Gold Labels table:
- Find the test in the refactored file — which class is it in?
- Look up `analyst_report.json` → `strategy_assignments` for that class
- Compare: does the analyst's strategy match the gold label?
- If gold expects CPU-only extraction, check `analyst_report.json` → `new_classes`

Record mismatches:
```
MISMATCH: test_histogram → analyst classified as device-agnostic, gold says CPU-only
MISMATCH: test_sum_parallel → analyst classified as CPU-only, gold says device-agnostic
MISSING: TestReductionsOnCPU not in new_classes
```

5. **Compute per-strategy metrics**

Count true positives, false positives, false negatives per strategy.
Compute precision, recall, F1.

```
Strategy  |  TP  |  FP  |  FN  |  Precision  |  Recall  |  F1
CPU-only        |  10  |   2  |   3  |     0.83    |   0.77   | 0.80
device-agnostic        | 140  |   3  |   2  |     0.98    |   0.99   | 0.98
device-specific        |   0  |   0  |   0  |      N/A    |    N/A   |  N/A
──────────────────────────────────────────────────────────────
Overall accuracy: 93.8% (150/160 with device-specific excluded)
```

Note: device-specific F1 is N/A when there are no device-specific tests in the file. For this
file, device-specific is always N/A (0 device-specific tests).

6. **Compare verification results**

Read `verification.json`. For each check, note PASS or FAIL:

```
Check                       Result   Notes
syntax                      PASS
test_count                  PASS     161/161 match
class_structure             PASS
decorateinfo_alignment      PASS
external_refs               PASS
stale_patterns              PASS
import_audit                PASS
dtype_integrity             PASS
accelerator_safety          PASS
coverage_preservation       WARN     scope broadening on test_foo
class_split                 PASS     TestReductionsOnCPU created
skipifmps_coverage          PASS
─────────────────────────────────────────
Failed checks: 0
```

7. **Review review findings**

Read `review_findings.json`. Count by severity:

```
Severity  |  Count
major     |    0
minor     |    2
info      |    1
────────────────
Total     |    3
```

8. **Produce the comparison report**

Fill in this table:

```markdown
### Evaluation

| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| Classification accuracy | XX% | YY% | ±Zpp |
| CPU-only F1 | X.XX | Y.YY | ±Z |
| device-agnostic F1 | X.XX | Y.YY | ±Z |
| device-specific F1 | N/A | N/A | — |
| Class split detected | Yes/No | Yes/No | — |
| Verification failures | N | M | ±K |
| Review findings | N (X major) | M (Y major) | ±K |
```

On first run, "Before" is "—". On subsequent runs, "Before" is the
previous run's values.

9. **Issue a verdict**

- ✅ **IMPROVEMENT** — at least one key metric improved, no regressions
- ⚠️ **MIXED** — some metrics improved, others regressed (needs human judgment)
- ❌ **REGRESSION** — accuracy drop >3pp, new verification failure, or class split miss
