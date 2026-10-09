# Target config — examples

`target.md` in this directory is plain natural language. There is no format to
follow: the triage workflow reads what you wrote and resolves it into GitHub
labels, code paths and search keywords, then shows you that resolution before it
searches anything.

Copy any of these shapes into `target.md`, or write your own.

## Terse

```
Inductor. CPU-side codegen mostly.
```

## Prose with exclusions

```
I want to work on inductor — the compiler backend, not the docs.
Anything xpu- or rocm-specific I can't test, so skip it.
```

## Two modules

```
FSDP2 is my main interest: the fully_shard API and anything that actually
breaks it in practice. DTensor matters only when FSDP2 is what breaks.

Secondarily, torch.compile CPU codegen.
```

## What the workflow does with it

| You write          | It resolves                                                                                                                      |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------- |
| `inductor`         | `module: inductor`, paths `torch/_inductor/**` and `test/inductor/**`, CPU-only work under `oncall: cpu inductor`                |
| `fsdp2`            | no such label exists — the API `fully_shard` → `torch/distributed/fsdp/_fully_shard/**` → `module: fsdp` + `oncall: distributed` |
| `not the docs`     | drops `module: docs` / `topic: docs`                                                                                             |
| `I can't test xpu` | flags those candidates rather than hiding them                                                                                   |

Machine capability is measured, not read from this file — a claim here that
contradicts the probe is reported as a contradiction.
