---
name: triaging-pytorch-issues
description: Use when asked to find open, claimable pytorch/pytorch issues to work on for a module configured in this skill's target file (inductor, fsdp2, and the like), or when judging whether a PyTorch issue is still present, already claimed, or reproducible and verifiable on this machine.
---

# Triaging PyTorch Issues

## Overview

Match pytorch/pytorch issues to two things: what this machine can actually
reproduce, and what nobody is already fixing. Filter by the **specific
capability** an issue needs, never by "needs a GPU".

The target module comes from `target.md` beside this `SKILL.md`, written as
plain natural language. Never ask the user to reformat it, and never invent a
target it does not name.

Derive every machine fact below on each run. Never carry an interpreter,
device, or checkout value over from another session.

## Step 0 — Turn the target prose into a search spec

- Read `target.md`. If the request names a different file, read that instead.
  The file wins over the request; if it is missing or silent on a target, use
  the module named in the request and offer to append it — if there is none,
  stop and ask.
- Print the resolved spec and reuse it verbatim for the whole run:
  **labels**, **paths**, **keywords** (2-4 words for PR search: symbols, API
  names, error strings), **exclude** (what the prose rules out).
- Resolve each term in this order:
  1. exact label match against the live list
     (`gh label list --repo pytorch/pytorch --limit 1000 --json name --jq '.[].name'`);
  2. the code the term names (`git -C <checkout> ls-files`, `ls`);
  3. that code's `module:` label;
  4. domain and oncall labels from `.github/labeler.yml` globs and, when the
     checkout has them, `.claude/skills/triaging-issues/*.md` and
     `.claude/skills/distributed-triage/distributed-rubric.md`.

**Validate every label against the live list before searching it.** A label
that does not exist returns zero results and reads as "no work available".

- `.github/labeler.yml` is a path→label map but has only 24 entries
  (`module: inductor`, `module: dynamo`, `module: cpu`, …) and **no
  `module: fsdp`**. A miss there is not proof the module has no label.
- `.claude/skills/triaging-issues/labels.json` is a 282-label *triage*
  allowlist. It omits `actionable`, `good first issue`, `needs reproduction`,
  `repro:agent:*` and `oncall: distributed parallelisms` — never use it as the
  existence check.
- Unresolvable term → an open question in the report. Never substitute a
  plausible-looking label.

Worked example — both from real configs:

| Target prose | Resolves to |
|---|---|
| "inductor" | `module: inductor` ← `torch/_inductor/**`, `test/inductor/**`; CPU-only work also sits under `oncall: cpu inductor` |
| "fsdp2" (no such label) | the API `fully_shard` → `torch/distributed/fsdp/_fully_shard/**` → `module: fsdp` + `oncall: distributed` (sub-oncall `oncall: distributed parallelisms`), overlap `module: dtensor` |

## Step 1 — Probe this machine

Fall through until a command answers, and keep going if the first interpreter
is broken — a half-installed torch is worse than none:

```bash
for P in python3 python /root/miniconda3/envs/*/bin/python "$HOME/miniconda3/envs"/*/bin/python; do
  command -v "$P" >/dev/null 2>&1 || [ -x "$P" ] || continue
  "$P" -c 'import torch' 2>/dev/null && { PY="$P"; break; }
done
[ -n "$PY" ] || { echo "no interpreter here can import torch"; exit 1; }
"$PY" - <<'PY'
import importlib.util, os, sys, torch
print("interpreter", sys.executable)
print("torch", torch.__version__, "git", torch.version.git_version)
print("checkout", os.path.dirname(os.path.dirname(torch.__file__)))
print("cuda", torch.cuda.is_available(), torch.cuda.device_count())
print("xpu", getattr(torch, "xpu", None) is not None and torch.xpu.is_available())
try:
    print("npu", torch.npu.is_available(), torch.npu.device_count())
except Exception as e:
    print("npu unreachable:", type(e).__name__)
import torch.distributed as dist
print("gloo", dist.is_gloo_available(), "nccl", dist.is_nccl_available(), "mpi", dist.is_mpi_available())
print("triton", importlib.util.find_spec("triton") is not None)
PY
echo "arch $(uname -m) cores $(nproc)"; free -g | awk 'NR==2{print "ram_gb",$2}'
for T in npu-smi nvidia-smi rocm-smi xpu-smi; do command -v $T >/dev/null && echo "host tool: $T present"; done
```

