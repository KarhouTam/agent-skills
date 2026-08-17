# CLAUDE.md

This file provides guidance to AI agents (Claude Code / Codex) working in this repository.

## Overview

This repo is a **harness-pluggable skill** that orchestrates refactoring PyTorch test files to decouple them from specific hardware accelerators (CUDA, MPS, XPU). Tests are split into three categories across an 8-phase workflow (7 refactoring + 1 CI automation) driven by AI agents.

The skill is not run standalone: it is invoked by the host harness (Claude Code or Codex). `SKILL.md` is the operational entry point; `flow.py` is the state machine core; the host spawns AI agents from the JSON task spec emitted by `orchestrator.py`, selecting the adapter via `--harness {claude,codex}`.

## Architecture

```
orchestrator.py  CLI bridge — emits JSON task specs, accepts feed-back; --harness selects the adapter; --ci-check / --ingest-feedback / --apply-ingest
flow.py          RefactorFlow state machine — core orchestrator, phases 1-7
ci_ops.py        CIOps state machine — CI monitoring & debug, phase 8
ingest_ops.py    IngestOps state machine — PR feedback ingest sidecar
├── state.py     Pydantic models for all workflow data (signals, reports, tasks, results, CI, ingest)
├── utils.py     Path constants, workspace helpers, refactoring rule definitions
├── scripts/     Deterministic steps (assess, verify, linter, report, ci, ingest, local_test, logger)
├── agent/       Harness adapters, AI agent prompt templates (prompts/), sub-skills (skills/)
└── reference/   Authoritative API classification (device_api_catalog.yaml, classification_guide.md)
```

The three state machines share one pattern: deterministic `scripts/*` steps do the mechanical work (analysis, verification, CI, harvesting); AI agents do the judgment (classification, coding, review, debugging). Each machine stops on a **flow signal** (`spawn_single` / `send_message` / `relay_findings` / `waiting` / `done`) and lets the selected harness adapter translate that signal into harness-specific actions.

### Harness adapter layer

Harness differences are isolated in `agent/`. `orchestrator.py` and the state machines never branch on the harness name — they delegate through the `BaseAdapter` interface only.

- `agent/adapter.py` — `BaseAdapter` interface + `AgentTask`; task builders and the harness-specific emission interface (`task_to_spec`/`ci_task_to_spec`/`ingest_task_to_spec`, `completion_note`, `ci_wait_on_complete`/`ci_done_next_steps`, `git_preflight`/`git_preflight_error`).
- `agent/claude_code.py` — `ClaudeCodeAdapter` (`harness_name="claude"`): `Agent`/`SendMessage`/`CronCreate`/`Write` semantics.
- `agent/codex.py` — `CodexAdapter` (`harness_name="codex"`): `spawn_agent`/`followup_task`/`wait_agent`, full-history forks, `sleep` poll instead of cron.
- `agent/registry.py` — `HARNESS_ADAPTERS` registry + `get_adapter(name)`. Adding a harness = one adapter module + one registry entry.

Harness selection (`--harness` or `PYTORCH_TEST_REFACTOR_HARNESS`, default `claude`) is resolved once via `get_adapter()` and injected into the state machines; `_cmd()` writes `--harness` into every `on_complete.command`/`poll_command` so cross-process resume re-selects the same harness.

## Core concepts

Tests are split into three strategies (full decision framework in `agent/skills/refactor-test-decoupling/SKILL.md`):

| Strategy | Mechanism | hw_classification | When |
|----------|-----------|-------------------|------|
| S1 | `@instantiate_parametrized_tests` or `TestCase` | `GENERIC` (or `CPU`) | No device dependency, pure CPU logic |
| S2 | `instantiate_device_type_tests()` | `ACCELERATOR` | Uses `device` param with generic accelerator APIs |
| S3 | `instantiate_device_type_tests(only_for="<device>")` | `CUDA`/`MPS`/`XPU` | Truly device-specific APIs (Category C) |

**Import:** `from torch.testing._internal.common_utils import HardwareClassification`. Class renaming is OPTIONAL — the future `hw_classification` member drives classification; rename only when external reference impact (DecorateInfo, dynamo skips) is low.

### Device API classification (first match wins)

- **Category A** — has `torch.accelerator.*` equivalent → S2
- **Category B** — cross-backend concept, no wrapper yet (Stream, Event) → S2
- **Category C** — truly device-specific (NCCL, NVTX, cuDNN, GDS, Jiterator, Metal shaders) → S3

The authoritative catalog is `reference/device_api_catalog.yaml` (lookup guide: `reference/classification_guide.md`).

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
python orchestrator.py <file> --harness <claude|codex> --resume     # resume after interruption
```

Each refactoring writes its workspace to `agent_space/refactor/{file_name}/` (assessment, reports, tasks, verification, findings, `final_summary.md`, audit/status/flow-state files); the ingest sidecar uses `agent_space/ingest/`.

## Changelog

Workflow evolution and evaluation history live in [CHANGELOG.md](CHANGELOG.md). Read the latest entries before modifying agent prompts or verification logic; update it (in Chinese) after new features, fixes, or workflow improvements.
