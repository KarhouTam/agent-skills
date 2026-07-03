# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repo is a **Claude Code skill** that orchestrates refactoring PyTorch test files to decouple them from specific hardware accelerators (CUDA, MPS, XPU). Tests are split into three categories across an 8-phase workflow (7 refactoring + 1 CI automation) driven by AI agents.

**This skill is invoked by Claude Code, not run standalone.** The SKILL.md entry point triggers the workflow; `flow.py` is the state machine core; Claude Code spawns AI agents when the flow emits `FlowSignal` values.

## Architecture

```
orchestrator.py (CLI bridge — JSON task spec ↔ Agent/SendMessage tool calls)
flow.py (RefactorFlow state machine — core orchestrator, phases 1-7)
ci_ops.py (CIOps state machine — CI monitoring & debug, phase 8)
├── state.py              Pydantic models for all workflow data (signals, reports, tasks, results, CI)
├── utils.py              Path constants, workspace helpers, refactoring rule definitions
├── scripts/
│   ├── assess.py         Phase 1: deterministic file analysis (class layout, test counts)
│   ├── verify.py         Phase 5: 7 deterministic verification checks
│   ├── report.py         Phase 7: final markdown summary generation
│   ├── ci.py             Phase 8: deterministic CI operations (check-runs, bot comments)
│   └── logger.py         Structured JSONL audit log + status.json snapshot
├── agent/
│   ├── adapter.py        Abstract BaseAdapter with AgentTask model
│   ├── claude_code.py    ClaudeCodeAdapter: builds AgentTask objects from prompt templates
│   ├── prompts/          Markdown prompt templates (analyst.md, coder.md, checker.md, debugger.md)
│   └── skills/           Sub-skills referenced by agents (classify-test-files, refactor-test-decoupling, review-test-refactoring, ci-automation)
└── reference/
    ├── device_api_catalog.yaml      Authoritative API classification (Category A/B/C)
    ├── classification_guide.md      How to look up API categories
    └── device_specific_features_report.md
```

### Dependency flow

```
flow.py → state.py (data models)
flow.py → utils.py (constants, workspace paths)
flow.py → scripts/assess.py (Phase 1)
flow.py → scripts/verify.py (Phase 5)
flow.py → scripts/report.py (Phase 7)
flow.py → scripts/logger.py (audit logging)
flow.py → agent/adapter.py (abstract base)
flow.py → agent/claude_code.py (Claude Code adapter)
ci_ops.py → state.py (data models)
ci_ops.py → scripts/ci.py (Phase 8 deterministic ops)
ci_ops.py → agent/adapter.py (abstract base)
ci_ops.py → agent/claude_code.py (Claude Code adapter)
```

## Core concepts

### Three strategies

| Strategy | Class naming | Mechanism | When |
|----------|-------------|-----------|------|
| S1 | `TestFoo` (original name) | `@instantiate_parametrized_tests` or `TestCase` | No device dependency, pure CPU logic |
| S2 | `TestFoo` or `TestFooDevice` | `instantiate_device_type_tests()` | Uses `device` parameter with generic accelerator APIs |
| S3 | `TestFoo` or `TestFoo<Device>` (e.g., `TestFooCUDA`) | `instantiate_device_type_tests(TestFooCUDA, globals(), only_for="cuda")` when using `@dtypes`/`@dtypesIfCUDA`; otherwise plain `TestCase` with `setUp` | Requires truly device-specific APIs (NCCL, cuDNN, etc.) |

**Class renaming is OPTIONAL.** The future `hw_classification` member on TestCase (not yet landed) will drive classification, so class names are not the primary discriminator. The agent decides whether to rename based on external reference impact: if the class has many DecorateInfo/dynamo_skip references, keep the original name to avoid breaking them. See `agent/skills/refactor-test-decoupling/SKILL.md` for the full decision framework.

**S3 instantiation preference**: Use `instantiate_device_type_tests(..., only_for="cuda")` whenever the class has `@dtypes`, `@dtypesIfCUDA`, `@dtypesIfCPU`, or `@parametrize` — these decorators rely on device-type injection from `instantiate_device_type_tests`. This also eliminates per-method `@onlyCUDA` (device is injected as a parameter). Do NOT use `@instantiate_parametrized_tests` for S3 — it cannot resolve device-type-aware decorators.

### Device API classification (first match wins)

- **Category A**: Has `torch.accelerator.*` equivalent → Strategy 2 (replace with accelerator API)
- **Category B**: Cross-backend concept, no wrapper yet (Stream, Event) → Strategy 2 (keep as-is)
- **Category C**: Truly device-specific (NCCL, NVTX, cuDNN, GDS, Jiterator, Metal shaders) → Strategy 3

### Seven phases

