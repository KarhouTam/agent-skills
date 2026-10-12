# Graph-field refactoring: knowledge-first entry, two-axis coupling model

The `graph` field (582 listed paths across fx, dynamo, inductor, aoti, export and onnx) has run the
field-agnostic baseline since fields were introduced: `compute_applicable_rules()` returns
`["cleanup"]` for every non-core field, and the baseline prompts forbid device decoupling. Its
hardware coupling is heavier than core's (`GPU_TYPE` appears 3568 times in the inductor family,
`@requires_gpu` 403) and its failure bookkeeping is name-keyed across roughly 9,300 sentinel files,
so the baseline is not an acceptable end state. We decided to land field knowledge first --
`reference/graph/test_infra.md` backed by a rerunnable census in
`scripts/graph_test_infra_census.py` -- and to change no rules, state machines, or prompt semantics
until that document has been reviewed; any future graph profile is derived from it.

Coupling in this field is modeled on two axes rather than one. The device axis (cpu/cuda/xpu/mps/
mtia) is what the core field's three strategies already describe; the backend/mode axis (inductor
versus eager, dynamic shapes, freezing, cudagraphs, export strictness) is what actually generates
the bulk of the field's test classes, through the class-cloning generators rather than through
device instantiation. Each family is mapped onto the in-tree `HardwareClassification` values --
`GENERIC` for the backend axis, `ACCELERATOR`/`CPU`/`CUDA`/`MPS`/`XPU` for the device axis -- instead
of introducing a graph-specific vocabulary.

## Considered Options

- **Write a full graph profile directly** (new `REFACTOR_RULES`, graph-specific prompts, new verify
  checks). Rejected: the rule vocabulary was not yet known, and mistakes would be enforced on every
  file in a 391-file field rather than documented and reviewed.
- **Leave the field on the baseline indefinitely.** Rejected: renaming a test or moving a class in
  this field silently drops coverage, because the sentinel ledgers are keyed on generated class and
  method names with no orphan detection.
- **Analyze only the modern compiler stack** (dynamo/inductor/export). Rejected: field membership is
  what the workflow acts on, so the document would have had no section for files the field routes to
  it; the peripheral families are listed as out of scope instead.

## Consequences

- The document is advisory. Nothing in it is machine-checked except the census; `flow.py` does not
  block on it, and the graph field still computes `["cleanup"]` as its applicable rules.
- The census is the contract for every number in the document. A claim that disagrees with
  `scripts/graph_test_infra_census.py` is a bug in the document, and the document records the commit
  it was generated at.
- The same `{ref_dir}/test_infra.md` convention reaches the baseline prompts conditionally, so the
  `distributed` field can adopt it later without touching `tasks.py` again.
