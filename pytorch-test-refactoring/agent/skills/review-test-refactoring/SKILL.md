---
name: review-test-refactoring
description: >
  Review PyTorch test refactoring for correctness and completeness against the
  decoupling standards defined in the refactor-test-decoupling skill. Accepts a
  test file path (whole-file review), a PR URL, a git diff, or a branch name.
  Use this when asked to review a test refactoring PR, check a test decoupling
  change, verify a refactored test file, review a single test file for
  classification correctness, or when the user mentions "review" in the context
  of test splitting, test decoupling, test refactoring, or device-agnostic
  test migration. Also use when the user opens a PR, diff, or Python test file
  and asks for a quality check.
  General test-scope criteria (review philosophy, backward compatibility,
  testing patterns, security, thread safety, general code quality) are adapted
  from the PyTorch pr-review skill and live in Part II.
---

# Review Test Refactoring

Review a test file (or a PR/diff) against the decoupling standards defined in
the `refactor-test-decoupling` skill. The review checks classification
correctness, naming conventions, API replacements, instantiation mechanisms,
and completeness.

This document has two parts. **Part I** is the decoupling criteria: what a test
file must look like after refactoring. **Part II** is the general criteria that
apply to any diff touching only test files — review philosophy, workflow,
backward compatibility, testing patterns, security, thread safety, and general
code quality — adapted from the PyTorch `pr-review` skill. A review applies
both.

## How to Use This Skill

### Choose Your Mode

This skill supports two review modes. Determine which applies:

| Input | Mode | What to Review |
|-------|------|----------------|
| A test file path (`test/test_ops.py`) | **Whole-file review** | Audit the entire file against decoupling standards |
| A PR URL, branch name, or `git diff` | **Diff-based review** | Review only the changed portions |

### For Whole-File Review (test file path)

1. **Read the entire test file.** Load the full file — you are auditing every
   class and test method, not just a diff.

2. **Read `../../../reference/device_api_catalog.yaml`.** This catalog is the
   authoritative reference for whether a device API is Category A (has
   `torch.accelerator` equivalent), Category B (general cross-backend concept),
   or Category C (truly device-specific). Every API classification decision in
   the review must be grounded in this catalog. See
   `../../../reference/classification_guide.md` for lookup instructions.

3. Run through the full checklist below, applying every check to every class
   and test method in the file. There is no "before" version to compare against
   — you are the auditor.

### For Diff-Based Review (PR, branch, or diff)

1. Identify the changes to review. If the user provides a PR URL, fetch it with
   `gh`. Otherwise, use `git diff` against the base branch (usually `origin/main`).

2. **Read `../../../reference/device_api_catalog.yaml`** (same as above).

3. For each changed test file, run through the checklist below. Focus on the
   diff — you are reviewing what changed, not re-auditing the entire file.
   However, key checks like naming conventions, instantiation mechanisms, and
   import cleanliness should be verified for the file as a whole even in
   diff-based mode.

### Reporting

