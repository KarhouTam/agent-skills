# CLAUDE.md

This file provides guidance to AI agents (Claude Code / Codex) working in this repository.

## Overview

This repo is a **harness-pluggable skill** that orchestrates refactoring PyTorch test files to decouple them from specific hardware accelerators (CUDA, MPS, XPU). Tests are split into three categories across an 8-phase workflow (7 refactoring + 1 CI automation) driven by AI agents.

The skill is not run standalone: it is invoked by the host harness (Claude Code or Codex). `SKILL.md` is the operational entry point; `flow.py` is the state machine core; the host spawns AI agents from the JSON task spec emitted by `orchestrator.py`, selecting the harness via `--harness {claude,codex}`.

## Architecture

```
orchestrator.py  CLI bridge — emits JSON task specs, accepts feed-back; --harness selects the harness; --ci-check / --ingest-feedback / --apply-ingest / --review-queue
flow.py          RefactorFlow state machine — core orchestrator, phases 1-7
ci_ops.py        CIOps state machine — CI monitoring & debug, phase 8
ingest_ops.py    IngestOps state machine — PR feedback ingest sidecar
review_ops.py    ReviewOps state machine — daily PR review queue sidecar
├── state.py     Pydantic models for all workflow data (signals, reports, tasks, results, CI, ingest)
├── utils.py     Path constants, workspace helpers, refactoring rule definitions
├── scripts/     Deterministic steps (assess, verify, linter, report, ci, ingest, local_test, review_queue, logger)
├── agent/       Shared task builders (tasks.py), harness plugins (harnesses/), AI prompt templates (prompts/), sub-skills (skills/)
└── reference/   Core reference base (default); non-core fields live under reference/<field>/
```

The state machines share one pattern: deterministic `scripts/*` steps do the mechanical work (analysis, verification, CI, harvesting, queue selection/publishing); AI agents do the judgment (classification, coding, review, debugging, PR reviewing). The review-queue sidecar is harness-dependent for its review phase — driven by the harness's `supports_delegated_agents` capability, not its name: Claude Code uses one reviewer sub-agent per PR, while Codex reviews inline in the executor because Codex MultiAgentV2 mis-delivers `spawn_agent` task messages (openai/codex#25458). Each machine stops on a **flow signal** (`spawn_single` / `spawn_parallel` / `send_message` / `relay_findings` / `waiting` / `done`) and lets the selected harness translate that signal into harness-specific actions.

### Harness plugin layer

Harness differences are isolated in `agent/`. `orchestrator.py` is the only component that touches a harness (to emit task specs and read policy); the state machines are entirely harness-free — they build `AgentTask`s through the shared builders in `agent/tasks.py` and never see a harness.

- `agent/tasks.py` — harness-agnostic `AgentTask` builders + prompt/field helpers, shared by every harness.
- `agent/harness.py` — the slimmed `AgentTask` model (task content only) + the `Harness` protocol (`spawn`/`followup` emission, `note`/`wait_on_complete`/`done_next_steps`/`git_preflight*` policy, `supports_delegated_agents` capability).
- `agent/harnesses/claude.py` — `ClaudeHarness`: `Agent`/`SendMessage`/`CronCreate`/`Write` semantics, per-role permission mode, settings.json git preflight.
- `agent/harnesses/codex.py` — `CodexHarness`: `spawn_agent`/`followup_task`/`wait_agent`, full-history forks, poll instead of cron, inline (non-delegated) AI steps.
- `agent/harnesses/__init__.py` — `HARNESSES` registry + `get_harness(name)`. Adding a harness = one module + one registry entry.

Harness selection (`--harness` or `PYTORCH_TEST_REFACTOR_HARNESS`, default `claude`) is resolved once via `get_harness()`; the `--harness` choices derive from the registry, and `_cmd()` writes `--harness <name>` into every `on_complete.command`/`poll_command` so cross-process resume re-selects the same harness.