Then prove Tier A rather than assuming it: one tiny
`torch.compile(backend="inductor")` call with `TORCHINDUCTOR_CACHE_DIR` under
`/tmp` shows whether CPU codegen works at all.

A host-level accelerator and a torch-reachable one are different facts: report
both. `npu-smi` answering while `torch.npu` does not exist means the hardware is
present with no working stack bound to it — that is Tier B, not "no GPU".

## Step 2 — Tier every candidate

| Tier | Meaning | Decided by |
|---|---|---|
| **A** | Verifiable now on CPU | Frontend/compiler logic (dynamo, decompositions, guards, export, fx, python dispatcher, arch-specific code), CPU inductor codegen, CPU+gloo multi-process distributed (FSDP2, DDP, c10d, checkpoints) |
| **B** | Accelerator present, unreachable through torch | **Name the human escalation** (a working stack for that device); never quietly demote to C |
| **C** | Nothing here can run it | CUDA/NCCL/ROCm/XPU/TPU/MPS, multi-GPU, x86-only ISA, Windows |

The probe decides what *runs*; the prose decides what *matters*. If the config
says "CPU only" and the probe finds an accelerator, report the contradiction and
tier on the probe.

## Step 3 — List candidates

```bash
gh issue list --repo pytorch/pytorch --state open \
  --search 'label:"module: inductor"' --limit 200 \
  --json number,title,labels,assignees,createdAt,updatedAt
```

- `no:assignee` and `-linked:pr` are **not** claimability filters on PyTorch:
  assignment is rare and lags the work, and `-linked:pr` only sees closing
  links (see Step 4). Do not shape the query around either.
- Rank by workability signals, never require them: `triaged` is the baseline
  (most FSDP2 issues carry it and nothing else), while `actionable`,
  `good first issue`, `OSS contribution wanted`, `module: bootcamp` and
  `internal ramp-up task` are bonuses when present.
- Flag `needs reproduction`, `repro:agent:*:repro_fail:*`, `repro:agent:incorrect`
  and `triage review` in the row; they predict the issue stalling.
- **Priority**: read the labels from the JSON you already fetched — no second
  query. Drop `low priority` rows from the table and state how many were
  dropped; mark `high priority` with `⚑` and repeat those rows in their own
  short list. Match the exact names: `intel priority`, `rocm priority` and
  `arm priority` are not the user's priority.
- **Query budget**: `gh issue list` / `gh pr list` are GraphQL (5000/h). The
  REST search endpoints — `gh api search/issues`, `gh search` — are 30/min and
  start failing mid-run with secondary rate-limit errors, which is what a long
  sweep does.

## Step 4 — Duplicate-work gate (REQUIRED)

Run all of it; quote what each returned.

```bash
# (a) linked PRs, assignees, claim comments. Nothing in gh's issue JSON
#     exposes the timeline, so this needs GraphQL.
gh api graphql -f query='
query { repository(owner:"pytorch", name:"pytorch") { issue(number:NNNNN) {
  assignees(first:5){nodes{login}}
  closedByPullRequestsReferences(first:5){nodes{number state}}
  timelineItems(last:50, itemTypes:[CROSS_REFERENCED_EVENT]) { nodes {
    ... on CrossReferencedEvent { source { ... on PullRequest { number state isDraft } } } } } } } }' \
  --jq '.data.repository.issue | "\(.number) assigned=\([.assignees.nodes[].login]) closedBy=\([.closedByPullRequestsReferences.nodes[]|"\(.number)/\(.state)"]) linked=\([.timelineItems.nodes[].source|select(.number != null)|"\(.number)/\(.state)"]|join(" "))"'
# (b) LOAD-BEARING: PRs that never cite the issue number, or cite it without a
#     closing keyword. Without these, in-flight work is invisible.
gh pr list --repo pytorch/pytorch --state open --search "<2-3 keywords>" --limit 10 --json number,title
gh pr list --repo pytorch/pytorch --state merged --search "<2-3 keywords>" --limit 10 --json number,title,mergedAt
# (c) claims in the comments
gh issue view NNNNN --repo pytorch/pytorch --json comments
```

