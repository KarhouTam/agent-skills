---
name: refactor-test-decoupling
description: Refactor PyTorch test files to decouple tests from specific hardware accelerators using three strategies: accelerator-unrelated (CPU-only standalone classes with instantiate_parametrized_tests), accelerator-agnostic (device-generic classes with instantiate_device_type_tests), and accelerator-specific (single-accelerator standalone classes). Pay special attention to tests tagged @onlyCUDA or using .cuda()/device="cuda" that are NOT actually CUDA-specific — most should be refactored to accelerator-agnostic. Use when asked to decouple, refactor, or reorganize tests to work across multiple accelerators, or when a test file imports TEST_CUDA/TEST_MPS/TEST_XPU but most tests don't require a specific device.
---

# Refactor Test Decoupling

Refactor PyTorch test files so tests focus on core functional logic and are decoupled from specific hardware accelerators.

## Naming Convention

Class renaming is **OPTIONAL**. The future `hw_classification` member on TestCase (not yet landed) will handle strategy classification, so class names are no longer the primary classification mechanism. The agent should decide whether to rename based on external reference impact.

### Recommended Names (when renaming)

| Strategy | Recommended Class Name | Instantiation | Example |
|----------|----------------------|---------------|---------|
| **Accelerator-unrelated** (S1) | `TestFoo` (keep original name) | `@instantiate_parametrized_tests` or plain `TestCase` | `TestBinaryUfuncs` |
| **Accelerator-agnostic** (S2) | `TestFooDevice` | `instantiate_device_type_tests()` | `TestBinaryUfuncsDevice` |
| **Accelerator-specific** (S3) | `TestFooOn<Device>` | Plain `TestCase` with `setUp` guard (NEVER uses `instantiate_device_type_tests`) | `TestBinaryUfuncsOnCUDA` |

### Renaming Decision

**When to rename:**
- The original class name has few or no external references (DecorateInfo entries, dynamo_skips/, dynamo_expected_failures/)
- The rename improves clarity (e.g., `TestFoo` → `TestFooDevice` makes the strategy obvious)

**When to keep the original name:**
- The class has many external references that would need updating
- Renaming would risk silently breaking CI (stale DecorateInfo, dynamo skip files)
- The original name is already clear enough

**How to decide:**
1. Check for DecorateInfo references: `grep "cls_name.*OldName" torch/testing/_internal/common_methods_invocations.py`
2. Check for dynamo skip/expected-failure files: `find test/dynamo_skips/ test/dynamo_expected_failures/ -name "OldName*"`
3. If zero or very few external refs → rename is safe
4. If many external refs → keep the original name (avoids breaking cross-file references)

`instantiate_device_type_tests` **removes** the generic class from scope and replaces it with per-device variants (`TestFooDeviceCPU`, `TestFooDeviceCUDA`, etc.). `instantiate_parametrized_tests` keeps the class discoverable.

**S3 mechanism**: S3 classes use plain `TestCase` with `setUp` guard and hardcoded device strings — they NEVER use `instantiate_device_type_tests`. When a test has both Category C APIs and `@dtypes`/`@dtypesIfCUDA`/`@dtypesIfCPU` decorators (which depend on `instantiate_device_type_tests` for resolution), the test is not suitable for S3 extraction and should remain as-is in its original class.

## Classification

Every test falls into one of three categories. Classification is hierarchical: **S3 > S2 > S1**.

| Category | Definition | Mechanism |
|----------|-----------|-----------|
| **Accelerator-unrelated (S1)** | No device usage; CPU only | `instantiate_parametrized_tests()` or plain `TestCase` |
| **Accelerator-agnostic (S2)** | Uses a device but only generic accelerator APIs | `instantiate_device_type_tests()` |
| **Accelerator-specific (S3)** | Requires a particular accelerator's unique features | Plain `TestCase` with `setUp` guard (NEVER uses `instantiate_device_type_tests`) |

### Device API Categories (consult `../../../reference/device_api_catalog.yaml`)

| Category | Examples | Strategy |
|----------|---------|----------|
| **A** — has `torch.accelerator` equivalent | `empty_cache`, `synchronize`, `CUDAGraph`, `memory_allocated`, `current_device` | S2 |
| **B** — general concept, no wrapper yet | `Stream`, `Event`, `manual_seed`, `get_device_properties` | S2 |
| **C** — truly device-specific, no cross-device equivalent | NCCL, NVTX, cuDNN, GDS, Jiterator, Metal shaders, SYCL handles | S3 |

**Only Category C makes a test S3.** If you can replace `"cuda"` with `"mps"` or `"xpu"` and the test still makes logical sense, it's S2.