1. **Assess** — deterministic: file stats, class layout, coder count estimate
2. **Analyze** — AI agent (analyst): classify every test, identify stale imports, review skip decorators
3. **Distribute** — deterministic: convert strategy assignments into per-rule coder tasks
4. **Code + Check** — AI loop: coder applies one rule → checker verifies → next rule (single coder, per-rule iteration)
5. **Verify** — deterministic: 7 automated checks (syntax, test count, class structure, DecorateInfo alignment, external refs, stale patterns, import audit)
6. **Final Review** — AI agent (checker): mandatory full-file quality review; findings → coder fix → re-verify (max 3 retries)
7. **Finalize** — deterministic: generate `final_summary.md`
8. **CI Ops** — user creates PR manually, then triggers CI monitoring (via "look after the CI" or --ci-check). The state machine cron-monitors CI, classifies failures, spawns a debugger agent to fix regressions, pushes fixes, and marks the PR ready (see `agent/skills/ci-automation/SKILL.md`)

### FlowSignal mechanism

The state machine stops on these signals and expects Claude Code to handle them:

| Signal | When | Claude Code action |
|--------|------|-------------------|
| `SPAWN_SINGLE` | Need 1 new AI agent | Spawn analyst/coder/checker via Agent tool; **capture agent_id from result**; include `agent_id` + `agent_name` in the JSON piped to `--feed`; call `feed_*_result()` |
| `SEND_MESSAGE` | Follow-up to existing agent | `SendMessage(to=agent_id)` to resume the stopped agent (uses registered agent ID, not name); if agent unreachable → fallback spawn + register new ID; call `feed_coder_result()` |
| `RELAY_FINDINGS` | Review found issues | Forward findings to coder via `SendMessage(coder_agent_id)`; call `feed_fix_complete()` |
| `WAITING` | CI still running | Schedule durable cron via CronCreate; session exits |
| `DONE` | Phase complete | Call `flow.run()` to continue |

## Key rules (non-negotiable)

- **KEEP blacklist skips**: `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd` — these are intentional and must be preserved
- **ENLARGE whitelist**: `@onlyCUDA` → `@onlyAccelerator`; `@onlyOn(["cuda","xpu"])` → `@onlyAccelerator`; `@unittest.skipIf(not TEST_CUDA)` → `@onlyAccelerator`
- **`@onlyAccelerator` is a METHOD decorator only**, never a class decorator — using it on a class breaks `instantiate_device_type_tests`
- **"CUDA as device" vs "CUDA as feature"**: If replacing `"cuda"` with `"mps"`/`"xpu"` still makes logical sense, it's Strategy 2 (CUDA was just the device). If the test uses Category C APIs, it's Strategy 3.
- **Phase 6 is mandatory** — the checker always does a full-file review even if per-rule checks passed
- **Test count must be preserved** — verification check #2 fails on mismatch

## Workspace

Each refactoring creates `agent_space/refactor/{file_name}/`:

```
assessment.json       # Phase 1 output
analyst_report.md     # Phase 2 human-readable
analyst_report.json   # Phase 2 structured
coder_tasks.json      # Phase 3 task allocation
verification.json     # Phase 5 results
review_findings.json  # Phase 6 findings
final_summary.md      # Phase 7 report
audit.jsonl           # Append-only event log
status.json           # Current state snapshot (for team coordination)
flow_state.json       # Transient state-machine position (phase, rule_index, agent_ids, etc.)
ci_state.json         # CI automation state (PR number, branch, fix history, cron job ID)
```

## Usage pattern

```python
import sys
from pathlib import Path
skill_dir = str(Path(__file__).parent)
if skill_dir not in sys.path:
    sys.path.insert(0, skill_dir)
from flow import RefactorFlow

flow = RefactorFlow()
state = flow.run("test/test_ops.py")

while state.signal.value != "done":
    tasks = flow.get_pending_tasks()
    if state.signal.value == "spawn_single":
        agent_id, result = ...  # spawn agent via Agent tool, capture agent_id
        flow.feed_agent_spawned(task.agent_name, agent_id)  # register ID for future SendMessage
        if state.current_phase == "analyze":
            flow.feed_analyst_result(result)
        elif state.current_phase == "code":
            flow.feed_coder_result("coder", result)
        elif state.current_phase == "review":
            flow.feed_review_findings(result)
    elif state.signal.value == "send_message":
        result = ...  # SendMessage(to=task.agent_id), or fallback-spawn + capture new ID
        flow.feed_coder_result("coder", result)
    elif state.signal.value == "relay_findings":
        send_findings_via_SendMessage(coder_agent_id)
        flow.feed_fix_complete()
    state = flow.run(state.file_path)
```

Cross-process resume: `flow.run("test_ops.py", resume=True)` loads artifacts from workspace.
