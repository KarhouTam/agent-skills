---
name: triaging-vllm-issues
description: Use when asked to find open, unassigned, or claimable vllm-project/vllm issues to work on, or when judging whether a vLLM issue can be reproduced or verified on this machine's hardware (GPU model, compute capability, accelerator vendor, GPU count).
---

# Triaging vLLM Issues

## Overview

Match vLLM issues to what this machine can actually reproduce and verify.
Filter by the **specific feature** an issue needs, never by "needs a GPU" —
a present accelerator is an asset, so an issue that needs it is workable, not
disqualified.

Derive the capability profile with the commands below every time. Never carry
forward hardware, arch, or interpreter values from documentation, memory, or
an earlier session.

## Step 1 — Detect the accelerator and the build targets

Commands fail in some environments; fall through until one answers.

| Fact | Command |
|---|---|
| Model, vendor, count | `cat /proc/driver/nvidia/gpus/*/information` (one dir per GPU); `rocm-smi`; `xpu-smi` |
| Name, compute capability, VRAM | `nvidia-smi --query-gpu=name,compute_cap,memory.total --format=csv` |
| Runtime visibility | `python3 -c "import torch;print(torch.cuda.is_available(),torch.cuda.device_count())"` |
| Arch the local kernels were built for | `cuobjdump --list-elf vllm/_C*.so \| head` |
| Interpreter with vLLM importable | `python3 -c "import vllm,sys;print(sys.executable,vllm.__version__)"` |
| Toolchain | `nvcc --version`; `hipcc --version` |

Take the arch from the linked cubins or the most recent CMake configure log,
not from `CMakeCache.txt`: its cache entry can retain a value predating the
project's own override. Take the Python interpreter from `sys.executable` —
a venv path named in contributor docs may never have been created. Log the
device count, since it bounds distributed work.

## Step 2 — Derive what the profile can and cannot do

Device capability and build targets together bound the feature set. Check each
issue's required feature against these gates:

- **BF16, TF32, FlashAttention-2, FlashInfer, AWQ/GPTQ/Marlin, CUDA graphs** — Ampere and newer.
- **Native FP8** — Ada and newer.
- **FlashAttention-3/4, TMA, WGMMA, DeepGEMM, FlashMLA, Machete kernels** — Hopper and newer.
- **FP4 / MXFP4 / NVFP4** — Blackwell.
- **Any >1-GPU feature** (TP/PP/EP, NCCL, NVLink, multi-node, disaggregation) — needs device count > 1.
- **Non-CUDA vendor features** (ROCm, XPU, TPU, Apple) — need that vendor's stack.

The local build is the authoritative narrowing: a configure log reports
`-- Not building <kernel> as no compatible archs found`, which names exactly
the kernels this machine did not build.

## Step 3 — Fetch open, unassigned issues

```bash
gh issue list --repo vllm-project/vllm --state open --search "no:assignee" \
  --limit 200 --json number,title,labels,createdAt
```

`--search "no:assignee"` is the reliable unassigned filter; confirm each
survivor's `assignees` is still empty before acting.

## Step 4 — Assign each candidate exactly one tier

| Tier | Meaning | Decided by |
|---|---|---|
| **A** | Verifiable now | Runs on CPU or the compiler alone — parser, tokenizer, structured output, entrypoint, scheduler, config, docs, CUDA compilation |
| **B** | Verifiable with accelerator access | Needs the local device, using only features Step 2 shows it supports |
| **C** | Not resolvable here | Needs a feature Step 2 rules out, or another vendor |

When Step 1 shows the device exists but the runtime cannot reach it (vendor
library init fails, device nodes denied), Tier B is gated on a permission
escalation — in DSH, `danger-full-access`, which a human approves. Name that
escalation in the report instead of quietly demoting Tier B to Tier C.

Labels (`rocm`, `intel-gpu`, `tpu`) and titles naming a vendor generation are
fast tier hints, but confirm them against Step 2.

## Step 5 — Duplicate-work gate (REQUIRED)

Repo policy forbids duplicate PRs. Run all three searches:

```bash
gh issue view <n> --repo vllm-project/vllm --comments
gh pr list --repo vllm-project/vllm --state open --search "<n> in:body"
gh pr list --repo vllm-project/vllm --state open --search "<2-3 keywords>"  # load-bearing
```

A hit from any of them disqualifies the issue. A **miss does not clear it**:
fix PRs routinely omit the issue number, so the number search is the weak one
and the keyword search is what clears a candidate. Quote the keyword-search
output as the evidence.

## Step 6 — Staleness gate (REQUIRED)

Open the cited code path, or run the cited test, against this checkout. Drop
anything already fixed here, and record the `file:line` you read or the test
command and result. A stale issue looks identical to a live one in the
tracker, so this gate is the only thing that separates them.

## Output contract

One table, every candidate in it — Tier B is never silently dropped:

| Issue | Tier | Required capability | Why it fits here | Dup evidence (keyword search) | Still present at |

Then two short lists: Tier B issues with the escalation each needs, and the
single best Tier A pick with a one-line justification. The last two columns
carry the Step 5 and Step 6 evidence; a row with either cell empty is not a
candidate.

## Common mistakes

| Mistake | Correction |
|---|---|
| Concluding "CPU-only" and excluding all device work | The device is present; device issues are Tier B, gated on escalation |
| Treating "needs a GPU" as one capability | Match the specific feature against the Step 2 gates |
| Believing a cached or documented arch value | Read the linked cubins or the configure log |
| Clearing an issue because `"<n> in:body"` returned nothing | That search misses PRs that never cite the number; only the keyword search clears |
| Trusting the issue text as current | Re-read the code path and confirm the defect is still there |
| Assuming an interpreter path from contributor docs | Resolve it with `sys.executable` |
| Leaving scratch files or caches in the checkout | Use a scratch dir outside it and delete it |

## Red flags — stop and re-tier

- Every device issue landed in the same bucket.
- The report has no Tier B row.
- "No GPU available" is claimed without a device-detection command.
- A hardware, arch, or interpreter value appears that Step 1 did not produce.
- Only the number search was run before calling a candidate unclaimed.