### Blacklist vs. Whitelist Decorators

| Decorator Type | Examples | Principle | Action |
|---------------|----------|-----------|--------|
| **Blacklist** (explicit skips) | `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd` | Documents a **known gap** — intentional and informed | **KEEP as-is** |
| **Whitelist** (restrictive) | `@onlyCUDA`, `@onlyOn(["cuda","xpu"])`, `@unittest.skipIf(not TEST_CUDA, ...)` | Artificially **restricts** — usually historical accident | **ENLARGE** to `@onlyAccelerator` |
| **Whitelist** (restrictive) | `@onlyCPU` | Artificially **restricts** — usually historical accident | **REMOVE** — make test device-agnostic (add `device` param, pass `device=device`). MUST evaluate each `@onlyCPU` test individually. Default to S2 unless the test genuinely tests CPU-only dispatch behavior. |

**`@onlyNativeDeviceTypes`** is NOT in the whitelist/enlargement list — leave it untouched. It includes CPU (`native_devices = ('cpu', 'cuda', 'xpu', 'meta', 'mps', 'mtia')`) while `@onlyAccelerator` excludes CPU — they are not interchangeable.

### Decision Tree

```
Does the test reference a device?
├─ NO → S1
├─ YES → What device APIs?
│  ├─ Generic only (torch.device(device), make_tensor(..., device=device)) → S2
│  ├─ Category A or B APIs → S2
│  ├─ Category C APIs → S3
│  └─ Hard to tell → Leave as-is

What decorators?
├─ Blacklist (@skipXPU, @skipCUDAIf, @skipMPS, @skipMeta, @onlyNativeDeviceTypesAnd) → KEEP
├─ Whitelist (@onlyCUDA, @onlyOn, @unittest.skipIf(not TEST_CUDA, ...)) → ENLARGE to @onlyAccelerator

> **Note:** An `if device_type == "<backend>"` conditional in the test body does NOT make a test S3 — only Category C API calls do.
```

### False-CUDA Patterns (→ S2, NOT S3)

These almost always indicate S2:

| Pattern | Why Not CUDA-Specific | Action |
|---------|----------------------|--------|
| `@onlyCUDA` on standard ops (add, softmax, matmul) | The op works on any accelerator | `@onlyAccelerator` + `device` param |
| `.cuda()` / `.to("cuda")` on tensors | Just device placement | `.to(device)` |
| `device="cuda"` in tensor creation | Any device would work | `device` param |
| `@unittest.skipIf(not TEST_CUDA, ...)` | Proxy for "needs accelerator" | `@onlyAccelerator` |
| Test name contains `_cuda` | Naming, not functional | Remove suffix |

**Caveats:**
- Do NOT enlarge `@onlyCUDA` → `@onlyAccelerator` if the test had no prior device restriction — remove the restriction entirely instead.
- Keep `@onlyCUDA` if the test relies on backend-specific behavioral guarantees (NaN handling, determinism, precision, rounding modes).

## Strategy 1: Accelerator-Unrelated (S1)

Zero device dependency. CPU tensors only, no `device` parameter.

**Pattern A — Plain TestCase** (no parametrization):
```python
from torch.testing._internal.common_utils import HardwareClassification

class TestFoo(TestCase):
    hw_classification = HardwareClassification.GENERIC

    def test_basic_addition(self):
        a = torch.randn(3, 3)
        b = torch.randn(3, 3)
        self.assertEqual(a + b, torch.add(a, b))
```

**Pattern B — `@instantiate_parametrized_tests`** (has `@parametrize`/`@ops`/`@dtypes`):
```python
from torch.testing._internal.common_utils import HardwareClassification

@instantiate_parametrized_tests
class TestFoo(TestCase):
    hw_classification = HardwareClassification.GENERIC

    @parametrize("dtype", [torch.float32, torch.float64])
    def test_dtype_behavior(self, dtype):
        t = torch.randn(3, 3, dtype=dtype)
        self.assertEqual(t.softmax(0).sum(0), torch.ones(3, dtype=dtype))
```

**Why not `instantiate_device_type_tests`?** It creates per-device variants (TestFooCPU, TestFooCUDA, etc.) — wasteful when all variants do the same CPU-only work.

**Steps:**
1. Extract test methods into a standalone class. Keep the original name (no device suffix) — S1 classes should never have device suffixes.
2. Remove `device` parameter from signatures; hardcode `"cpu"` or omit device args
3. Remove device decorators and device imports (`TEST_CUDA`, `TEST_MPS`, etc.)
4. Add `@instantiate_parametrized_tests` if the class has parametrized decorators
5. **Tag with `hw_classification`**: Add `hw_classification = HardwareClassification.GENERIC` as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically into the existing `common_utils` import block). If the class uses `instantiate_device_type_tests(only_for="cpu")` for `@ops`, use `HardwareClassification.CPU` instead.