## Core concepts

Tests are split into three strategies (full decision framework in `agent/skills/refactor-test-decoupling/SKILL.md`):

| Strategy | Mechanism                                            | hw_classification    | When                                              |
| -------- | ---------------------------------------------------- | -------------------- | ------------------------------------------------- |
| CPU-only       | `@instantiate_parametrized_tests` or `TestCase`      | `GENERIC` (or `CPU`) | No device dependency, pure CPU logic              |
| device-agnostic       | `instantiate_device_type_tests()`                    | `ACCELERATOR`        | Uses `device` param with generic accelerator APIs |
| device-specific       | `instantiate_device_type_tests(only_for="<device>")` | `CUDA`/`MPS`/`XPU`   | Truly device-specific APIs (Category C)           |

**Import:** `from torch.testing._internal.common_utils import HardwareClassification`. Class renaming is OPTIONAL — the future `hw_classification` member drives classification; rename only when external reference impact (DecorateInfo, dynamo skips) is low.

### Device API classification (first match wins)

- **Category A** — has `torch.accelerator.*` equivalent → device-agnostic
- **Category B** — cross-backend concept, no wrapper yet (Stream, Event) → device-agnostic
- **Category C** — truly device-specific (NCCL, NVTX, cuDNN, GDS, Jiterator, Metal shaders) → device-specific

The authoritative catalog is `reference/device_api_catalog.yaml` (lookup guide: `reference/classification_guide.md`).

### Test fields

Files resolve to `core` (default), `distributed`, or `graph` by exact
membership in `reference/distributed/test_list.txt` or
`reference/graph/test_list.txt`. Ambiguous membership is an error.

`core` keeps the historical root reference directory and full CPU-only/device-agnostic/device-specific
workflow. Non-core fields use `reference/<field>/`, fall back to core
references for missing content, and currently run a field-agnostic baseline:
cleanup only, generic verification/review, and no local-test gate.

### Phases

1. **Assess** — deterministic file analysis (stats, class layout, coder count)
2. **Analyze** — AI analyst classifies every test, flags stale imports/skips
3. **Distribute** — deterministic: strategy assignments → per-rule coder tasks
4. **Code + Check** — AI loop: coder applies one rule → checker verifies → next rule
5. **Verify** — deterministic checks (syntax, test count, class structure, refs, lint hard gate)
6. **Final Review** — AI checker full-file review (mandatory) → findings → fix
6.5 **Local test gate** — run the refactored file, relay failures to coder (fix-or-defer)
7. **Finalize** — generate `final_summary.md`
8. **CI Ops** — cron-monitor CI, debugger fixes regressions, marks PR ready

## Usage

The operational loop, JSON feed formats, CI automation, and feedback ingest are documented in `SKILL.md`. Entry points:

```bash
python orchestrator.py <file> --harness <claude|codex>              # refactor a test file (phases 1-7)
python orchestrator.py <file> --harness <claude|codex> --ci-check   # CI monitoring (phase 8)
python orchestrator.py --harness <claude|codex> --ingest-feedback   # harvest PR feedback (sidecar)
python orchestrator.py --harness <claude|codex> --review-queue      # daily PR review queue (sidecar)
python orchestrator.py <file> --harness <claude|codex> --resume     # resume after interruption
```

Each refactoring writes its workspace to `agent_space/refactor/{field}/{file_name}/` (assessment, reports, tasks, verification, findings, `final_summary.md`, audit/status/flow-state files); the ingest sidecar uses `agent_space/ingest/`; the review-queue sidecar uses `agent_space/pr_reviews/` and consumes `agent_space/pr_needs_review.txt`.

## Changelog

Workflow evolution and evaluation history live in [CHANGELOG.md](CHANGELOG.md). Read the latest entries before modifying agent prompts or verification logic; update it (in Chinese) after new features, fixes, or workflow improvements.
