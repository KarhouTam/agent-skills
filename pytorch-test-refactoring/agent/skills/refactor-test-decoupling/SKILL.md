---
name: refactor-test-decoupling
description: Refactor PyTorch test files to decouple tests from specific hardware accelerators using three strategies: CPU-only (standalone classes with instantiate_parametrized_tests), device-agnostic (device-parametrized classes with instantiate_device_type_tests), and device-specific (single-accelerator standalone classes). Pay special attention to tests tagged @onlyCUDA or using .cuda()/device="cuda" that are NOT actually CUDA-specific — most should be refactored to device-agnostic. Use when asked to decouple, refactor, or reorganize tests to work across multiple accelerators, or when a test file imports TEST_CUDA/TEST_MPS/TEST_XPU but most tests don't require a specific device.
---

# Refactor Test Decoupling

Refactor PyTorch test files so tests focus on core functional logic and are decoupled from specific hardware accelerators.

## Naming Convention

Class renaming is **OPTIONAL**. The future `hw_classification` member on TestCase (not yet landed) will handle strategy classification, so class names are no longer the primary classification mechanism. The agent should decide whether to rename based on external reference impact.

### Recommended Names (when renaming)

| Strategy | Recommended Class Name | Instantiation | Example |
|----------|----------------------|---------------|---------|
| **CPU-only** | `TestFoo` (keep original name) | `@instantiate_parametrized_tests` or plain `TestCase` | `TestBinaryUfuncs` |
| **device-agnostic** | `TestFooDevice` | `instantiate_device_type_tests()` | `TestBinaryUfuncsDevice` |
| **device-specific** | `TestFoo` (original name — `instantiate_device_type_tests` appends the device) | `instantiate_device_type_tests(only_for="<device>")` | `TestBinaryUfuncsCUDA` |

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

**Device-specific mechanism**: Device-specific classes use `instantiate_device_type_tests(<Class>, globals(), only_for="<device>")` with a `device` parameter on each test method — they do NOT use a plain `TestCase` with a `setUp` guard. Because device-specific classes use `instantiate_device_type_tests`, `@dtypes`/`@dtypesIfCUDA`/`@dtypesIfCPU` decorators resolve correctly.

## Classification

Every test falls into one of three categories. Classification is hierarchical: **device-specific > device-agnostic > CPU-only**.

| Category | Definition | Mechanism |
|----------|-----------|-----------|
| **CPU-only** | No device usage; CPU only | `instantiate_parametrized_tests()` or plain `TestCase` |
| **device-agnostic** | Uses a device but only generic accelerator APIs | `instantiate_device_type_tests()` |
| **device-specific** | Requires a particular accelerator's unique features | `instantiate_device_type_tests(only_for="<device>")` |

### Device API Categories (consult `../../../reference/device_api_catalog.yaml`)

| Category | Examples | Strategy |
|----------|---------|----------|
| **A** — has `torch.accelerator` equivalent | `empty_cache`, `synchronize`, `CUDAGraph`, `memory_allocated`, `current_device` | device-agnostic |
| **B** — general concept, no wrapper yet | `Stream`, `Event`, `manual_seed`, `get_device_properties` | device-agnostic |
| **C** — truly device-specific, no cross-device equivalent | NCCL, NVTX, cuDNN, GDS, Jiterator, Metal shaders, SYCL handles | device-specific |

**Only Category C makes a test device-specific.** If you can replace `"cuda"` with `"mps"` or `"xpu"` and the test still makes logical sense, it's device-agnostic.

### Blacklist vs. Whitelist Decorators

| Decorator Type | Examples | Principle | Action |
|---------------|----------|-----------|--------|
| **Blacklist** (explicit skips) | `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta` | Documents a **known gap** — intentional and informed | **KEEP as-is** |
| **Whitelist** (restrictive) | `@onlyCUDA`, `@onlyOn(["cuda","xpu"])`, `@unittest.skipIf(not TEST_CUDA, ...)` | Artificially **restricts** — usually historical accident | **ENLARGE** to `@onlyAccelerator` |
| **Whitelist** (restrictive) | `@onlyCPU` | Artificially **restricts** — usually historical accident | **REMOVE** — make test device-agnostic (add `device` param, pass `device=device`). MUST evaluate each `@onlyCPU` test individually. Default to device-agnostic unless the test genuinely tests CPU-only dispatch behavior. |

**`@onlyNativeDeviceTypes` / `@onlyNativeDeviceTypesAnd`** are redundant on device-agnostic classes — device instantiation already scopes to the right devices. REMOVE them (the test linter flags them on ACCELERATOR classes).

### Decision Tree