## Strategy 2: Accelerator-Agnostic (S2)

Tests that use a `device` parameter but only need generic accelerator APIs. **This is the highest-impact refactoring** — it unlocks tests for all accelerators at once.

### Canonical Before/After

**Before** (false-CUDA):
```python
from torch.testing._internal.common_cuda import TEST_CUDA

class TestFoo(TestCase):
    @unittest.skipIf(not TEST_CUDA, "no CUDA")
    def test_softmax_cuda(self):
        t = torch.randn(3, 3, device="cuda")
        result = t.softmax(0)
        self.assertEqual(result.sum(0), torch.ones(3, device="cuda"))

    @onlyCUDA
    @skipXPU  # XPU doesn't support this op yet
    def test_matmul_cuda(self, device):
        a = torch.randn(3, 3, device=device)
        b = torch.randn(3, 3, device=device)
        self.assertEqual(a @ b, torch.matmul(a, b))
```

**After** (accelerator-agnostic):
```python
from torch.testing._internal.common_device_type import (
    instantiate_device_type_tests, onlyAccelerator,
)
from torch.testing._internal.common_utils import HardwareClassification

class TestFooDevice(TestCase):
    hw_classification = HardwareClassification.ACCELERATOR

    @onlyAccelerator
    def test_softmax(self, device):
        t = torch.randn(3, 3, device=device)
        result = t.softmax(0)
        self.assertEqual(result.sum(0), torch.ones(3, device=device))

    @onlyAccelerator
    @skipXPU  # Still here — known gap
    def test_matmul(self, device):
        a = torch.randn(3, 3, device=device)
        b = torch.randn(3, 3, device=device)
        self.assertEqual(a @ b, torch.matmul(a, b))

instantiate_device_type_tests(TestFooDevice, globals())
```

### Steps

1. **Scrutinize every CUDA reference.** Ask: "CUDA as device or CUDA as feature?" Most are the former → S2.
2. **Create the S2 class** inheriting from `TestCase`. Decide whether to rename (see "Renaming Decision" above). If renaming, use `TestFooDevice`; otherwise keep the original name.
3. **Add `device` parameter** as first arg after `self` on each test method.
4. **Replace hardcoded device strings**: `"cuda"` → `device` param, `.cuda()` → `.to(device)`.
5. **Enlarge whitelist, keep blacklist**: `@onlyCUDA` → `@onlyAccelerator`, `@unittest.skipIf(not TEST_CUDA, ...)` → `@onlyAccelerator`. Keep `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd` as-is.
6. **Replace device-specific APIs**: `torch.cuda.is_available()` → `torch.accelerator.is_available()`, Category A APIs → `torch.accelerator.*` equivalents (see catalog).
7. **Register**: `instantiate_device_type_tests(<ClassName>, globals())` at module level.
8. **Tag with `hw_classification`**: Add `hw_classification = HardwareClassification.ACCELERATOR` as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically into the existing `common_utils` import block).
9. **Remove stale imports**: `TEST_CUDA`, `TEST_MPS` only if no longer referenced.

### Key Rules

- **`@onlyAccelerator` is a method decorator, NOT a class decorator.** Applied to a class, it replaces the class with a function and `instantiate_device_type_tests` fails.
- **Use device-type-aware skips in S2 classes**: `skipXPUIf(True, msg)` / `skipCUDAIf(condition, msg)` from `common_device_type` (not `common_utils`) — these check `self.device_type` and only skip the specific device variant.
- **Category A APIs** (`empty_cache`, `synchronize`, `CUDAGraph`, `memory_*`) have `torch.accelerator.*` equivalents — they do NOT make a test CUDA-specific.
- **Category B APIs** (`Stream`, `Event`) are general concepts on all backends — they do NOT make a test CUDA-specific.

## Strategy 3: Accelerator-Specific (S3)

Tests requiring a particular accelerator's unique (Category C) features.

**S3 NEVER uses `instantiate_device_type_tests`.** Use plain `TestCase` with `setUp` guard. Hardcode device strings (`"cuda"`, `torch.cuda.*` calls). Naming convention: `TestFooOn<Device>` (e.g., `TestFooOnCUDA`), NOT `TestFooCUDA` — the latter could collide with `instantiate_device_type_tests(TestFoo)` generating `TestFooCUDA`.

