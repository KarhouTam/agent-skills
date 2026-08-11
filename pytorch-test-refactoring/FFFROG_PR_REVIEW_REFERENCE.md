# Test Refactor — Full Rules Reference & Pattern Catalog

## HardwareClassification enum

```python
class HardwareClassification(Enum):
    GENERIC = "generic"        # device-agnostic: no device param, no instantiate
    ACCELERATOR = "accelerator" # any accelerator: device param, except_for only
    CPU = "cpu"                 # cpu-only: device param, only_for="cpu"
    CUDA = "cuda"               # cuda-only: device param, only_for="cuda"
    MPS = "mps"                 # mps-only: device param, only_for="mps"
    XPU = "xpu"                 # xpu-only: device param, only_for="xpu"
```

## Linter rules by classification

### GENERIC

| Rule | Linter message |
|------|---------------|
| Must NOT be passed to `instantiate_device_type_tests` | `[instantiation]` |
| Test methods must NOT accept `device`/`devices` | `[device_param]` |

### ACCELERATOR

| Rule | Linter message |
|------|---------------|
| MUST be passed to `instantiate_device_type_tests` | `[instantiation]` |
| Every test method MUST accept `device`/`devices` | `[device_param]` |
| No `@onlyCPU`, `@onlyCUDA`, `@onlyMPS`, `@onlyXPU` | `[decorator]` |
| `@onlyAccelerator` is allowed | — |
| `only_for` is FORBIDDEN | `[only_for]` |
| Use `except_for` instead | — |

### CPU / CUDA / MPS / XPU

| Rule | Linter message |
|------|---------------|
| MUST be passed to `instantiate_device_type_tests` | `[instantiation]` |
| Every test method MUST accept `device`/`devices` | `[device_param]` |
| MUST use `only_for=<device>` (exact match) | `[only_for]` |
| `except_for` is FORBIDDEN | `[except_for]` |

## Pattern catalog — before/after

### Pattern 1: Add label only (class already compliant)

**Before:**
```python
class TestFoo(TestCase):
    def test_bar(self):
        x = torch.randn(3, 3)
        ...
```

**After:**
```python
from torch.testing._internal.common_utils import HardwareClassification

class TestFoo(TestCase):
    hw_classification = HardwareClassification.GENERIC

    def test_bar(self):
        x = torch.randn(3, 3)
        ...
```

### Pattern 2: Split mixed class

**Before — one class mixes device-agnostic and device-specific tests:**
```python
class TestOps(TestCase):
    def test_op_metadata(self):           # no device needed
        self.assertEqual(op.name, "add")

    def test_op_on_device(self, device):  # needs device
        x = torch.randn(3, 3, device=device)
        ...

instantiate_device_type_tests(TestOps, globals(), only_for=("cuda", "hpu", "xpu"))
```

**After — split into base + GENERIC + ACCELERATOR:**
```python
from torch.testing._internal.common_utils import HardwareClassification

class TestOpsBase(TestCase):
    """Shared fixtures; no test_* methods, so no label needed."""
    def setUp(self):
        super().setUp()
        self.op = ...

class TestOps(TestOpsBase):
    hw_classification = HardwareClassification.GENERIC

    def test_op_metadata(self):
        self.assertEqual(self.op.name, "add")

class TestOpsDevice(TestOpsBase):
    hw_classification = HardwareClassification.ACCELERATOR

    def test_op_on_device(self, device):
        x = torch.randn(3, 3, device=device)
        ...

instantiate_device_type_tests(TestOpsDevice, globals(), except_for=("cpu",))
```

### Pattern 3: Inject device param

**Before:**
```python
class TestFoo(TestCase):
    def test_cuda_only(self):
        x = torch.randn(3, 3, device="cuda")
        y = torch.randn(3, 3).cuda()
        self.assertEqual(x, y)
```