```
Does the test reference a device?
├─ NO → CPU-only
├─ YES → What device APIs?
│  ├─ Generic only (torch.device(device), make_tensor(..., device=device)) → device-agnostic
│  ├─ Category A or B APIs → device-agnostic
│  ├─ Category C APIs → device-specific
│  └─ Hard to tell → Leave as-is

What decorators?
├─ Blacklist (@skipXPU, @skipCUDAIf, @skipMPS, @skipMeta) → KEEP
├─ Whitelist (@onlyCUDA, @onlyOn, @unittest.skipIf(not TEST_CUDA, ...)) → ENLARGE to @onlyAccelerator

> **Note:** An `if device_type == "<backend>"` conditional in the test body does NOT make a test device-specific — only Category C API calls do.
```

### False-CUDA Patterns (→ device-agnostic, NOT device-specific)

These almost always indicate device-agnostic:

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

## Strategy: CPU-only

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
1. Extract test methods into a standalone class. Keep the original name (no device suffix) — CPU-only classes should never have device suffixes.
2. Remove `device` parameter from signatures; hardcode `"cpu"` or omit device args
3. Remove device decorators and device imports (`TEST_CUDA`, `TEST_MPS`, etc.)
4. Add `@instantiate_parametrized_tests` if the class has parametrized decorators
5. **Tag with `hw_classification`**: Add `hw_classification = HardwareClassification.GENERIC` as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically into the existing `common_utils` import block). If the class uses `instantiate_device_type_tests(only_for="cpu")` for `@ops`, use `HardwareClassification.CPU` instead.

## Strategy: Device-Agnostic

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

**After** (device-agnostic):
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

1. **Scrutinize every CUDA reference.** Ask: "CUDA as device or CUDA as feature?" Most are the former → device-agnostic.
2. **Create the device-agnostic class** inheriting from `TestCase`. Decide whether to rename (see "Renaming Decision" above). If renaming, use `TestFooDevice`; otherwise keep the original name.
3. **Add `device` parameter** as first arg after `self` on each test method.
4. **Replace hardcoded device strings**: `"cuda"` → `device` param, `.cuda()` → `.to(device)`.
5. **Enlarge whitelist, keep blacklist**: `@onlyCUDA` → `@onlyAccelerator`, `@unittest.skipIf(not TEST_CUDA, ...)` → `@onlyAccelerator`. Keep `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta` as-is; remove `@onlyNativeDeviceTypes`/`@onlyNativeDeviceTypesAnd` (redundant).
6. **Replace device-specific APIs**: `torch.cuda.is_available()` → `torch.accelerator.is_available()`, Category A APIs → `torch.accelerator.*` equivalents (see catalog).
7. **Register**: `instantiate_device_type_tests(<ClassName>, globals())` at module level.
8. **Tag with `hw_classification`**: Add `hw_classification = HardwareClassification.ACCELERATOR` as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically into the existing `common_utils` import block).
9. **Remove stale imports**: `TEST_CUDA`, `TEST_MPS` only if no longer referenced.

### Key Rules

- **`@onlyAccelerator` is a method decorator, NOT a class decorator.** Applied to a class, it replaces the class with a function and `instantiate_device_type_tests` fails.
- **Use device-type-aware skips in device-agnostic classes**: `skipXPUIf(True, msg)` / `skipCUDAIf(condition, msg)` from `common_device_type` (not `common_utils`) — these check `self.device_type` and only skip the specific device variant.
- **Category A APIs** (`empty_cache`, `synchronize`, `CUDAGraph`, `memory_*`) have `torch.accelerator.*` equivalents — they do NOT make a test CUDA-specific.
- **Category B APIs** (`Stream`, `Event`) are general concepts on all backends — they do NOT make a test CUDA-specific.

## Strategy: Device-Specific

Tests requiring a particular accelerator's unique (Category C) features.

**Device-specific classes use `instantiate_device_type_tests(only_for="<device>")`.** Every test method takes a `device` parameter. Do NOT use a plain `TestCase` with a `setUp` guard — the test linter rejects it. Naming: keep the original class name — `instantiate_device_type_tests` appends the device name to generate the variant (e.g. `TestFoo` → `TestFooCUDA`). Do NOT pre-suffix the name with the device (`TestFooOnCUDA` + `only_for="cuda"` → `TestFooOnCUDACUDA`).

```python
from torch.testing._internal.common_device_type import instantiate_device_type_tests
from torch.testing._internal.common_utils import HardwareClassification

class TestFoo(TestCase):
    hw_classification = HardwareClassification.CUDA

    def test_cuda_stream(self, device):
        s = torch.cuda.Stream()
        ...

instantiate_device_type_tests(TestFoo, globals(), only_for="cuda")
```

Because device-specific classes use `instantiate_device_type_tests`, `@dtypes`/`@dtypesIfCUDA`/`@dtypesIfCPU`/`@parametrize` decorators resolve correctly (they receive the device-type context from instantiation).