- The **timeline** is the linked item on the issue panel — any referencing PR,
  open, draft or closed, counts.
- `closedByPullRequestsReferences` is a hint, not a clearance: it is populated
  for non-draft linked PRs and was empty for every draft-PR case tested.
- `assignees` is usually empty, but a maintainer's handle there means the issue
  is owned.
- **A clean timeline and no assignee clear nothing.** Real cases where every
  link field was empty yet the fix was already open: an atanh-vectorization
  issue with a live PR rewriting the same override, and three more like it.
  Only the keyword sweep finds those.

## Step 5 — Staleness gate (REQUIRED)

A PyTorch checkout is routinely hundreds of commits behind the fetched ref —
this one was 681, with 96 in `torch/_inductor` alone. A verdict read from the
working tree is a guess.

```bash
CHK=/path/to/pytorch        # the checkout printed in Step 1
REF=upstream/main           # newest local ref: upstream/main, else origin/main
git -C "$CHK" remote -v | grep -q pytorch/pytorch || echo "not a pytorch/pytorch checkout"
git -C "$CHK" for-each-ref --format='%(refname:short) %(committerdate:short)' \
  refs/remotes/upstream/main refs/remotes/origin/main    # newest local ref + its age
git -C "$CHK" rev-list --count "HEAD..$REF"
# decide at the REF, not the tree
git -C "$CHK" grep -n "<symbol or error string>" "$REF" -- PATH
git -C "$CHK" log --oneline "HEAD..$REF" -S"<symbol or error string>" -- PATH
git -C "$CHK" log --oneline "HEAD..$REF" --grep="<keyword>" -- PATH
```

Drop the issue when the cited behaviour is gone at `$REF`, and cite the commit
that removed it — `-S` names it.

A feature request has no error string to grep: record the constraint that binds
its cell (the flag or argument is still absent at `$REF` @ `<sha>`), or it is not
a candidate.

Real case: an open FSDP2 issue's error string sits at HEAD
(`torch/distributed/fsdp/_fully_shard/_fsdp_collectives.py:615`) and is gone
tree-wide at `upstream/main`; `-S` names `38d3f6fd0d6`. Reading the working tree
would send someone at an already-fixed bug and call it a free test gap.

Check for a local fetched ref **before** reaching for the network (a GitHub
contents/commit API read per candidate is slow and rate-limited); if the ref is
older than the issue itself, say so and offer to refresh it.

Never `git fetch`, `checkout`, `stash` or `reset` by default — the tree may be
someone's dirty working copy — and keep scratch files and inductor caches under
`/tmp`.

## Step 6 — Write the report file (REQUIRED)

The triage result is a **markdown file, not a chat message**. A run that only
printed a report did not finish.

Decide the destination on the **git remote**, never on the directory existing:
any repo can sit at that path, and only a pytorch/pytorch one may receive the
report. Read-only — do not reuse Step 5's `$CHK`, which holds the checkout path.

```bash
N=$(git -C <checkout> remote -v 2>/dev/null | grep -c 'pytorch/pytorch')
if [ "${N:-0}" -gt 0 ]; then OUT="<checkout>"; else OUT="<this skill's directory>"; fi
echo "report: $OUT/pytorch-triage-<target-slug>-$(date -u +%F).md"
```

| Priority | Condition | Destination |
|---|---|---|
| 1 | `<checkout>`'s `git remote -v` names `pytorch/pytorch` | `<checkout>/pytorch-triage-<target-slug>-<YYYY-MM-DD>.md` |
| 2 | no checkout, or a checkout whose remotes never name `pytorch/pytorch` | `<this skill's directory>/pytorch-triage-<target-slug>-<YYYY-MM-DD>.md` |

