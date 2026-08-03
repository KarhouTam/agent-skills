# Evaluation Guide: test_reductions.py

## Methodology

This guide measures whether a workflow change improves or regresses
refactoring quality on `test_reductions.py`. An agent follows this
guide after running the pytorch-test-refactoring skill to completion.

### What is evaluated

1. **Analyst classification accuracy** — does the analyst assign the
   correct strategy (S1/S2/S3) to each test?
2. **Class split detection** — does the analyst recommend extracting
   S1 tests into a separate class when appropriate?
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

All gold labels are S1 or S2. **S3 = 0** — no test in this file uses
Category C device-specific APIs (no NCCL, cuDNN, NVTX, GDS, etc.).

| # | Test method | Strategy | Target class | Notes |
|---|------------|----------|-------------|-------|
| 1 | test_dim_default | S2 | TestReductions | Already device-agnostic |
| 2 | test_dim_default_keepdim | S2 | TestReductions | Already device-agnostic |
| 3 | test_dim_none | S2 | TestReductions | Already device-agnostic |
| 4 | test_dim_none_keepdim | S2 | TestReductions | Already device-agnostic |
| 5 | test_dim_single | S2 | TestReductions | Already device-agnostic |
| 6 | test_dim_single_keepdim | S2 | TestReductions | Already device-agnostic |
| 7 | test_dim_empty | S2 | TestReductions | Already device-agnostic |
| 8 | test_dim_empty_keepdim | S2 | TestReductions | Already device-agnostic |
| 9 | test_dim_multi | S2 | TestReductions | Already device-agnostic |
| 10 | test_dim_multi_keepdim | S2 | TestReductions | Already device-agnostic |
| 11 | test_dim_multi_unsorted | S2 | TestReductions | Already device-agnostic |
| 12 | test_dim_multi_unsorted_keepdim | S2 | TestReductions | Already device-agnostic |
| 13 | test_dim_multi_duplicate | S2 | TestReductions | Already device-agnostic |
| 14 | test_dim_multi_unsupported | S2 | TestReductions | Already device-agnostic |
| 15 | test_dim_offbounds | S2 | TestReductions | Already device-agnostic |
| 16 | test_dim_ndim_limit | S2 | TestReductions | Already device-agnostic |
| 17 | test_identity | S2 | TestReductions | Already device-agnostic |
| 18 | test_nan_policy_propagate | S2 | TestReductions | Already device-agnostic |
| 19 | test_nan_policy_omit | S2 | TestReductions | Already device-agnostic |
| 20 | test_result_dtype | S2 | TestReductions | Already device-agnostic |
| 21 | test_empty_tensor_empty_slice | S2 | TestReductions | Already device-agnostic |
| 22 | test_empty_tensor_nonempty_slice | S2 | TestReductions | Already device-agnostic |
| 23 | test_noncontiguous_innermost | S2 | TestReductions | Already device-agnostic |
| 24 | test_noncontiguous_outermost | S2 | TestReductions | Already device-agnostic |
| 25 | test_noncontiguous_all | S2 | TestReductions | Already device-agnostic |
| 26 | test_noncontiguous_transposed | S2 | TestReductions | Already device-agnostic |
| 27 | test_noncontiguous_expanded | S2 | TestReductions | Already device-agnostic |
| 28 | test_ref_scalar_input | S2 | TestReductions | Already device-agnostic |
| 29 | test_ref_small_input | S2 | TestReductions | Already device-agnostic |
| 30 | test_ref_large_input_1D | S2 | TestReductions | Already device-agnostic |
| 31 | test_ref_large_input_2D | S2 | TestReductions | Already device-agnostic |
| 32 | test_ref_large_input_64bit_indexing | S2 | TestReductions | Already device-agnostic |
| 33 | test_ref_duplicate_values | S2 | TestReductions | Already device-agnostic |
| 34 | test_ref_extremal_values | S2 | TestReductions | Already device-agnostic |
| 35 | test_var_unbiased | S2 | TestReductions | Already device-agnostic |
| 36 | test_var_stability | S2 | TestReductions | Already device-agnostic |
| 37 | test_sum_dim_reduction_uint8_overflow | S2 | TestReductions | Already device-agnostic |
| 38 | test_dim_reduction_less_than_64 | S2 | TestReductions | Already device-agnostic |
| 39 | test_dim_reduction_lastdim | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 40 | test_logsumexp | S2 | TestReductions | Already device-agnostic |
| 41 | test_logsumexp_integral_promotion | S2 | TestReductions | Already device-agnostic |
| 42 | test_logcumsumexp_complex | S2 | TestReductions | Already device-agnostic |
| 43 | test_sum_parallel | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` (trivial `.to(device)`) |
| 44 | test_max_elementwise | S1 | TestReductionsOnCPU | Uses `_testCSelection` → CPU-only `Tensor.map2_`; not mechanically convertible |
| 45 | test_min_elementwise | S1 | TestReductionsOnCPU | Same `map2_` CPU-only helper |
| 46 | test_all_any | S2 | TestReductions | Already device-agnostic |
| 47 | test_all_any_with_dim | S2 | TestReductions | Already device-agnostic |
| 48 | test_numpy_named_args | S2 | TestReductions | Helper `_test_dim_ops` converted to `device` param |
| 49 | test_sum_dim | S2 | TestReductions | `@slowTest @onlyCPU` → `@slowTest @skipIfMPS` |
| 50 | test_mean_dim | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 51 | test_std_dim | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 52 | test_var_dim | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 53 | test_logsumexp_dim | S2 | TestReductions | `@onlyCPU @skipIfNoSciPy` → `@skipIfNoSciPy @skipIfMPS` |
| 54 | test_mean_int_with_optdtype | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 55 | test_mean_out_is_alias_of_return | S2 | TestReductions | `@onlyCPU` → `@dtypesIfMPS(...)` (dtype-narrowing, not skip) |
| 56 | test_sum_integer_upcast | S1 | TestReductionsOnCPU | `get_all_math_dtypes('cpu')` — CPU-specific dtype enumeration |
| 57 | test_prod_integer_upcast | S1 | TestReductionsOnCPU | Same helper |
| 58 | test_cumsum_integer_upcast | S1 | TestReductionsOnCPU | Same helper |
| 59 | test_cumprod_integer_upcast | S1 | TestReductionsOnCPU | Same helper |
| 60 | test_mode | S2 | TestReductions | Already device-agnostic |
| 61 | test_mode_large | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 62 | test_mode_boolean | S2 | TestReductions | Already device-agnostic |
| 63 | test_mode_wrong_dtype | S2 | TestReductions | Already device-agnostic |
| 64 | test_mode_wrong_device | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 65 | test_accreal_type | S1 | TestReductionsOnCPU | `@onlyCPU`; "TODO: make work on CUDA, too" — accreal semantics CPU-only |
| 66 | test_var_mean_some_dims | S2 | TestReductions | Already device-agnostic |
| 67 | test_all_any_empty | S2 | TestReductions | Already device-agnostic |
| 68 | test_all_issue117215 | S2 | TestReductions | Already device-agnostic |
| 69 | test_max_with_inf | S2 | TestReductions | Already device-agnostic |
| 70 | test_min_with_inf | S2 | TestReductions | Already device-agnostic |
| 71 | test_max | S2 | TestReductions | Already device-agnostic |
| 72 | test_min | S2 | TestReductions | Already device-agnostic |
| 73 | test_amin | S2 | TestReductions | Already device-agnostic |
| 74 | test_amax | S2 | TestReductions | Already device-agnostic |
| 75 | test_aminmax | S2 | TestReductions | Already device-agnostic |
| 76 | test_invalid_0dim_aminmax | S2 | TestReductions | Already device-agnostic |
| 77 | test_bincount | S2 | TestReductions | Already device-agnostic |
| 78 | test_var_stability2 | S2 | TestReductions | Already device-agnostic |
| 79 | test_sum_noncontig_lowp | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 80 | test_sum_all | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 81 | test_sum_out | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 82 | test_prod_gpu | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 83 | test_prod | S2 | TestReductions | `@onlyCPU @dtypes(torch.float)` → `@dtypes(torch.float) @skipIfMPS` |
| 84 | test_prod_lowp | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 85 | test_prod_bool | S2 | TestReductions | Already device-agnostic |
| 86 | test_max_mixed_devices | S2 | TestReductions | `@onlyCPU` → `@onlyAccelerator @skipIfMPS`; uses `.to(device)` |
| 87 | test_min_mixed_devices | S2 | TestReductions | `@onlyCPU` → `@onlyAccelerator @skipIfMPS` |
| 88 | test_bucketization | S2 | TestReductions | Already device-agnostic |
| 89 | test_nansum | S2 | TestReductions | Already device-agnostic |
| 90 | test_count_nonzero | S2 | TestReductions | Already device-agnostic |
| 91 | test_sum_vs_numpy | S2 | TestReductions | Already device-agnostic |
| 92 | test_nansum_vs_numpy | S2 | TestReductions | Already device-agnostic |
| 93 | test_nansum_complex | S1 | TestReductionsOnCPU | `@onlyCPU`; CPU-specific error-message assertion |
| 94 | test_nansum_out_dtype | S2 | TestReductions | Already device-agnostic |
| 95 | test_nansum_int_out_dtype_float_input | S2 | TestReductions | Already device-agnostic |
| 96 | test_nansum_int_out_dtype_matches_inductor | S2 | TestReductions | `@onlyCPU` → `@skipIfMPS` |
| 97 | test_argminmax_multiple | S2 | TestReductions | Already device-agnostic |
| 98 | test_all_any_vs_numpy | S2 | TestReductions | Already device-agnostic |
| 99 | test_repeated_dim | S2 | TestReductions | Already device-agnostic |
| 100 | test_var | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 101 | test_var_large_input | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 102 | test_sum_noncontig | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 103 | test_min_max_nan | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 104 | test_sum_cpu_device_mismatch | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 105 | test_minmax_illegal_dtype | S2 | TestReductions | Already device-agnostic |
| 106 | test_dim_arg_reduction_scalar | S2 | TestReductions | Already device-agnostic |
| 107 | test_dim_reduction | S2 | TestReductions | Already device-agnostic |
| 108 | test_nanmean_integral_types | S2 | TestReductions | `@onlyCPU @dtypes(...)` → `@dtypes(...) @skipIfMPS` |
| 109 | test_dim_reduction_fns | S2 | TestReductions | Already device-agnostic |
| 110 | test_reduction_split | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 111 | test_reduction_vectorize_along_input_corner | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 112 | test_reduction_vectorize_along_output | S2 | TestReductions | `@onlyCUDA` → `@onlyAccelerator @skipIfMPS` (only `@onlyCUDA` in file) |
| 113 | test_argminmax_large_axis | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 114 | test_argminmax_axis_with_dim_one | S2 | TestReductions | Already device-agnostic |
| 115 | test_median_real_values | S2 | TestReductions | Already device-agnostic |
| 116 | test_median_nan_values | S2 | TestReductions | Already device-agnostic |
| 117 | test_median_corner_cases | S2 | TestReductions | Already device-agnostic |
| 118 | test_quantile | S2 | TestReductions | Already device-agnostic |
| 119 | test_quantile_backward | S2 | TestReductions | Already device-agnostic |
| 120 | test_quantile_error | S2 | TestReductions | Already device-agnostic |
| 121 | test_quantile_large_input | S2 | TestReductions | Already device-agnostic |
| 122 | test_quantile_size_limit | S2 | TestReductions | Already device-agnostic |
| 123 | test_quantile_partial_selection | S2 | TestReductions | Already device-agnostic |
| 124 | test_quantile_partial_selection_autograd | S2 | TestReductions | Already device-agnostic |
| 125 | test_std_mean | S2 | TestReductions | Already device-agnostic |
| 126 | test_std_mean_all_dims | S2 | TestReductions | Already device-agnostic |
| 127 | test_var_mean | S2 | TestReductions | Already device-agnostic |
| 128 | test_var_mean_all_dims | S2 | TestReductions | Already device-agnostic |
| 129 | test_std_mean_some_dims | S2 | TestReductions | Already device-agnostic |
| 130 | test_var_vs_numpy | S2 | TestReductions | Already device-agnostic |
| 131 | test_std_vs_numpy | S2 | TestReductions | Already device-agnostic |
| 132 | test_var_correction_vs_numpy | S2 | TestReductions | Already device-agnostic |
| 133 | test_std_correction_vs_numpy | S2 | TestReductions | Already device-agnostic |
| 134 | test_std_mean_correction | S2 | TestReductions | Already device-agnostic |
| 135 | test_var_mean_correction | S2 | TestReductions | Already device-agnostic |
| 136 | test_warn_invalid_degrees_of_freedom | S2 | TestReductions | Already device-agnostic |
| 137 | test_amin_amax_some_dims | S2 | TestReductions | Already device-agnostic |
| 138 | test_histc | S2 | TestReductions | Already device-agnostic |
| 139 | test_histc_lowp | S1 | TestReductionsOnCPU | `@onlyCPU` + low-precision histc; dtype loop over `(bfloat16, half)` |
| 140 | test_histc_min_max_errors | S2 | TestReductions | Already device-agnostic |
| 141 | test_histc_min_max_corner_cases | S2 | TestReductions | Already device-agnostic |
| 142 | test_histc_value_corner_cases | S1* | TestReductions | `@onlyCPU` (S1 signal); landed PR kept it `@onlyCPU` in `TestReductions`. Accept either this or extraction to `TestReductionsOnCPU` |
| 143 | test_histc_min_max_corner_cases_cuda | S2 | TestReductions | **Renamed** `_cuda`→`_device`; `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator @skipIfMPS` |
| 144 | test_histogram | S1 | TestReductionsOnCPU | numpy-bound, no GPU value |
| 145 | test_histogramdd | S1 | TestReductionsOnCPU | numpy-bound |
| 146 | test_histogram_error_handling | S1 | TestReductionsOnCPU | CPU-only error messages; explicit `device="cpu"` |
| 147 | test_tensor_compare_ops_empty | S2 | TestReductions | Already device-agnostic |
| 148 | test_tensor_compare_ops_argmax_argmix_kthvalue_dim_empty | S2 | TestReductions | Already device-agnostic |
| 149 | test_tensor_reduce_ops_empty | S2 | TestReductions | Already device-agnostic |
| 150 | test_reduction_empty_any_all | S2 | TestReductions | Already device-agnostic |
| 151 | test_reduce_dtype | S2 | TestReductions | Already device-agnostic |
| 152 | test_reference_masked | S2 | TestReductions | Already device-agnostic |
| 153 | test_reductions_large_half_tensors | S2 | TestReductions | `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator` (no `@skipIfMPS`; uses `@dtypesIfXPU`) |
| 154 | test_scalar_tensor_as_dim_argument | S2 | TestReductions | Already device-agnostic |
| 155 | test_scalar_tensor_dim_compiled_mode | S2 | TestReductions | Already device-agnostic |

Strategy summary (155 standalone tests):

| Strategy | Count | Target class |
|----------|-------|--------------|
| S1 | 12 | TestReductionsOnCPU |
| S1* (borderline) | 1 | TestReductions (retained `@onlyCPU`) |
| S2 | 142 | TestReductions |
| S3 | 0 | — |

The **6 nested inner `def test_*` helper functions** (not standalone tests; already counted in their parent tests):

| Pre line | Nested helper | Parent test (classification) |
|----------|---------------|------------------------------|
| 1103 | `test_for_dtypes(x_ty, v_ty, i_ty, message)` | `test_mode_wrong_dtype` (S2) |
| 1692 | `test_output_dtype(dtype, is_int32)` | `test_bucketization` (S2) |
| 1728 | `test_dtype_bfloat16(...)` | `test_bucketization` (S2) |
| 2413 | `test_multidim(x, dim)` | `test_dim_reduction_fns` (S2) |
| 3385 | `test_against_np(tensor, bins=100, ...)` | `test_histc` (S2) |
| 3990 | `test_reduction(op, has_no_dim, ...)` | `test_reduce_dtype` (S2) |

### Expected Class Splits

The analyst should recommend extracting S1 tests into a new class:

| New class | Strategy | Base class | Instantiation |
|-----------|----------|-----------|---------------|
| TestReductionsOnCPU | S1 | TestCase | None — plain `TestCase`, discovered by `run_tests()`. MUST NOT use `instantiate_device_type_tests` (S1/S3 convention) |

Note: the task brief called this class `TestReductionsCPU`. The landed
PR #185881 uses **`TestReductionsOnCPU`** — the gold label uses the
landed name to avoid false negatives. A plain `TestCase` (no
instantiation) is the gold mechanism here; `@instantiate_parametrized_tests`
would also be acceptable per the S1 convention if the tests were
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

All S2 tests (110 unchanged + 18 `@onlyCPU`→S2 conversions + 14
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
- If gold expects S1 extraction, check `analyst_report.json` → `new_classes`

Record mismatches:
```
MISMATCH: test_histogram → analyst classified as S2, gold says S1
MISMATCH: test_sum_parallel → analyst classified as S1, gold says S2
MISSING: TestReductionsOnCPU not in new_classes
```

5. **Compute per-strategy metrics**

Count true positives, false positives, false negatives per strategy.
Compute precision, recall, F1.

```
Strategy  |  TP  |  FP  |  FN  |  Precision  |  Recall  |  F1
S1        |  10  |   2  |   3  |     0.83    |   0.77   | 0.80
S2        | 140  |   3  |   2  |     0.98    |   0.99   | 0.98
S3        |   0  |   0  |   0  |      N/A    |    N/A   |  N/A
──────────────────────────────────────────────────────────────
Overall accuracy: 93.8% (150/160 with S3 excluded)
```

Note: S3 F1 is N/A when there are no S3 tests in the file. For this
file, S3 is always N/A (0 S3 tests).

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
| S1 F1 | X.XX | Y.YY | ±Z |
| S2 F1 | X.XX | Y.YY | ±Z |
| S3 F1 | N/A | N/A | — |
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