**Steps:**
1. Confirm the test genuinely uses Category C APIs.
2. Extract into the device-specific class. Decide whether to rename (see "Renaming Decision" above). Prefer the original name — `hw_classification` is the discriminator.
3. Add a `device` parameter as first arg after `self` on each test method. Keep Category C API calls (`torch.cuda.*`, etc.) as-is.
4. Register: `instantiate_device_type_tests(<ClassName>, globals(), only_for="<device>")` at module level.
5. **Tag with `hw_classification`**: Add `hw_classification = HardwareClassification.CUDA` (or `MPS`, `XPU` per device) as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically into the existing `common_utils` import block).

## Combined Workflow

### Step 1: Audit
Classify every test method. Create a table:

| Test Method | Device Usage | Category | Target Strategy |
|-------------|-------------|----------|-----------------|
| `test_basic_add` | None | unrelated | CPU-only |
| `test_softmax_cuda` | Generic only | agnostic | device-agnostic |
| `test_cuda_stream` | CUDA-specific | specific | device-specific |

### Step 2: Split
Create up to three classes following the naming convention and patterns above.

### Step 3: Clean up
- Remove stale `TEST_CUDA`/`TEST_MPS` imports and `copy_tests()` calls
- Remove `device` parameter from CPU-only tests
- **Keep blacklist skips** (`@skipXPU`, `@skipMPS`, `@skipMeta`, `@skipCUDAIf`)

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
| `instantiate_device_type_tests(only_for="<device>")` | Yes (single device) | No (removed from scope) | `CUDA` / `MPS` / `XPU` | device-specific classes — Category C APIs |

## Common Pitfalls

| Pitfall | Fix |
|---------|-----|
| **Removing blacklist skips** (`@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`) | Keep as-is — they document known gaps |
| **Treating Cat A/B APIs as CUDA-specific** (`empty_cache`, `synchronize`, `CUDAGraph`, `Stream`, `Event`, `memory_*`) | These are device-agnostic — consult `device_api_catalog.yaml` |
| **`@onlyAccelerator` as class decorator** | Use as **method decorator** only — on a class it replaces the class with a function |
| **Using `skipIfXpu`/`skipIfCUDA` from `common_utils` in device-agnostic classes** | Use `common_device_type` equivalents (`skipXPUIf`, `skipCUDAIf`) — they check `self.device_type` and only skip the target variant |
| **Naming CPU-only class with device suffix** (e.g., `TestFooCPU`) | Keep original name without suffix (`TestFoo`) — CPU-only has no device dependency. CPU-only classes should never have device suffixes. |
| **Renaming a class without checking external reference impact** | Before renaming, check DecorateInfo entries and dynamo_skips/dynamo_expected_failures for references to the old name. If there are many external refs, consider keeping the original name to avoid silent breakage. |
| **Moving cross-device tests (CPU+GPU) to CPU-only** | Tests using both CPU and GPU tensors still need a GPU — keep in device-agnostic |
| **Renaming class without updating DecorateInfo** | Search `common_methods_invocations.py` for old class name and update |
| **Renaming class without updating dynamo_skips/** | Search `test/dynamo_skips/` for filenames starting with old class name and rename to new class name |
| **Renaming class without updating dynamo_expected_failures/** | Search `test/dynamo_expected_failures/` for filenames starting with old class name and rename to new class name |
| **Using `instantiate_device_type_tests` for CPU-only tests** | Creates wasteful per-device variants doing the same CPU work — use `instantiate_parametrized_tests` |
| **Using `@instantiate_parametrized_tests` for device-specific classes** | Device-specific classes use `instantiate_device_type_tests` (which provides the device-type context that `@dtypesIfCUDA`/`@dtypes`/`@parametrize` need). Do not use `@instantiate_parametrized_tests` for device-specific classes |
| **Mixing `device` param and hardcoded `"cuda"` in same class** | Pick one strategy per class |
| **Including device suffix in device-specific class name when using `instantiate_device_type_tests(..., only_for=...)` produces doubled names** | Keep the original class name — `instantiate_device_type_tests` appends the device suffix itself |
| **Derive device type from existing data — never add new parameters** | The `device` parameter from `instantiate_device_type_tests` or `tensor.device.type` from any tensor in scope already provides the device type. Adding explicit `device_type`/`device` parameters to functions that already receive tensors or have access to the test's `device` kwarg is redundant and breaks conventions (especially `autograd.Function.forward()`). |
| **Mixed-device tests** | When a test deliberately creates tensors on different devices (CPU + accelerator) for cross-device error handling: keep CPU tensors as explicit CPU, use `device` param for accelerator tensors, scope with `@onlyAccelerator`. Do NOT move to CPU-only or blindly convert all tensors to `device`. |
| **Missing `hw_classification` attribute** | Every refactored test class must have `hw_classification = HardwareClassification.XXX` as the first class attribute. Import `HardwareClassification` from `torch.testing._internal.common_utils` (merge alphabetically). CPU-only→GENERIC (or CPU for `only_for="cpu"`), device-agnostic→ACCELERATOR, device-specific→CUDA/MPS/XPU per device. |