```python
from torch.testing._internal.common_utils import HardwareClassification

class TestFooOnCUDA(TestCase):
    hw_classification = HardwareClassification.CUDA

    def setUp(self):
        if not torch.cuda.is_available():
            self.skipTest("CUDA not available")

    def test_cuda_stream(self):
        s = torch.cuda.Stream()
        ...
```

For tests that use `@dtypes`/`@dtypesIfCUDA`/`@dtypesIfCPU`/`@parametrize` decorators alongside Category C APIs, these device-type-aware decorators cannot be resolved outside `instantiate_device_type_tests`. Such tests are not suitable for S3 extraction and should remain as-is in their original class.

**Why NOT `@instantiate_parametrized_tests`?**
`@instantiate_parametrized_tests` cannot resolve `@dtypesIfCUDA`/`@dtypesIfCPU` correctly — these decorators rely on the device-type context that only `instantiate_device_type_tests` provides. Using `@instantiate_parametrized_tests` for S3 classes with device-type-aware decorators results in incorrect dtype selection or runtime errors.

**Steps:**
1. Confirm the test genuinely uses Category C APIs and has no `@dtypes`/`@dtypesIfCUDA`/`@dtypesIfCPU` decorators (tests with those decorators should remain as-is).
2. Extract into the S3 class. Decide whether to rename (see "Renaming Decision" above). If renaming, use `TestFooOn<Device>` (e.g., `TestFooOnCUDA`), NOT `TestFooCUDA` — the latter could collide with `instantiate_device_type_tests(TestFoo)` generating `TestFooCUDA`; otherwise keep the original name.
3. Hardcode device strings — no `device` parameter (S3 uses plain `TestCase`, not `instantiate_device_type_tests`).
4. Add `setUp` guard: skip test if the required device is unavailable.
5. **Tag with `hw_classification`**: Add `hw_classification = HardwareClassification.CUDA` (or `MPS`, `XPU` per device) as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically into the existing `common_utils` import block).

## Combined Workflow

### Step 1: Audit
Classify every test method. Create a table:

| Test Method | Device Usage | Category | Target Strategy |
|-------------|-------------|----------|-----------------|
| `test_basic_add` | None | unrelated | S1 |
| `test_softmax_cuda` | Generic only | agnostic | S2 |
| `test_cuda_stream` | CUDA-specific | specific | S3 |

### Step 2: Split
Create up to three classes following the naming convention and patterns above.

### Step 3: Clean up
- Remove stale `TEST_CUDA`/`TEST_MPS` imports and `copy_tests()` calls
- Remove `device` parameter from S1 tests
- **Keep blacklist skips** (`@skipXPU`, `@skipMPS`, `@skipMeta`, `@skipCUDAIf`, `@onlyNativeDeviceTypesAnd`)

### Step 4: Update external references after class renames

**If you kept the original class names, skip this step** — no external references need updating. This is the primary benefit of not renaming.

When a class IS renamed (e.g., `TestCommon` → `TestCommonDevice`), external references to the old class name will **silently stop matching**. This causes previously-skipped tests to run and fail, or expected failures to become unguarded.

**Three locations to check:**

**(a) DecorateInfo in `common_methods_invocations.py`** — `DecorateInfo` entries use exact `cls_name` comparison:

```bash
python -c "
from torch.testing._internal.common_methods_invocations import op_db
from torch.testing._internal.opinfo.core import DecorateInfo
old = {'TestOldName1', 'TestOldName2'}
for op in op_db:
    for d in op.decorators:
        if isinstance(d, DecorateInfo) and d.cls_name in old:
            print(f'{op.name}: cls_name={d.cls_name}, test_name={d.test_name}')
"
```

**Fix:** Search-and-replace the old class name in `common_methods_invocations.py`. For class splits, verify which new class owns each test method first.

**(b) `test/dynamo_skips/`** — filenames are `ClassName.test_method_name`. When a class is renamed, old filenames no longer match and skipped tests may start running:

```bash
# Find stale entries after renaming TestFoo -> TestFooDevice
ls test/dynamo_skips/TestFoo.* 2>/dev/null
```

**Fix:** Rename files to use the new class name: `mv test/dynamo_skips/TestFoo.test_x test/dynamo_skips/TestFooDevice.test_x`

**(c) `test/dynamo_expected_failures/`** — same filename convention as dynamo_skips:

```bash
# Find stale entries after renaming TestFoo -> TestFooDevice
ls test/dynamo_expected_failures/TestFoo.* 2>/dev/null
```

**Fix:** Same as (b) — rename files to match the new class name.