**After:**
```python
class TestFoo(TestCase):
    hw_classification = HardwareClassification.ACCELERATOR

    def test_on_device(self, device):
        x = torch.randn(3, 3, device=device)
        y = torch.randn(3, 3, device=device)
        self.assertEqual(x, y)

instantiate_device_type_tests(TestFoo, globals(), except_for=("cpu",))
```

### Pattern 4: Whitelist → blacklist

**Before:**
```python
class TestFoo(TestCase):
    def test_op(self, device):
        ...

instantiate_device_type_tests(TestFoo, globals(), only_for=("cuda", "hpu", "xpu"))
```

**After:**
```python
class TestFoo(TestCase):
    hw_classification = HardwareClassification.ACCELERATOR

    def test_op(self, device):
        ...

instantiate_device_type_tests(TestFoo, globals(), except_for=("cpu",))
```

### Pattern 5: CUDA guards → accelerator guards

**Before:**
```python
class TestFoo(TestCase):
    @unittest.skipIf(not torch.cuda.is_available(), "requires CUDA")
    def test_op(self):
        ...

    @onlyCUDA
    def test_cuda_specific(self, device):
        ...
```

**After:**
```python
class TestFoo(TestCase):
    @unittest.skipIf(not torch.accelerator.is_available(), "requires accelerator")
    def test_op(self):
        ...

    @onlyAccelerator
    def test_accelerator_specific(self, device):
        ...
```

### Pattern 6: CPU-only class

**Before:**
```python
class TestCPU(TestCase):
    def test_cpu_behavior(self, device):
        ...

instantiate_device_type_tests(TestCPU, globals(), only_for="cpu")
```

**After:**
```python
from torch.testing._internal.common_utils import HardwareClassification

class TestCPU(TestCase):
    hw_classification = HardwareClassification.CPU

    def test_cpu_behavior(self, device):
        ...

instantiate_device_type_tests(TestCPU, globals(), only_for="cpu")
```

### Pattern 7: CUDA-only class

**Before:**
```python
class TestCUDASpecific(TestCase):
    @onlyCUDA
    def test_cuda_graph(self, device):
        g = torch.cuda.CUDAGraph()
        ...

instantiate_device_type_tests(TestCUDASpecific, globals(), only_for="cuda")
```

**After:**
```python
from torch.testing._internal.common_utils import HardwareClassification

class TestCUDASpecific(TestCase):
    hw_classification = HardwareClassification.CUDA

    def test_cuda_graph(self, device):
        g = torch.cuda.CUDAGraph()
        ...

instantiate_device_type_tests(TestCUDASpecific, globals(), only_for="cuda")
```

## Allowlist

`tools/linter/adapters/test_linter_allowlist.json` maps file paths (relative to
repo root) to `true`. A file in the allowlist is skipped by the linter. Once
every class in a file has a valid `hw_classification`, remove the file's entry
from the allowlist.

Example entry removal:
```json
// Before
{
    "test/test_sort_and_select.py": true,
    ...
}
// After — line removed
```

## Edge cases

### Decorator stacking order on ACCELERATOR classes

`@onlyAccelerator` goes below parameterized/generic decorators, above the
method:

```python
class TestFoo(TestCase):
    hw_classification = HardwareClassification.ACCELERATOR

    @onlyAccelerator
    @skipXPUIf(True, "reason")
    def test_op(self, device):
        ...
```

### Classes instantiated but NOT by instantiate_device_type_tests

Some test files use custom instantiation (e.g. `@instantiate_parametrized_tests`
or manual test generation). Those classes still need `hw_classification` if they
define `test_*` methods. Follow the same classification rules.

### Vendor / copied tests

`test/cpython/**` and `test/cpp_extensions/open_registration_extension/**` are
excluded from linting. Don't refactor those.

### Tests that import device_type from module level

Some files define `device_type = torch.accelerator.current_accelerator(...)`
at module level. This is fine; the refactoring focuses on test class structure.

### Dynamo-wrapped tests (`@skipIfTorchDynamo`, etc.)

These decorators are orthogonal to hardware classification. Don't touch them
during refactoring.