Report findings organized by severity:
- **Blocker**: Test loss, wrong classification locking tests out of accelerators,
  broken instantiation (class won't run).
- **Major**: Wrong naming convention, wrong instantiation mechanism,
  stale imports that keep file classified as device_specific.
- **Minor**: Style issues, missed cleanup opportunities, suboptimal
  decorator ordering.

How these map onto the per-PR verdict, and how the verdict is computed, is
defined in **II.11**. The severity of a finding is decided by the same question
there: would a maintainer send this back?

## Reference: The Device API Catalog

`../../../reference/device_api_catalog.yaml` classifies every PyTorch device API into three categories. Always consult it when reviewing classifications — never rely on memory or heuristics.

| Category | Description | Strategy Implication |
|----------|-------------|---------------------|
| **A** | APIs with `torch.accelerator` equivalents | **NOT device-specific** → device-agnostic |
| **B** | General cross-backend concepts, no wrapper yet | **NOT device-specific** → device-agnostic |
| **C** | Truly device-specific, no cross-device equivalent | **device-specific only** |

**Rule**: Only Category C APIs justify device-specific. If a test uses only Category A or B APIs, it must be device-agnostic with `@onlyAccelerator`.

## Part I — Decoupling Criteria (Review Checklist)

### 1. Classification Correctness

The single most impactful category of review finding. A wrong classification
either locks tests out of accelerators they could run on (device-agnostic
misclassified as device-specific) or causes test failures on accelerators that lack
the required features (device-specific misclassified as device-agnostic).

#### 1a. False-CUDA Detection (most common error)

For every test classified as device-specific (`TestFooCUDA`) or using
`@onlyCUDA` / `device="cuda"`, ask:

> Is this test verifying a truly device-specific feature (Category C in the
> report), or is it just using CUDA as a device for generic computation?

**How to check**: Look up each `torch.cuda.*` API the test uses in
`../../../reference/device_api_catalog.yaml`. If every API it uses is Category A or B,
the test is misclassified — it should be device-agnostic with `@onlyAccelerator`.

**Red flags** (signals the test is wrongly classified as CUDA-specific):

| Code Pattern | What It Means | Severity |
|-------------|---------------|----------|
| Test with generic ops (add, softmax, matmul, loss) still has `@onlyCUDA` or `device="cuda"` | Should be device-agnostic with `@onlyAccelerator` | Blocker |
| `.cuda()` / `.to("cuda")` used instead of `.to(device)` | Test hardcodes CUDA for no reason | Blocker |
| `torch.cuda.<api>` call where the catalog shows `torch.accelerator.<api>` exists | Category A — has cross-accelerator equivalent; replace with `torch.accelerator.*` | Major |
| `torch.cuda.Stream` / `torch.cuda.Event` used but test not marked as device-specific | Category B — general concept; verify usage context, usually device-agnostic | Info |
| `TEST_CUDA` import remains but no device-specific CUDA tests exist in the file | Stale import keeps file classified as device_specific | Major |

**Unnecessary `@onlyAccelerator`**: If `@onlyAccelerator` was ADDED to a test that had no prior device restriction, verify the test genuinely requires an accelerator. If it works on CPU, the restriction should have been removed entirely.

#### 1b. Over-generalization Detection

Conversely, check that tests using Category C APIs were NOT incorrectly
generalized to device-agnostic. Consult `../../../reference/device_api_catalog.yaml` → `category_c` for the full per-backend lists. Key examples:

| Code Pattern | What It Means | Severity |
|-------------|---------------|----------|
| Test using any API from `category_c.cuda` in the catalog but placed in `TestFooDevice` with `@onlyAccelerator` | Will fail on non-CUDA accelerators | Blocker |
| Test using any API from `category_c.mps` in the catalog but placed in `TestFooDevice` | Will fail on non-MPS accelerators | Blocker |
| Test using any API from `category_c.xpu` in the catalog but placed in `TestFooDevice` | Will fail on non-XPU accelerators | Blocker |

**Dtype compatibility on MPS**: Even when a test uses only generic ops (no
Category C APIs), it may still fail on non-CUDA accelerators if it uses dtypes
not supported by that backend. The most common case: `complex128` and `float64`
are unsupported on MPS. When a test is generalized to device-agnostic (or already
uses `@onlyAccelerator`):

| Check | How to Verify |
|-------|---------------|
| `complex128` or `torch.complex128` used in `@dtypes` or as default dtype | MPS does not support double-precision complex. Add `@expectedFailureMPS` or use `@dtypesIfMPS` to exclude `complex128`. |
| `float64` or `torch.float64` used in `@dtypes` or as default dtype | MPS does not support float64. Add `@expectedFailureMPS` or use `@dtypesIfMPS` to exclude `float64`. |
| `torch.long` used with MPS convolution/indexing ops | MPS has limited int64 support in some ops. Add a skip (`@skipIfMPS`) if needed. |

**How to validate**: For every test using `@onlyAccelerator` or the `device`
parameter, verify every dtype it exercises (from `@dtypes`, `_default_dtype`, or
inline tensor creation) against known MPS dtype limitations. The failure
signature is: `"Cannot convert a MPS Tensor to float64 dtype"` or similar dtype
conversion errors on MPS. In diff-based mode, pay special attention to tests
where `@onlyCUDA` was removed.

**MPS coverage safety**: When MPS coverage is broadened (new `allow_mps=True` or `@onlyAccelerator` replacing CUDA-only restriction), verify `@skipIfMPS` is present unless MPS was already covered via `@dtypesIfMPS` or `@onlyMPS`. Exception: `@skipIfMPS` is NOT required when the class is NOT instantiated for MPS — MPS variants are only created when the class's `instantiate_device_type_tests` call passes `allow_mps=True`. If no MPS variant exists, the test cannot run on MPS and the skip is unnecessary.

**@onlyCPU to device-agnostic**: Verify each `@onlyCPU` test was individually evaluated (not bulk-decided). Check that `device` param was added when `@onlyCPU` was removed.

#### 1c. CPU-only Correctness

For tests in a CPU-only class (`TestFoo` without device suffix):

| Check | What to Look For |
|-------|-----------------|
| No `device` parameter in method signature | `def test_foo(self)` not `def test_foo(self, device)` |
| No device-dependent decorators | No `@onlyCUDA`, `@onlyAccelerator`, `@skipCUDAIf`, etc. |
| No `.to(device)`, `.cuda()`, `device=device` in test body | All tensors are CPU |
| No cross-device tensor operations | Can't have CPU tensor op GPU tensor |

### 2. Naming Convention

Class renaming is **OPTIONAL**. The future `hw_classification` member on TestCase will handle strategy classification; class names are no longer the primary discriminator. The coder decides whether to rename based on external reference impact (see `refactor-test-decoupling` for the decision framework).

**Do NOT flag a class name mismatch as an issue unless it is actively misleading** (e.g., a CPU-only class named `TestFooCUDA`). A class using the original name with the correct strategy mechanism is valid.

For reference, the recommended naming convention (when coder chooses to rename):

| Strategy | Recommended Name | Acceptable Alternative |
|----------|-----------------|----------------------|
| CPU-only | `TestFoo` (original name) | Must NOT have device suffix |
| device-agnostic | `TestFooDevice` | Original name is fine |
| device-specific | Original name (`instantiate_device_type_tests` appends the device suffix) | Original name is fine |

#### 2a. Cross-File Reference Integrity

**If the coder kept the original class names, skip this check** — no external references need updating. This is the primary benefit of not renaming.

**When a class IS renamed** (e.g., `TestIndexing` → `TestIndexingDevice`), the
old name may still be referenced in external configuration files. A rename
without updating these files causes CI breakage: dynamo expected-failure entries
stop matching, turning expected failures into unexpected failures.

**Files to check for stale class name references:**

| File/Directory | Example Reference | How to Verify |
|---------------|-------------------|---------------|
| `test/dynamo_skips/` | `TestIndexing.test_invalid_sparse_coo_values_cpu` | `find test/dynamo_skips/ -name "OldClassName*"` **(by filename, NOT grep — these are sentinel files, often 0 bytes)** |
| `test/dynamo_expected_failures/` | `TestIndexingCPU.test_byte_mask_cpu` | `find test/dynamo_expected_failures/ -name "OldClassName*"` **(by filename, NOT grep)** |
| `test/inductor_expected_failures/` | `TestIndexing.test_foo` | `find test/inductor_expected_failures/ -name "OldClassName*"` **(by filename, NOT grep)** |
| `torch/testing/_internal/common_methods_invocations.py` | `DecorateInfo(unittest.skip("..."), 'TestCommon', 'test_complex_half_reference_testing')` | Search for `'OldClassName'` string in `DecorateInfo(...)` constructor calls |
| `.ci/pytorch/test_exclude_list.py` | Test name in skip list | `grep -r "OldClassName\b" .ci/pytorch/` |
| `.ci/pytorch/*-trunk.yml` | Test name in CI config | `grep -r "OldClassName\b" .ci/` |

**How to fix:** For each stale reference, update the class name to match the
new name. Verify which class actually owns each test — when a class is split
into multiple new classes (CPU-only + 2 + 3), tests may now live under
different class names (e.g., `TestIndexing.test_foo` might now be
`TestIndexingDevice.test_foo` or `TestIndexingCPU.test_foo`).

**For `common_methods_invocations.py` specifically:** `DecorateInfo` entries
use exact class name matching in `is_active()` — if `cls_name='TestCommon'`
but the test now lives in `TestCommonDevice`, the skip/xfail decorator is
silently dropped. To find broken entries:

```bash
python -c "
import torch
from torch.testing._internal.common_methods_invocations import op_db
from torch.testing._internal.opinfo.core import DecorateInfo

old_names = {'TestOldName1', 'TestOldName2'}  # fill in renamed classes
count = 0
for op in op_db:
    for d in op.decorators:
        if isinstance(d, DecorateInfo) and d.cls_name in old_names:
            print(f'{op.name}: cls_name={d.cls_name}, test_name={d.test_name}')
            count += 1
print(f'Total: {count} stale DecorateInfo entries')
"
```

Then replace the old class name string literal with the new one in
`torch/testing/_internal/common_methods_invocations.py`.

**Severity**: Blocker — broken DecorateInfo entries cause tests to silently
run when they should be skipped, or tests to be silently skipped when they
should run.

### 3. Instantiation Mechanism

| Strategy | Expected Mechanism | Wrong Mechanism |
|----------|-------------------|-----------------|
| CPU-only, no parametrization | Plain `TestCase` | `instantiate_device_type_tests` |
| CPU-only, with `@parametrize`/`@dtypes` | `@instantiate_parametrized_tests` | `instantiate_device_type_tests` |
| CPU-only, with `@ops` | `instantiate_device_type_tests(..., only_for="cpu")` | `@instantiate_parametrized_tests` |
| device-agnostic | `instantiate_device_type_tests(TestFooDevice, globals())` | `@instantiate_parametrized_tests` |
| device-specific | `instantiate_device_type_tests(..., only_for="<device>")` | Plain `TestCase` with `setUp` guard or `@instantiate_parametrized_tests` |

**Critical**: Check that `instantiate_device_type_tests` is never used for
CPU-only classes — it creates useless per-device variants.

**Critical**: Check that no class uses both `instantiate_parametrized_tests`
and `instantiate_device_type_tests` — double instantiation causes test name
collisions.

### 4. API Replacement Correctness

For device-agnostic tests, verify device-specific APIs were replaced with their
device-agnostic equivalents. **Consult `../../../reference/device_api_catalog.yaml` → `category_a` for the authoritative mapping.** The catalog defines every `torch.<device>.<api>` → `torch.accelerator.<api>` replacement.

**Key checks:**

| Before (Wrong) | After (Correct) | Check |
|---------------|-----------------|-------|
| `@onlyCUDA` | `@onlyAccelerator` | Not left as `@onlyCUDA` |
| `@unittest.skipIf(not TEST_CUDA, ...)` | `@onlyAccelerator` | Not left as skip |
| `device="cuda"` | `device` parameter | No hardcoded `"cuda"` |
| `.cuda()` / `.to("cuda")` | `.to(device)` | No `.cuda()` calls |

For any `torch.cuda.<api>()` call remaining in a device-agnostic test, check the
catalog: if Category A, it should be `torch.accelerator.<api>()`. If Category B,
use the unified type (e.g., `torch.Stream` instead of `torch.cuda.Stream`). If
Category C, the test belongs in device-specific.

**Return type compatibility**: Verify return type compatibility for all `torch.accelerator.*` replacements, especially HIGH RISK APIs: `current_device_index` (returns `int`, compare against `int`), `set_device_index` (takes `int` arg), `get_device_capability` (return type differs across backends). Consult `../../../reference/device_api_catalog.yaml` type annotations.

**Remaining `torch.cuda` in device-agnostic classes**: Scan each device-agnostic class for remaining `torch.cuda.*` calls — each must be either migrated to `torch.accelerator.*` or the test moved to device-specific.

### 5. Import Cleanup

| Check | How to Verify |
|-------|---------------|
| `TEST_CUDA` import removed if no device-specific CUDA tests remain | `grep "TEST_CUDA"` in the file |
| `TEST_MPS` import removed if no device-specific MPS tests remain | `grep "TEST_MPS"` in the file |
| `TEST_XPU` import removed if no device-specific XPU tests remain | `grep "TEST_XPU"` in the file |
| `@onlyCUDA` import removed if no device-specific CUDA tests remain | `grep "onlyCUDA"` in the imports |
| `@onlyOn` import removed if all uses were replaced | `grep "onlyOn"` in the file |
| `@onlyNativeDeviceTypes` removal | `@onlyNativeDeviceTypes` / `@onlyNativeDeviceTypesAnd` are redundant on device-agnostic classes — REMOVE them. Before removing, verify dtype compatibility (`float64`/`complex128`/channels-last may be unsupported on MPS/MTIA) and add `@skipIfMPS`/`@dtypesIfMPS` if needed. |
| New imports are correct | `onlyAccelerator` from `common_device_type`, `torch.accelerator` if used |

### 6. Test Completeness

**Whole-file review**: Verify every test method in the file is properly placed in
an appropriate strategy class. No test should be in a class that doesn't match
its device dependency level (e.g., a test using only CPU ops should not be in a
`TestFooCUDA` class).

| Check | How to Verify |
|-------|---------------|
| Every `def test_` belongs to the correct strategy class | Cross-reference each test's API usage against the catalog and its enclosing class name |
| Device instantiation present for device-specific | `instantiate_device_type_tests(..., only_for="<device>")` with a `device` param on each method |
| No test logic unintentionally modified | If reviewing a diff, compare test bodies against the base version. If whole-file, flag tests that appear incomplete or have empty bodies |
| No duplicate test bodies across device-specific classes | If identical test bodies appear across device-specific classes, they belong in the device-agnostic shared class |
| No device-specific artifacts in device-agnostic classes | Scan for `_cuda` suffix in test method names, internal variable names like `cuda_out`, module-level helpers with `if device_type == "<backend>"` branches — clean these when the test is in a device-agnostic class |

**Diff-based review**: Additionally verify that every original test method is
accounted for (count `def test_` in old vs new). A test "lost" in refactoring is
a regression.

### 7. Common Pitfalls

| Pitfall | Detection | Severity |
|---------|-----------|----------|
| `@onlyAccelerator` used as class decorator | `@onlyAccelerator\nclass TestFoo` — breaks `instantiate_device_type_tests` | Blocker |
| CPU-only class has `device` parameter | `def test_foo(self, device)` in `TestFoo` (no Device suffix) | Blocker |
| `skipIfXpu`/`skipIfCUDA` from `common_utils` in device-agnostic class | These skip ALL variants, not just the target device | Major |
| `GPU_TYPE`/`HAS_GPU` from `inductor_utils` not converted | Leftover inductor-specific device abstraction | Major |
| Mixed `device` param and hardcoded `"cuda"` in same class | Inconsistent; some tests use device param, others hardcode | Major |
| `instantiate_device_type_tests` call references wrong class name | Class name in `globals()` call doesn't match actual test class — class never instantiated | Blocker |
| `except_for`/`only_for`/`allow_mps`/`allow_xpu` args missing from instantiation | Device allowlists not applied to current `instantiate_device_type_tests` call | Major |
| Category A/B API treated as if it makes a test CUDA-specific | Test locked to CUDA unnecessarily; check the catalog | Major |
| Missing blacklist skip decorators | `@skipXPU`, `@skipMPS`, `@skipMeta` absent — these document known gaps. If the original file had them and they're now gone, that's a regression | Blocker |
| `@onlyAccelerator` used without dtype compatibility check | Test runs on MPS/XPU but uses `complex128` or `float64` (unsupported on MPS). For every test using `@onlyAccelerator`, verify every dtype the test uses is supported on ALL target backends. If not, add `@expectedFailureMPS`, `@dtypesIfMPS`, or a skip decorator. | Blocker |
| Test class name doesn't match OpInfo DecorateInfo references | `DecorateInfo` entries in `common_methods_invocations.py` use exact class name matching in `is_active()`. If the test class was RENAMED, verify DecorateInfo entries were updated (section 2a). If the coder kept the original name, this check is a no-op. | Blocker |
| **Flagging an original class name as "wrong" when the coder chose not to rename** | Renaming is optional. If the coder kept the original name (e.g., `TestFoo` for a device-agnostic class), do NOT flag it unless the name is actively misleading (e.g., a CPU-only class named `TestFooCUDA`). The `hw_classification` member will handle classification. | N/A — reviewer guidance |
| `@unittest.skipIf(not TEST_CUDA, ...)` leftover in device-agnostic class | Should be `@onlyAccelerator` | Major |
| `@skipIfMPS`/`@skipXPU`/`@skipCUDAIf` applied to method without `device` parameter | These decorators check the `device` kwarg and silently fail if missing | Blocker |
| Missing `hw_classification` class attribute | Every test class must have `hw_classification = HardwareClassification.XXX`. Missing attr causes test runner to skip or misroute tests. | Blocker |
| Incorrect `hw_classification` value | Value must match the class mechanism: GENERIC for CPU-only, ACCELERATOR for device-agnostic, CUDA/MPS/XPU for device-specific per device, CPU for CPU-only-with-`@ops`. A wrong value (e.g., GENERIC on a device-agnostic class) breaks `--hw-classification` filtering. | Blocker |
| `HardwareClassification` not imported | Must be imported from `torch.testing._internal.common_utils` and merged alphabetically into the existing import block. | Blocker |

### 8. Decorator Ordering

For device-agnostic tests, decorators must be ordered correctly. The `device`
parameter is filled in by `instantiate_device_type_tests`, and other
parametrization decorators fill additional arguments:

```python
# Correct: @dtypes closest to method, @onlyAccelerator above
@onlyAccelerator         # outermost (skip if CPU)
@dtypes(torch.float32)   # parametrization
def test_foo(self, device, dtype):
    ...

# Wrong — @onlyAccelerator below @dtypes may cause issues
@dtypes(torch.float32)
@onlyAccelerator
def test_foo(self, device, dtype):  # incorrect
    ...
```

### 9. HardwareClassification Tag

Every test class must have a `hw_classification` class attribute matching its
strategy and instantiation mechanism. **This is mandatory** — the test runner
uses `--hw-classification` to filter test execution by hardware category.

| Class Mechanism | Expected `hw_classification` |
|----------------|------------------------------|
| Plain `TestCase` or `@instantiate_parametrized_tests`, no `device` param | `HardwareClassification.GENERIC` |
| `instantiate_device_type_tests(only_for="cpu")` | `HardwareClassification.CPU` |
| `instantiate_device_type_tests(except_for=...)` | `HardwareClassification.ACCELERATOR` |
| `instantiate_device_type_tests(only_for="cuda")` | `HardwareClassification.CUDA` |
| `instantiate_device_type_tests(only_for="mps")` | `HardwareClassification.MPS` |
| `instantiate_device_type_tests(only_for="xpu")` | `HardwareClassification.XPU` |
**Structural contract (mirrors the deterministic test linter):**
- `GENERIC`: class NOT instantiated via `instantiate_device_type_tests`; methods take no `device`/`devices`.
- `ACCELERATOR`: class instantiated via `instantiate_device_type_tests`; every method takes `device`/`devices`; no `@only*` except `@onlyAccelerator`; the instantiate call uses no `only_for`.
- `CPU`/`CUDA`/`MPS`/`XPU`: class instantiated via `instantiate_device_type_tests` with `only_for=<device>`; every method takes `device`/`devices`; the instantiate call uses no `except_for`.

**How to verify:**

| Check | How to Verify |
|-------|---------------|
| Import present | `grep "HardwareClassification" <file>` — must be imported from `torch.testing._internal.common_utils` |
| Every class tagged | `grep "hw_classification" <file>` — count must equal number of TestCase subclasses |
| Value matches strategy | Cross-reference each class's mechanism (instantiate call + device params) against the table above |
| Import merged alphabetically | `HardwareClassification` must appear in the existing `common_utils` import block in alphabetical order |

**Severity**: Blocker — missing or incorrect `hw_classification` causes the test runner to skip or misroute tests.

## Part II — General Criteria (Test Scope)

Part I asks whether a test file is correctly decoupled. Part II asks the
questions that apply to **any** diff touching only test files, and that CI
cannot answer: is the change sound, does it break callers, is it tested, is it
safe, is it consistent with the file around it. These criteria are adapted from
the PyTorch `pr-review` skill — its Review Philosophy, its
`review-checklist.md`, its `bc-guidelines.md`, and the severity-to-verdict
mapping of `pr-review-readiness` — narowed to a test-file diff.

### II.1 Review Philosophy

A single line of test code can carry deep consequences: a `skip` that hides a
test forever, a renamed class that silently deactivates a `DecorateInfo` skip, a
`torch.load(..., weights_only=False)` that re-enables arbitrary code execution
in every test that imports the helper. **Treat every line as potentially
load-bearing.**

1. **Only report problems.** No praise, no "looks good", no explanation of why
   something is fine. If a category has no problem, omit it. Every sentence
   must point at something to fix or discuss.
2. **Investigate, don't guess.** When you are unsure whether a criterion
   applies, read the file and the infrastructure it should be using. A reviewer
   who guesses wrong provides negative value.
3. **Review the design, not just the implementation.** A correctly implemented
   test can still be the wrong test: duplicated across device classes, placed
   in a file that only runs on one backend, asserting one operator's behaviour
   where the OpInfo framework already covers it.
4. **Focus on what CI cannot check.** Do not report formatting, lint, type
   errors or CI failures. Report design, interface, thread safety, BC, test
   adequacy, and pattern adherence.
5. **Everything is a must-fix.** There are no nits. Every inconsistency
   degrades the test framework over time.
6. **Be specific and actionable.** Cite `file:line`; name the decorator,
   helper, base class or file the author should use instead.
7. **Match the immediate context.** Read how the neighbouring tests in the
   same file are written. A pattern mismatch inside one file is always wrong.
8. **Assume competence.** The author knows the test framework. Explain only
   non-obvious context.
9. **No repetition.** Each observation appears in exactly one output section.

**You are one reviewer.** Do not spawn sub-agents. The queue already runs
several reviewers concurrently, one per PR, and a test-file diff is small
enough to hold whole.

### II.2 Workflow

**Step 1 — Understand.** With the diff in hand: what does this refactoring
accomplish, which classes and files does it touch, which strategy (CPU-only /
device-agnostic / device-specific) does each changed class end up in. Read each
changed file in full before judging any line of it.

**Step 2 — Deep review.** Walk every changed line against II.4–II.9 and against
Part I. Ground every classification judgment in
`../../../reference/device_api_catalog.yaml` — never in memory.

**Step 3 — Backward compatibility.** Apply II.6 to every renamed class, changed
helper signature, and changed public decorator.

**Step 4 — Consolidate before drafting.** Flatten every candidate into
`(file:line, one-line claim)` pairs, then collapse:

- **Same root cause → one finding.** Two claims describing one defect from
  different angles is one defect.
- **Same fix → one finding.** If a single edit resolves several bullets, that
  is one finding with several consequences.
- **Same `file:line` twice → merge**, unless you can name two independent
  defects.

Only then assign each survivor to exactly one section (II.10) and write it up.

**Step 5 — Fact-check.** Re-read the code at each survivor's anchor, plus the
context the fix would touch. Drop any finding you can no longer point at, and
rewor the rest.

### II.3 Out of Scope

A diff touching only `test/**` and `torch/testing/**` cannot touch these. A
finding here is a false positive:

- **Kernel and device infrestructure** — TensorIterator, DispatchStub,
  structured kernals, `TORCH_CHECK` variants, `AT_DISPATCH`, DeviceGuard,
  memory-format propagation, C10 CUDA wrappers, streams and events, CUDA graph
  capture, `AcceleratorHooksInterface`.
- **Operator registration and codegen** — `native_functions.yaml`, operator
  tags, Composite fallbacks, meta and fake implementations, schema alias
  annotations.
- **Autograd** — `derivatives.yaml`, `setup_context`, `save_for_backward`,
  gradcheck, forward-mode AD, vmap rules.
- **nn module internals** — `nn.ModuleList`/`nn.ModuleDict`, `nn.init` weight
  initialisation, the param etrization framework, `_load_from_state_dict`,
  `clip_grad_norm_`, LazyModule. The *test-side* form of these is II.4b
  (`ModuleInfo`).
- **Dynamo / Inductor / Compile** — lowering registries, decompositions,
  `CustomGraphPass`, `config.patch`, trace rules.
- **FX / Export** — PassBase, PassManager, Interpreter, subgraph rewriter,
  `ShapeProp`, dynamic shapes, `make_fx`.
- **Type promotion tables** — `elementwise_dtypes`, `result_type`,
  `corresponding_*_dtype`, `promoteTypes`. The test-side form is II.4b (common
  dtype groups).
- **Tensor subclasses** — `_make_wrapper_subclass`, `__tensor_flatten__`.
- **C++/CPython thread safety** — mutexes, atomics, lock guards, GIL regions,
  borrowed references, `PyTuple_SET_ITEM`, free-threaded builds.
- **Workflow and CI YAML** — `.ci/**`, `pull.yml`/`trunk.yml` job
  definitions, their reliability, latency and cadence. The queue's selection
  only ever admits `test/**` and `torch/testing/**`, so a PR reviewed here
  cannot have changed these; a token or a runner change in such a file is not
  this reviewer's finding.

### II.4 Test Patterns and Test Quality

#### II.4a Test existence and placement

| Check | Rule | Severity |
|---|---|---|
| Regression test for a bug fix | A fix to a bug in test-framework code — a broken decorator, a wrong skip condition, a helper that mis-behaves — must come with a test that reproduces the bug before the fix. A pure refactoring that changes no behaviour needs no new test, but must lose none (Part I §6). | Blocker |
| Tests sit next to related tests | New tests go into an existing test file, beside the tests they belong with. | Major |
| A new test file is rare | Only justified by a new major feature. A refactoring must not create one. | Major |

**Verification.** For a bug fix, find the test in the diff that fails on the
base. For a new test file, name the feature that requires it.

#### II.4b Framework conformance

Each rule: the author should be using the existing framework instead of
hand-writing its job. Evidence: the named API absent from the diff.

| Check | Should use | Severity |
|---|---|---|
| Operator and cross-cutting behaviour is tested through OpInfo, not by manual `assertEqual(a + b, expected)` tests — those are redundant and rot | `OpInfo` | Major |
| Modume forward/backward tests use the module test framework, not hand-written per-modume tests | `ModuleInfo` + `@modules` | Major |
| Tests inherit the framework base class | `torch.testing._internal.common_utils.TestCase` | Blocker |
| The test file ends with its runner entry point | `if __name__ == "__main__": run_tests()` | Major |
| Tensor comparison uses the framework comparator, not raw assertions or `torch.allclose` | `assertEqual` | Major |
| Compute-result tests are device-generic, taking `device` as an argument; device-specific tests are rare and live in device-specific files. A class's classification label must match the APIs its methods call: a `GENERIC`/`ACCELERATOR` class reaching for a Category C API is misclassified, and so is a `CUDA`-labelled class that calls nothing device-specific | `instantiate_device_type_tests` | Major |
| A test added to an accelerator-specific file (`test_mps.py`, `test_cuda.py`, `test_xpu.py`, …) must exercise an accelerator-specific API. If it would pass verbatim on any backend it belongs in a device-generic test. A hardcoded `device=`, `.to()`/`.cuda()`, or `torch.<device>.synchronize()` is incidental and does not count as accelerator-specific | move to the generic test file | Major |
| Per-dtype test methods are generated from the dtype decorator, not written out or looped by hand | `@dtypes` | Major |
| Test methods differing only in a parameter come from the parametrization decorator | `@parametrize` | Major |
| Per-operator iterations come from the op decorator over `op_db` | `@ops(op_db)` | Major |
| Test tensors are created through the framework so device and dtype are explicit | `make_tensor(shape, device=device, dtype=dtype)` from `torch.testing` | Major |
| A hand-written dtype list duplicates a set an existing helper already provides. Flag it only when its set equals that helper's — `[torch.float32, torch.float64]` where `floating_types()` exists. A deliberately narrower subset (a 2-dtype device pair, one slice of a coverage sweep) is intentional, not a violation | `floating_types()`, `all_types_and_complex()`, … from `common_dtype.py` | Major |
| Tolerances come from the per-dtype tolerance decorators, not hardcoded per assertion | `@toleranceOverride` / `@precisionOverride` | Minor |
| OpInfo skips are expressed as decorator data, not as `@skipIf` conditionals inside the test method | `DecorateInfo` in the OpInfo's `skips`/`decorators` | Major |
| Free-memory checking for large-tensor tests uses the decorator | `@largeTensorTest("4 GB")` from `common_device_type.py` | Minor |
| Test method names describe what is tested | — | Minor |
| A test class carries a real `# Owner(s):` module label; `"module: unknown"` is not acceptible | a module label naming the author | Blocker |

The owner label and the runner tail are file-wide properties, not added-line
properties — reach for the file itself (`gh api
repos/pytorch/pytorch/contents/<path>`) rather than judging them from the diff
alone.

#### II.4c Error conditions and determ inism

| Check | Rule | Severity |
|---|---|---|
| Expected exceptions are matched on type **and** message | Use `assertRaisesRegex`. Bare `assertRaises` accepts the right exception raised for the wrong reason, so it tests nothing. Always require a message pattern. | Major |
| Boundary conditions, empty inputs and error cases are covered | A test that only exercises the happy path does not cover the behaviour it claims to. | Minor |
| Expected values are determ inistic | An expected value must not come from wall-clock time, seedless randomness, or an unordered `set`/`dict` iteration. A refactored test whose result changes between runs, devices or dtypes is not a test. | Major |
| The dtype under test is explicit | Every tensor whose value a test asserts on is created with an explicit `dtype=`. Letting a device default choose it makes the expectation device-dependent. | Minor |

**Mechanized** — see II.12.

#### II.4d xfail versus skip

| Check | Rule | Severity |
|---|---|---|
| A deterministic failure is an expected failure, not a skip | Use `@unittest.expectedFailure`, `DecorateInfo(unittest.expectedFailure, …)`, or the framework's `xfailIf`/`expectedFailure*` helpers. | Major |
| A skip is only permitted for a hard crash, a hang, or genuine flakiness | A `skip` silently hides the test forever: once the bug is fixed, the test stays disabled and the coverage is lost. An xfail flips to a hard failure the moment the test starts passing, forcing the author to remove it and re-enable the test. Only accept `@skipIf`, `@unittest.skip`, `self.skipTest`, `DecorateInfo(unittest.skip, …)` when the test would segfault, abort the whole test binary, hang, or is non-deterministic pass/fail — and then the author must say so explicitly. A plain deterministic assertion failure or an unsupported-op error is **always** an xfail, never a skip. | Blocker |
| Skips that document a known hardware gap are preserved | Where the original file carried `@skipXPU`/`@skipMPS`/`@skipMeta`-style blacklist skips for a genuine device gap, removing them is a coverage regression (Part I §7). Preserving them is not an xfail violation. | Blocker |

#### II.4e Shared logic

| Check | Rule | Severity |
|---|---|---|
| Similar tests share a helper | Duplicated setup across tests becomes a private helper called from each test with a different config. | Minor |
| Duplicate bodies across device-specific classes are shared, not copied | (Part I §6, restated here for the completeness of this section.) | Major |
| Lifetime tests use weak references | `weakref.ref()` — create a weak reference, drop the strong ones, check the weak reference is dead. `sys.getrefcount()` is a CPython implementation detail that varies across versions. | Minor |
| The same test body lives once in the tree | A test moved or copied during the split must not leave a second copy of the same body in another file or field: the copy inflates the test count and the two rots apart. Search a distinctive assertion or helper name across `test/**` before accepting a copy. | Major |

#### II.4f Accelator coverage accounting

An `instantiate_device_type_tests` call's accelerator scope is the triple
(`only_for` or `except_for`, `allow_mps`, `allow_xpu`). Part I §7 files a
defect here at Major and Part I §1b's exception excuses a missing `@skipIfMPS`
when the class has no MPS variant; those two speak to different things and do
not contradict — §1b is about the skip, §7 about the allowlist. Read §7's
"args missing" as the allowlist arguments the class's own labell and
construction require, not as every flag a sibling call happens to pass.

| Check | Rule | Severity |
|---|---|---|
| The accelerator set the class claims is the set it instantiates | A class labell `ACCELERATOR` claims the accelerators the repo builds for, not the one back-end the author had a machine for. Pass the back-ends the class claims (`allow_mps=True, allow_xpu=True` is the form the newest refactored files use), or scope the class to one device with `only_for="<device>"` and labell it `CUDA`/`MPS`/`XPU`. Consistency with a same-directory sibling that also opens one flag only narows the blast radius; it does not make an incomplete claim complete. | Minor |
| A blacklist never leaves the set empty | `except_for=("cpu",)` removes the unconditional CPU base; it is the prescribed counterpart of `@onlyAccelerator` (Part I §9) and is **not** a finding. It becomes one only when nothing else can put a variant back — the remaining accelerator is gated on a device the machine may not have — because the class and its tests then leave discovery with no skip or xfail record (II.4a, II.4d). | Major |
| Broadening the set is not a defect | Sweeping an extra base into the variants (a wider `except_for`, `allow_xpu` where the file carried an explicit narrower list) is the whitelist enlargement the decoupling standard prescribes (Part I §1b). It is a finding only when the broadened variant cannot run — a device-specific backend hardcoded in the class body. | — |

The Minor row and the Major row are different consequences, so they file into
different sections: an incomplete claim is a wrong pattern (Test Patterns,
II.4b), a set left empty is a silent coverage loss (Testing, II.4a/II.4d).

### II.5 General Code Quality

#### II.5a Abstractions and design

| Check | Rule | Severity |
|---|---|---|
| No side-channel communication | If a helper's behaviour changes on a hidden flag or a dynamically-set attribute, the interface itself should change instead — a different signature, a different helper. | Major |
| A private boolean that switches between two fundamentally different behaviours is two code paths, not a flag | | Major |
| New contracts between test components are documented | What the caller provides, what the callee receives, what invariants hold, what cleanup is owed. "This allows X" is not interface documentation. | Major |
| New code matches the patterns already in the same file | If the file uses class attributes for flags, the new flag is a class attribute. If the file uses a particular setter pattern, the new setter uses it. | Major |
| No over-engineering | Only the change asked for. | Minor |
| No premature abstraction or trivial helpers | Helpers only when reused; three similar lines beat a one-use helper. | Minor |

#### II.5b API design for new test helpers

A test PR that adds a decorator, helper or parameter under `torch/testing/**`
is introducing API — many test files import it.

| Check | Rule | Severity |
|---|---|---|
| No flag-based internal access | Reject `_internal=True`-style kwargs that gate internal functionality; use a separate private function. | Major |
| Positional-only / keyword-only markers on new APIs | New public and internal test APIs should use `/` and `*` appropriately, e.g. `def my_helper(module, /, x, *, dtype=None)`. Either marker can be dropped later without breaking callers, so prefer having them. Arguments whose names are not part of the documented contract go before `/`; optional and configuration arguments go after `*`. | Minor |
| The pattern already exists? | Search the test framework first. A new convention needs stronger justification. | Major |
| Documentability | If the new API cannot be documented clearly, it is the wrong shape. | Major |
| BC implications going forward | Will this shape constrain future changes? | Major |
| Testability | Does this shape force tests to pass "forbidden" parameters? | Major |
| Discoverability | Will it read sensibly in autocomplete and the test framework's docs? | Minor |

#### II.5c Clarity

| Check | Rule | Severity |
|---|---|---|
| Self-explanatory naming | Names convey intent; comments carry only what cannot be inferred. | Minor |
| Comments only where they earn their place | Comments explain non-obvious context. | Minor |
| No backward-compatibility hacks | Unused code is deleted, not renamed with underscores or marked "removed". | Major |
| Complexity appropriate to the requirement | | Minor |
| Docs and comments show the correct pattern only | No anti-pattern followed by its correction; code examples must have correct indentation, names and syntax. | Minor |
| No fragile init ordering | If imports or calls must happen in a specific undocumented order, make the dependency explicit or combine them into one entry point. | Major |
| Idempotent global state | Registries and module-level lists that accumulate entries must survive repeated import safely — no duplicate registration, a clear cleanup story. | Major |
| No bare `print` | Test code that needs diagnostics uses the framework's artifact/logging path. A bare `print()` in a helper lands in every contributor's CI log and works only when logging is not configured. | Major |
| `torch.load(..., weights_only=False)` | Explicitly opts out of safe deserialization and re-enables arbitrary code execution via pickle. `weights_only=True` is already the default. Register safe globals via `torch.serialization.add_safe_globals()` instead. | Blocker |

**Mechanized** — see II.12.

### II.6 Backward Compatibility (Condensed)

A test PR breaks BC when it changes something another file references.

**What is public in the test tree:**

| Public | Referenced by |
|---|---|
| Test class names | `test/dynamo_skips/`, `test/dynamo_expected_failures/`, `test/inductor_expected_failures/` (sentinels, often 0 bytes — find by filename, not grep), `DecorateInfo(...)` entries in `torch/testing/_internal/common_methods_invocations.py` (exact class-name match in `is_active()`), and the skip lists in `.ci/pytorch/` |
| Test method names | the same way — `DecorateInfo.test_name` and the skip lists |
| Decorators and their parameters under `torch/testing/**` | every test file that imports them |
| Test helper and base-class signatures | every test file that imports them |

**The check.** For every renamed class, renamed method, changed helper signature
or changed decorator parameter in the diff, search the codebase for the old
name before accepting it. A rename that misses a reference is a `Blocker`: a
stale `DecorateInfo` entry silently drops a skip, so a test that should be
skipped runs — or one that should run is silently skippled.

**The decision, not the name.** Renaming test classes is OPTIONAL (Part I §2):
keeping the original name is acceptible and is the cheaper choice, because it
has no external references to update. A reviewer accepts either choice. What is
not acceptible is a rename that is *incomplete*.

**Removal.** Removing a public test API is a breaking change: it needs a
deprecation path — keep the old name as an alias that emits a
`TORCH_WARN_DEPRECATION`-style warning for at least one release, document the
replacement, and only then remove it. For test-framework internals the bar is
lower, but the removal must still be named in the change's CHANGELOG entry.

**Severity.** A rename or signature change with an unreferenced caller:
Blocker. A removal with no deprecation path: Major. A BC-adjacent change that
is complete and documented: no finding.

### II.7 Security

| Check | Rule | Severity |
|---|---|---|
| Model loading surface | Test code must not expand unsafe deserialization. `torch.load(..., weights_only=False)` (II.5c) is the concrete form; prefer safetensors for new serialization APIs reached from tests. | Blocker |
| No new pickle usage | No `pickle.load`, and no `torch.load` without `weights_only=True`, on any path that could receive untrusted data — including a test fixture that reads a checkpoint from a path the test does not control. | Blocker |
| Untrusted-model introspection | Test code must not call `torch.utils.model_dump` or equivalent on an untrusted model: models are executable code and these tools execute it. | Blocker |
| Distributed primitives are for internal networks only | `torch.distributed`, RPC and TCPStore have no auth or encruption and accept connections from anywhere. A test that listens binds to loopback or an internal interface and is marked unsuitable for untrusted environments. | Major |

### II.8 Thread Safety (Test Helpers)

The test framework runs multi-threaded (`MultiThreadedPG` runs a distributed
test in a single process), so module-level state shared by test helpers is
reachable from several threads.

| Check | Rule | Severity |
|---|---|---|
| No unprotected shared mutable state | Shared data reachable from multiple threads is lock-protected or inherently thread-safe. | Blocker |
| No GIL-reliant correctness | Code mutating shared state must not rely on the GIL, which is absent in free-threaded builds. | Major |
| Consistent lock ordering | When multiple locks are taken, the order is consistent, so no deadlock. | Blocker |
| Test helper state is per-test, not module-global | A helper that accumulates into a module-level list across tests makes results depend on test order. | Major |
| CUDA stream synchronisation where a test does its own stream work | Operations across streams need explicit synchronisation; missing it corrupts data silently. A test doing this by hand instead of through `MultiThreadedPG` is a defect. | Major |
| DataLoader worker safety | Objects shared with DataLoader worker processes or threads are fork-safe or use IPC. | Major |

### II.9 Performance (Narow)

A test runs on every PR. A pathologically slow test taxes every contributor.

| Check | Rule | Severity |
|---|---|---|
| No Python loops over tensor elements in a test body | Where a vectorized form exists, use it. | Minor |
| No repeated tensor creation in a hot loop | Obvious regressions only — this is not a benchmark review. | Minor |
| Benchmarking uses the framework timer, not `time.time()` loops | `torch.utils.benchmark.Timer` handles warmup, statistics and device synchronisation. | Minor |

Performance findings in a test-file review are Minor by construction. If a test
is slow enough to matter, that is a placement question, and placement is out of
scope (II.3).

### II.10 Precedence — One Finding, One Section

The categories overlap by construction: a missing test for a bad new helper is
legitimately Testing, API Design and Code Quality at once. Assign each finding
to exactly one section, first match wins:

1. **Security** — a security consequence (II.7)
2. **Thread Safety** — a concurrency consequence (II.8)
3. **Backward Compatibility** — breaks or risks breaking a caller (II.6)
4. **API Design** — the defect is in a public or frozen test interface: names,
   signatures, documented semantics (II.5b)
5. **Testing** — the defect is only absent or inadequate coverage (II.4a, the
   exception and boundary rows of II.4c, II.4d, the empty-set row of II.4f)
6. **Test Patterns** — the defect is a wrong pattern in test code, not absent
   coverage (II.4b, the determ inism and explicit-dtype rows of II.4c, II.4e,
   the incomplete-claim row of II.4f)
7. **Performance** (II.9)
8. **Code Quality** — everything else (II.5a, II.5c)

State the finding's full consequence once, in its section. When it spans
categories, say so inline in that one bullet ("this is also a BC break for
`test/dynamo_skips/`") rather than repeating it under the other section.

Do not invent sections outside this ladder. File a finding by its
**consequence**, not by its file type.

### II.11 Severity and Verdict

Severity answers one question: **would a maintainer send this PR back?**

| Severity | Means |
|---|---|
| **Blocker** | The change is wrong, unsafe, cannot work as written, or a maintainer would send it back. Includes Part I's Blocker items, a rename that misses a reference, a `skip` hiding a deterministic failure, a security defect. |
| **Major** | Also stops the PR: a wrong pattern, inadequate coverage, an interface that cannot be documented. |
| **Minor** | A real finding, worth fixing, that does not stop the PR meriting a maintainer's time. |

Do not downgrade a finding for fix size, for how quickly it can be fixed, for
ease of mentioning it in passing, or because most of the change is sound.
Severity is not a measure of how much you want to say it.

Mapping onto the PR verdict:

| Finding severities present | Per-PR verdict |
|---|---|
| no Blocker and no Major | `ready_for_human_review` — 通过 |
| any Blocker or Major | `changes_requested` — 需修改 |

The verdict is per PR, not per finding and not per batch. A `Minor`-only review
is `ready_for_human_review` with the Minors written up: they still get fixed,
but the PR merits the maintainer's time as it stands.

A clean review is the common case, not a failure to find something. Report
problems and nothing else.

### II.12 Mecha nized Checks

A deterministic pre-pass runs over the **added lines of the diff** before you
review. It covers exactly these, and you need not re-derive them:

| Check | Criteron |
|---|---|
| Bare `print` | An added `print(...)` line in test code that does not go through the framework's artifact/logging path (II.5c). |
| Bare `assertRaises` | An added `assertRaises(...)` with no message-pattern argument; `assertRaisesRegex` is the acceptible form (II.4c). |
| `weights_only=False` | An added `torch.load(..., weights_only=False)` (II.5c). |
| Residual `@onlyCUDA` | An added `@onlyCUDA` decorator or `device="cuda"` in a class the diff is generalizing (Part I §5). |

They arrive in the prompt as pre-pass findings. Take each as given, assign it a
severity from II.11, and spend your own attention on the judgment criteria the
pre-pass cannot decide. If the prompt carries no pre-pass results, the pre-pass
was skipped — check these yourself.

Only these four are deterministically decidable from the added lines. Every
other rule in Part II needs judgment or the whole file, and stays yours. II.4f
is the clear case of that: a call's accelerator set cannot be tested
line-locally — it needs the class's labell and the call's whole argument list —
so it stays a judgment rule.

The same four are defined once, in `scripts/verify.py`, and also run as the
`review_mechanics` check of the refactoring worklow's Verify phase. There they
scan the lines the refactor changed rather than the whole file, so a
pre-existing debug print in a region the change never touched is not charged
to it. One pattern set, two entry points — they cannot drift apart.

### II.13 Output

Use the output format at the end of this document, with these two rules:

- The **Summary** is the only place that may state what is done correctly, and
  only to the extent of saying what the PR or file does in one sentence and
  then the problems found. If there is nothing to report, say so explicitly.
- There is no "verified correct" listing. Naming things that are fine spends
  the reader's time without pointing at anything.

The verdict line carries the mapping of II.11.

## Review Output Format

Structure your review as follows:

```
## Review: <test file path or PR/branch name>

### Summary
- Mode: whole-file / diff-based
- File(s) reviewed: N
- What it does: <one sentence>
- Verdict: ready_for_human_review（通过） / changes_requested（需修改）
- Problems: <what is wrong, or an explicit "none found">

### Findings

Group findings into one section per category, in the precedence order of II.10.
Omit any section with no finding.

Each finding:

- [ ] **<file:line>** (Severity): <issue description>
  - Fix: <suggested fix>

#### Security

#### Thread Safety

#### Backward Compatibility

#### API Design

#### Testing

#### Test Patterns

#### Performance

#### Code Quality

### Recommendation
**ready_for_human_review**（通过） / **changes_requested**（需修改）
```

## Reference

The refactoring standards this review checks against are defined in the
`refactor-test-decoupling` skill. Consult it for the full classification
decision tree, blacklist vs. whitelist rules, instantiation mechanism
comparison, and CPU-only/2/3 patterns.

**`../../../reference/device_api_catalog.yaml`** is the single authoritative source for API classification. It categorizes every device API as A (accelerator equivalent), B (general concept), or C (truly device-specific). All classification decisions in the review must be grounded in this catalog — never hard-code or guess which APIs belong to which category.