### Step 5: Verify
1. **Test count**: `grep -c "def test_" test/test_file.py` — must match original
2. **Class structure**: `grep "^class " test/test_file.py` — verify naming and instantiation
3. **DecorateInfo**: Step 4(a) check script produces zero output
4. **dynamo_skips**: Step 4(b) check produces no stale entries
5. **dynamo_expected_failures**: Step 4(c) check produces no stale entries
6. **Syntax**: `python -c "import py_compile; py_compile.compile('test/test_file.py', doraise=True)"`

## Instantiation Mechanism Comparison

| Mechanism | Creates Device Variants? | Generic Class Discoverable? | hw_classification | Use When |
|-----------|--------------------------|----------------------------|-------------------|----------|
| Plain `TestCase` | No | Yes | `GENERIC` | No parametrization needed |
| `instantiate_parametrized_tests()` | No | Yes | `GENERIC` | Tests with `@parametrize`/`@ops`/`@dtypes`, no device dependency |
| `instantiate_device_type_tests()` | Yes (CPU, CUDA, MPS, ...) | No (removed from scope) | `ACCELERATOR` | Tests with a `device` parameter, works on any accelerator |
| Plain `TestCase` with `setUp` guard | No | Yes (no parametrization) | `CUDA` / `MPS` / `XPU` | S3 classes — Category C APIs, no device-type-aware decorators |

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| **Removing blacklist skips** (`@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd`) | Keep as-is — they document known gaps |
| **Treating Cat A/B APIs as CUDA-specific** (`empty_cache`, `synchronize`, `CUDAGraph`, `Stream`, `Event`, `memory_*`) | These are S2 — consult `device_api_catalog.yaml` |
| **`@onlyAccelerator` as class decorator** | Use as **method decorator** only — on a class it replaces the class with a function |
| **Using `skipIfXpu`/`skipIfCUDA` from `common_utils` in S2 classes** | Use `common_device_type` equivalents (`skipXPUIf`, `skipCUDAIf`) — they check `self.device_type` and only skip the target variant |
| **Naming S1 class with device suffix** (e.g., `TestFooCPU`) | Keep original name without suffix (`TestFoo`) — S1 has no device dependency. S1 classes should never have device suffixes. |
| **Renaming a class without checking external reference impact** | Before renaming, check DecorateInfo entries and dynamo_skips/dynamo_expected_failures for references to the old name. If there are many external refs, consider keeping the original name to avoid silent breakage. |
| **Moving cross-device tests (CPU+GPU) to S1** | Tests using both CPU and GPU tensors still need a GPU — keep in S2 |
| **Renaming class without updating DecorateInfo** | Search `common_methods_invocations.py` for old class name and update |
| **Renaming class without updating dynamo_skips/** | Search `test/dynamo_skips/` for filenames starting with old class name and rename to new class name |
| **Renaming class without updating dynamo_expected_failures/** | Search `test/dynamo_expected_failures/` for filenames starting with old class name and rename to new class name |
| **Using `instantiate_device_type_tests` for S1 tests** | Creates wasteful per-device variants doing the same CPU work — use `instantiate_parametrized_tests` |
| **Using `@instantiate_parametrized_tests` for S3 with `@dtypesIfCUDA`** | `@dtypesIfCUDA` needs device-type context from `instantiate_device_type_tests`. Tests with both Category C APIs and `@dtypesIfCUDA`/`@dtypes`/`@parametrize` are not suitable for S3 extraction — keep in original class |
| **Mixing `device` param and hardcoded `"cuda"` in same class** | Pick one strategy per class |
| **Including device suffix in S3 class name when using `instantiate_device_type_tests(..., only_for=...)` produces doubled names** | S3 should use plain `TestCase` with `setUp` guard instead |
| **Derive device type from existing data — never add new parameters** | The `device` parameter from `instantiate_device_type_tests` or `tensor.device.type` from any tensor in scope already provides the device type. Adding explicit `device_type`/`device` parameters to functions that already receive tensors or have access to the test's `device` kwarg is redundant and breaks conventions (especially `autograd.Function.forward()`). |
| **Mixed-device tests** | When a test deliberately creates tensors on different devices (CPU + accelerator) for cross-device error handling: keep CPU tensors as explicit CPU, use `device` param for accelerator tensors, scope with `@onlyAccelerator`. Do NOT move to S1 or blindly convert all tensors to `device`. |
| **Missing `hw_classification` attribute** | Every refactored test class must have `hw_classification = HardwareClassification.XXX` as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically). S1→GENERIC (or CPU for `only_for="cpu"`), S2→ACCELERATOR, S3→CUDA/MPS/XPU per device. |

## Related Skills

- `agent/skills/classify-test-files` — Scan and classify test files before refactoring
