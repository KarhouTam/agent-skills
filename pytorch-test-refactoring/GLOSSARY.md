# PyTorch Test Refactoring

Shared vocabulary for the workflow that refactors PyTorch test files so they declare the hardware
they need instead of hardcoding it. Only the workflow's own concepts are defined here; PyTorch's
runtime concepts (operations, kernels, dispatch) live in the upstream `GLOSSARY.md`.

## Language

**Refactoring field**:
Which knowledge domain a test file belongs to -- `core`, `distributed`, or `graph` -- decided by exact
path membership in that field's list, defaulting to `core`.
_Avoid_: category, group, scope

**Family**:
A set of test files within a field that share a base class and test-generation mechanism, such as the
Inductor family inside the `graph` field. Families describe infrastructure; fields drive the workflow.
_Avoid_: subfield, suite, module

**Strategy**:
The single treatment a test class receives during refactoring: CPU-only, device-agnostic, or
device-specific. Exactly one strategy is assigned per test.
_Avoid_: plan, disposition, bucket

**CPU-only test**:
A test that uses no device-specific API and needs no device parameter, so it runs once, on CPU.
_Avoid_: host test, device-less test

**Device-agnostic test**:
A test that takes a device parameter and is instantiated once per desired device type, so its
assertions must hold on every accelerator.
_Avoid_: device-generic test, portable test

**Device-specific test**:
A test that genuinely requires one device family's API and is therefore instantiated only for that
device.
_Avoid_: CUDA test, accelerator test

**Generic test class**:
The class as written in the file, before instantiation replaces it with one subclass per device type.
Distinct from a device-agnostic classification: a generic class may still yield device-specific tests.
_Avoid_: template class, base class

**Device-type instantiation**:
The mechanism that turns one generic test class into one class per device type, threading a device
parameter into every test.
_Avoid_: device parametrization, device expansion

**Parameterization**:
The mechanism that turns one test method into one method per argument tuple, independently of device.
_Avoid_: parametrization (spell it out), subtests

**Generation mechanism**:
Any mechanism that creates test methods or test classes at collection time instead of by literal
definition. Parameterization, device-type instantiation, and class cloning are generation mechanisms.
_Avoid_: metaprogramming, magic, codegen

**Sentinel ledger**:
A set of empty files whose names are compiled-test identifiers, where a file's presence marks that
test as skipped or expected-to-fail whenever tests run under a compiler.
_Avoid_: skip list, blocklist, denylist

**Hardware classification**:
The in-tree declaration of what hardware a test class needs, replacing per-test device decorators;
the declared values are generic, accelerator, and one per device family.
_Avoid_: hardware requirement, device annotation

**Whitelist decorator**:
A decorator that names the devices a test permits, so every unlisted device is skipped.
_Avoid_: allowlist, positive filter

**Blacklist skip**:
A decorator that names the devices a test refuses, so every other device runs it.
_Avoid_: denylist, negative filter

**Field profile**:
The complete field-specific behavior: its reference documents, applicable refactoring rules, and
prompt variants. A field with no profile runs the field-agnostic baseline.
_Avoid_: config, plugin, ruleset