`<target-slug>` is the Step 0 target, lowercased and hyphenated (`fsdp2`,
`inductor`); the date is UTC so a rerun the same day overwrites its own file
rather than littering. An installed wheel is not a checkout: a path with no
`.git` means priority 2.

- **Follow the guidance from the repo first (usually agent_space/ subdirectory), and fallback to this skill's directory.**
- The report is not a scratch file: Step 5's `/tmp` rule covers intermediates
  (issue dumps, inductor caches), not where the report lands.
- The chat message carries the **absolute** path actually written. Never `~`,
  never a `$CHK` placeholder.
- Priority 1 is usually outside this session's workspace. Write it; if the file
  sandbox denies that, retry the same write once with the wider-permission flag
  and a one-sentence justification. When approval is unavailable or refused, the
  report still gets written — to priority 2, the user's stated fallback.
- A priority-2 write made **after a priority-1 denial** is a fallback, not a
  free choice: mark it in the report's first line (`> fallback: write to
  <the checked-out path> was denied`) and carry the path it should live at into
  the chat message. Relocating silently hides that the checkout copy is missing;
  writing nothing throws the run away.

## Output contract

### The report file

Open with the pick, then the evidence:

```markdown
# PyTorch triage — <target> — <YYYY-MM-DD>

## Target spec (Step 0)
- labels: … (each validated against the live list)
- paths: … · keywords: … · exclude: …
- unresolved terms: … (or "none")

## Machine (Step 1)
- interpreter … · torch … @ git … · checkout …
- accelerators: reachable … / host-present … · distributed: … · arch/cores/ram …
- Tier A proof: torch.compile(backend="inductor") on CPU → <result>
- tier this machine implies: A/B/C
- config-vs-probe contradiction, if any (`target.md` claims an A100; nothing here
  reaches it → the CUDA-gated rows below sit at Tier B)

## Best pick
**#NNNNN <title>** — Tier A · <one-line justification>

| Issue | ⚑ | Tier | Summary | Related module(s) | Complexity | Requirement | Workability signals | Dup evidence | Still present at |
|---|---|---|---|---|---|---|---|---|---|

### High priority
| Issue | Tier | Summary | Requirement | … |

### Tier B — needs a human escalation
| Issue | Unreachable device | Escalation to request |

### Dropped — low priority
| Issue | Signals | Why dropped |

dropped: N low priority (<reason>)

## Open questions
- …

