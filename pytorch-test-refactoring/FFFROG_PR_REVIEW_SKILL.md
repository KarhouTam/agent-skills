SKILL.md

---
name: test-refactor
description: Refactor PyTorch test files to be device-agnostic by adding HardwareClassification labels, splitting mixed device/generic classes, and replacing hardcoded device references. Use when refactoring tests to pass TEST_LINTER, making tests reusable by out-of-tree accelerators, or when the user mentions hw_classification, device-agnostic tests, or test hardware classification.
---

# Test Refactor — Device-Agnostic PyTorch Tests

Refactors PyTorch test files so they declare hardware requirements explicitly via
`hw_classification`, making them reusable by out-of-tree accelerators. Every
`TestCase` subclass with `test_*` methods must carry a valid classification.

See [reference.md](reference.md) for the full rules reference and pattern catalog.

## Quick decision tree

```
Does the class have test_* methods?
  No → no label needed (fixture / mixin base)
  Yes → read on

Do test methods accept a `device`/`devices` parameter?
  No → GENERIC
  Yes → Are the tests tied to exactly ONE device type?
    Yes → CPU / CUDA / MPS / XPU (use only_for=<device>)
    No  → ACCELERATOR (use except_for for exclusions)
```

## Instructions

### Step 1: Inventory the file

Read the test file. For every top-level class that defines `test_*` methods,
record:
- Whether its test methods accept a `device` (or `devices`) parameter
- Whether it's already instantiated via `instantiate_device_type_tests`
- What `only_for` / `except_for` kwargs are used (if any)
- Any `@onlyCPU`, `@onlyCUDA`, `@onlyMPS`, `@onlyXPU`, `@onlyAccelerator`
  decorators on test methods
- Any hardcoded device strings (`"cuda"`, `"cpu"`, `.cuda()`, etc.)
- Whether it already has `hw_classification`

### Step 2: Classify each class

Apply the decision tree above. A class with mixed methods (some take `device`,
some don't) must be split (Pattern 2).

### Step 3: Apply the refactoring

Apply the pattern that matches. The patterns are summarised below; full
before/after examples live in [reference.md](reference.md).

**Pattern 1 — Add label only:** Class already follows the rules but lacks
`hw_classification`. Add the import and the class attribute.

**Pattern 2 — Split mixed class:** Extract device-agnostic tests into a GENERIC
subclass and device-dependent tests into an ACCELERATOR subclass. Share
fixtures/helpers via a common base class with no `test_*` methods.

**Pattern 3 — Inject device param:** Convert hardcoded `"cuda"` / `.cuda()` to
the injected `device` parameter.

**Pattern 4 — Blacklist instead of whitelist:** Replace
`only_for=("cuda", "hpu", "xpu")` with `except_for=("cpu",)`.

**Pattern 5 — Replace CUDA guards:** `torch.cuda.is_available()` →
`torch.accelerator.is_available()`, `@onlyCUDA` → `@onlyAccelerator`.

**Pattern 6 — CPU-only class:** Add `hw_classification = HardwareClassification.CPU`
and `only_for="cpu"`.

**Pattern 7 — CUDA-only class:** Add `hw_classification = HardwareClassification.CUDA`
and `only_for="cuda"`. Keep truly cuda-specific logic intact.

### Step 4: Verify the result

Check every point:

- [ ] Every class with `test_*` methods has `hw_classification`.
- [ ] GENERIC classes are NOT passed to `instantiate_device_type_tests`.
- [ ] ACCELERATOR / device-specific classes ARE passed to
  `instantiate_device_type_tests`.
- [ ] ACCELERATOR classes use `except_for`, never `only_for`.
- [ ] Device-specific classes use `only_for=<device>`, never `except_for`.
- [ ] ACCELERATOR methods only use `@onlyAccelerator` (no `@onlyCPU`, etc.).
- [ ] `hw_classification` uses the exact form `HardwareClassification.MEMBER`.
- [ ] The `HardwareClassification` import is present.
- [ ] `instantiate_device_type_tests` calls are at module top-level.

### Step 5: Remove from allowlist

Once the file is fully refactored, remove its entry from
`tools/linter/adapters/test_linter_allowlist.json`. The entry is the path
relative to the repo root (e.g. `test/test_sort_and_select.py`).

## Important rules

- **Mixins with `test_*` methods need labels too.** The linter uses static AST
  (not `TestCase` inheritance), so any class defining `test_*` is examined.
- **Fixture bases without `test_*` methods need no label.**
- **Declaration syntax:** Only these two forms are accepted:
  ```python
  hw_classification = HardwareClassification.GENERIC
  hw_classification: HardwareClassification = HardwareClassification.GENERIC
  ```
- **`instantiate_device_type_tests` placement:** The call must be at module
  top-level, not inside `if __name__ == "__main__"`.
- **`@onlyAccelerator` on ACCELERATOR classes:** Allowed. The method still
  receives `device`; it just skips on CPU/meta.
- **`allow_xpu=True` / `allow_mps=True`:** Pass these to
  `instantiate_device_type_tests` for ACCELERATOR classes to include those
  devices.

## Imports to add

```python
from torch.testing._internal.common_utils import HardwareClassification
```

Already-common imports (usually present):
```python
from torch.testing._internal.common_device_type import (
    instantiate_device_type_tests,
    onlyAccelerator,
)
```