## Self-check
- [ ] every label validated against the live list
- [ ] every candidate survived the keyword-PR sweep (Step 4)
- [ ] every staleness verdict read at `$REF`, not the working tree
- [ ] four required fields present in every row
- [ ] the file sits at its Step 6 destination (checkout root when the
      `pytorch/pytorch` remote validated, else this skill's directory)
- [ ] a priority-2 write that followed a denied priority-1 write carries the
      `fallback:` line, and chat names the path it should live at
- [ ] every `Still present at` cell pins a short SHA
```

Per row:

- **Summary** — one line naming the failure and its trigger, never a copy of the
  title.
- **Related module(s)** — the `module:`/`oncall:` label plus the owning paths
  (`torch/_inductor/codegen/cpp.py`), taken from the Step 0 resolution and the
  code the issue cites.
- **Complexity** — `small` / `medium` / `large`: one file and an obvious change /
  several files or a new test surface / cross-subsystem, design, or perf work.
  Append the evidence (`1 file, 2 call sites`, `touches decompositions + tests`).
  A bare label with no justification is guesswork.
- **Requirement** — what taking the issue needs, machine and contributor:
  `CPU+gloo ✓; A100 for the CUDA variant`, `familiarity with inductor caching`.
  The machine half comes from the Step 1 probe and Step 2 tier, the contributor
  half from the labels and the code.
- **Still present at** — the ref **and** its short SHA
  (`` `upstream/main` @ dfb3c303857 ``), so the verdict can be re-derived. A bare
  ref, `HEAD`, or `<sha>` placeholder is not evidence.

Every row in the main table carries all four; a row missing one is not a
candidate.

### The chat message

Cites the absolute path actually written — plus the intended checkout path when
the write fell back there — then carries only: the resolved target spec,
the probe summary with its tier, the best Tier A pick with its one-line
justification, the priority and dropped counts, Tier B rows with their
escalations, and open questions. The table lives in the file, not in the
message.

## Common mistakes

| Mistake | Correction |
|---|---|
| Searching `label:"module: fsdp2"` | No such label (only `release notes: distributed (fsdp2)`); the search returns nothing and reads as no work. Resolve the API `fully_shard` to `module: fsdp` |
| Treating `labels.json` as the label allowlist | It omits the workability and repro labels this skill ranks on; validate against `gh label list` |
| Calling an issue claimable because it is unassigned and has no linked PR | Real cases had a live PR rewriting the exact code with no link anywhere; run the keyword sweep |
| Trusting `-linked:pr` / `closedByPullRequestsReferences` | They only see closing links; drafts and non-closing PRs stay invisible |
| Judging staleness from the working tree | An agent did exactly this and called a fixed FSDP2 issue a free test gap; check the ref |
| Reading upstream over the network when a fetched ref exists | Check `refs/remotes/upstream/main` first |
| Conflating "no NVIDIA GPU" with "no accelerator" | Probe every vendor; an unreachable accelerator is Tier B, not C |
| Dropping `low priority` silently | Report the count and the reason |
| Matching priority by substring | `intel priority` / `rocm priority` / `arm priority` are different labels |
| Hammering `gh search` / `gh api search/issues` | 30/min; secondary rate limits kill the sweep mid-way |
| Leaving the result in chat | A run with no report file did not finish; write `pytorch-triage-<target>-<date>.md` to the checkout root |
| Picking the report path from `~`, a relative path, or `$CHK` | The message must carry an absolute path; the session cwd is rarely the checkout |
| Falling back to the skill directory without checking | Validate with `git remote -v \| grep pytorch/pytorch`; a repo that is merely *at* the path does not qualify |
| Dropping the report under `/tmp` as a "scratch file" | Step 5's `/tmp` rule covers intermediates; the report has its own destination |
| Relocating the report silently after a denied write | The fallback is allowed; calling it the checkout copy is not. Mark the fallback line and name the intended path in chat |
| Writing nothing because escalation was unavailable | A delegated scope cannot approve anything. Write priority 2 and mark it; a missing report is not a safe outcome |
| An installed wheel read as a checkout | No `.git`, no `git grep`, no `$REF` — that is priority 2, and Step 5 facts must come from a real checkout |
| A complexity label with no evidence | `medium` alone is guesswork; append files/call sites touched |
| A `Still present at` cell holding a bare ref or `HEAD` | Pin the short SHA; without it the next run cannot re-derive the verdict |
| Replacing the four required fields with a bare 7-column table | Summary, modules, complexity and requirement are the deliverable — under a separate `### #NNNNN` heading is fine too |

## Red flags — stop and redo

- No report file was written, or the chat message does not carry its absolute path.
- The report was written to the skill directory while a `pytorch/pytorch` checkout was reachable, with no fallback line saying why.
- The report landed in `docs/`, a subdirectory, or a `/tmp` path.
- A row is missing summary, related module(s), complexity, or requirement.
- Complexity carries no evidence, or "requirement" names only the machine and not the contributor.
- A `Still present at` cell names no commit, only a ref or `HEAD`.
- A candidate row has an empty dup-evidence or staleness cell.
- The staleness verdict cites only the working tree.
- "Unclaimed" rests on assignees or link fields, with no keyword PR sweep.
- The report has no Tier B row even though a host accelerator probe answered.
- A label in a query was never checked against the live list.
- `low priority` rows appear in the table, or the dropped count is missing.
- Every candidate landed in one tier.
