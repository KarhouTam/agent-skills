---
name: pytorch-test-refactoring
description: Orchestrate PyTorch test file refactoring to decouple tests from specific hardware accelerators. Use this skill whenever the user asks to refactor, decouple, or reorganize a PyTorch test file for cross-accelerator compatibility, or when they ask to apply the test decoupling workflow to a specific test file. Triggers on phrases like "refactor test_ops.py", "decouple tests in test_file.py", "apply test decoupling to X", or "use pytorch-test-refactoring on X". Also triggers on CI monitoring phrases like "look after the ci", "monitor ci", "check ci status", "watch the pr checks", or "debug ci failures". Always invoke this skill before starting any test refactoring work that involves splitting tests into accelerator-unrelated, accelerator-agnostic, and accelerator-specific classes.
---

# PyTorch Test Refactoring

8-phase workflow (7 refactoring + 1 CI automation) driven by `flow.py` + `ci_ops.py` state machines + `orchestrator.py` deterministic wrapper.

## Usage (one command)

```bash
python /root/.claude/skills/pytorch-test-refactoring/orchestrator.py <test_file_path>
```

The orchestrator outputs a JSON task spec to stdout. Follow this loop:

```
┌─────────────────────────────────────────┐
│ python orchestrator.py test/test_ops.py │
└────────────┬────────────────────────────┘
             │ JSON on stdout
             ▼
     ┌─────────────────┐
     │ Read JSON       │
     │ status=?        │
     └───┬───────┬─────┘
         │       │
         │       ├── "done" + phase="ci_done"
         │       │   → gh pr ready, CronDelete, truly done ✓
         │       │
         │       ├── "done" + phase: "finalize"
         │       │   → Refactoring complete. User creates
         │       │     PR manually, then says "look after
         │       │     the CI" or runs --ci-check
         │       │
         │       ├── "schedule_cron"
         │       │   → CronCreate(on_complete.cron_interval,
         │       │     durable=true, prompt=on_complete.prompt)
         │       │   → Session exits. Next firing resumes.
         │       │
         │       └── "need_agent"
         │            │
         │            ▼
         │    ┌────────────────────────────────┐
         │    │ For each task in tasks[]:      │
         │    │  method=spawn → Agent tool     │
         │    │    → capture agent_id from     │
         │    │      Agent tool result         │
         │    │  method=send_message →         │
         │    │    SendMessage(to=send_to);    │
         │    │    if agent dead/unreachable   │
         │    │    → use fallback.spawn        │
         │    │      → capture new agent_id    │
         │    └────────────┬───────────────────┘
         │                 │ agent completes
         │                 ▼
         │    ┌────────────────────────────────┐
         │    │ Read agent output              │
         │    │ Extract result → JSON (below)  │
         │    │ Include agent_id + agent_name  │
         │    │   if agent was spawned/new     │
         │    │ Pipe to on_complete.command    │
         │    └────────────┬───────────────────┘
         │                 │
         ▼                 ▼
     (loop back to top)
```

## Result JSON Formats

After each agent completes, extract the key result and pipe JSON to the `on_complete.command`.

**All result JSON objects may include these optional fields:**
- `agent_id`: The agent ID returned by the `Agent` tool when spawning. **Required after any spawn** — this is how the orchestrator learns the agent's ID for future `SendMessage` calls.
- `agent_name`: The `agent_name` from the task spec (e.g. `"coder"`, `"checker"`, `"analyst"`). Include alongside `agent_id`.

### Coder (`--feed coder`)

```json
{
  "agent_id": "a3fa28753cd227df1",
  "agent_name": "coder",
  "success": true,
  "tests_moved": ["test_foo:TestOld -> TestNewDevice"],
  "errors": [],
  "warnings": []
}
```

- `agent_id` / `agent_name`: **Include after spawn** so the orchestrator can target this agent for future `SendMessage` calls
- `success`: did the coder apply the rule without errors?
- `tests_moved`: list of "test_name: OldClass -> NewClass"
- `errors`: any error messages (empty if success)
- Parse the coder's output: look for "error"/"success" indicators, test movement summary

### Checker — per-rule (`--feed checker` when phase=code)

```json
{
  "passed": true
}
```

- `passed`: did the checker report no issues for this specific rule?
- Look for "PASS" / "no issues" vs "FAIL" / "issues found" in the checker output

### Checker — full review (`--feed checker` when phase=review)

```json
{
  "agent_id": "b7c20184ae3921e0",
  "agent_name": "checker",
  "all_clear": false,
  "findings": [
    {
      "severity": "major",
      "category": "classification",
      "description": "TestFoo still has @onlyCUDA",
      "line_number": 42,
      "coder_responsible": "coder"
    }
  ],
  "summary": "Found 2 issues"
}
```

- `all_clear`: true if no issues found
- `findings`: list of issues (empty if all_clear)
- Parse the structured report from the checker's output

### Analyst (`--feed analyst`)

The analyst writes `analyst_report.json` to the workspace automatically. The orchestrator loads it from disk. If the analyst fails, pass an empty object `{}` and the orchestrator will attempt fallback.

## What You NEVER Need to Do

The orchestrator handles all of this automatically:
- ❌ Decide which phase comes next
- ❌ Check `rule_sub_phase` to pick the right `feed_*` method
- ❌ Know whether to spawn or send_message
- ❌ Build agent prompts (they're in the task spec)
- ❌ Decide whether a checker is per-rule or full-file
- ❌ Loop through rules manually

Your ONLY job: run the command → follow the JSON → extract result → pipe JSON back.

**Before spawning the analyst**: quickly skim the test file (first ~50 lines and a few test methods). Note whether the original file is a plain `TestCase` with no device parametrization and whether tests primarily exercise utility/library logic (`rnn_utils`, `nn.functional` helpers, padding/packing utilities, etc.). If so, expect most tests to be S1. When the analyst report arrives, question any bulk S2 classification — a test that merely creates tensors is not automatically S2.

**When you see `"done"` with `phase: "finalize"`, the refactoring is complete.** The `next_steps` field tells you how to proceed: create a PR manually (branch, commit, push, draft PR), then say **"look after the CI"** or run `python orchestrator.py <file> --ci-check` to start CI monitoring. Only when you see `"done"` with `phase: "ci_done"` is the entire workflow truly finished — the `next_steps` field will tell you to mark the PR ready for review and delete cron jobs.

**Important: When you spawn a new agent (method=spawn),** capture the `agent_id` from the Agent tool result and include it (along with `agent_name`) in the result JSON you pipe back. This is how the orchestrator learns the agent's identity for future `SendMessage` calls. Without it, `SendMessage` will fail because it needs an agent ID, not a name.

## CI Automation (Phase 8)

After refactoring completes, the user creates a PR manually. To start CI monitoring, say **"look after the CI"** or run:

```bash
python orchestrator.py <test_file_path> --ci-check [--pr-number N]
```

The orchestrator auto-detects the PR from the current branch. Pass `--pr-number` to skip detection.

| Status | `phase` | What to do |
|--------|---------|------------|
| `"done"` | `"ci_done"` | All CI green. Run `gh pr ready <N>`, then `CronDelete` all CI cron jobs. Truly finished. |
| `"schedule_cron"` | `"ci_monitor"` | CI still running. `CronCreate(cron_interval, durable=true, prompt=...)` using the `on_complete` fields. Session exits. |
| `"need_agent"` | `"ci_debug"` | CI failures found. Spawn debugger agent (background, `mode: bypassPermissions`). When done, pipe result back via `--feed debugger`. Loop. |

The debugger agent's result format and the full CI ops reference: `agent/skills/ci-automation/SKILL.md`.

## Workspace

```
agent_space/refactor/{file_name}/
├── assessment.json
├── analyst_report.md / .json
├── coder_tasks.json
├── verification.json
├── review_findings.json
├── final_summary.md
├── audit.jsonl
├── status.json
└── flow_state.json
```

## Resuming After Interruption

```bash
python orchestrator.py test/test_ops.py --resume
```

The orchestrator loads all artifacts from the workspace and continues from where it left off.

## Workflow Phases (reference)

1. **Assess** — deterministic: file size, class layout, coder count, line ranges
2. **Analyze** — AI agent (analyst): classify every test, identify stale imports, review skip decorators
3. **Distribute** — deterministic: convert strategy assignments into per-rule coder tasks
4. **Code + Check** — AI loop: coder applies one rule → checker verifies → next rule (single coder, per-rule iteration, max 3 fix retries)
5. **Verify** — deterministic: 7 automated checks (syntax, test count, class structure, DecorateInfo alignment, external refs, stale patterns, import audit)
6. **Final Review** — AI agent (checker): **mandatory** full-file quality review; findings → coder fix → re-verify (max 3 retries)
7. **Finalize** — deterministic: generate `final_summary.md`
8. **CI Ops** — user creates PR manually, then triggers CI monitoring (via "look after the CI" or `--ci-check`). The state machine cron-monitors CI, classifies failures, spawns a debugger agent to fix regressions, pushes fixes, and marks the PR ready. See CI Automation section above.

## Key Rules (non-negotiable, from agent/skills/refactor-test-decoupling)

- **KEEP blacklist skips**: `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd`
- **ENLARGE whitelist**: `@onlyCUDA` → `@onlyAccelerator`, `@onlyOn` → `@onlyAccelerator`
- **Class naming**: Renaming is OPTIONAL (the future `hw_classification` member handles classification). Recommended names if renaming: `TestFoo` (S1), `TestFooDevice` (S2), `TestFooCUDA` (S3). Agent decides based on external reference impact.
- **Phase 6 is mandatory** — checker always reviews, even if verification passes
- **External refs after rename**: When classes are renamed, update `common_methods_invocations.py` DecorateInfo entries, and rename stale entries in `test/dynamo_skips/` and `test/dynamo_expected_failures/`. **CRITICAL: dynamo skip/expected-failure files are sentinels (often 0 bytes). Search by FILENAME (`find -name`), NEVER by content (`grep`).** When a device-parametrized class is renamed (TestFoo → TestFooDevice), `instantiate_device_type_tests` renames device variants too (TestFooCUDA → TestFooDeviceCUDA). Files named after old variants must be renamed to match

### Three strategies

| Strategy | Class naming | Mechanism | When |
|----------|-------------|-----------|------|
| S1 | `TestFoo` (original name) | `@instantiate_parametrized_tests` or `TestCase` | No device dependency, pure CPU logic. **Also includes tests of device-agnostic utility logic (`rnn_utils`, `pad_sequence`, `pack_sequence`, `F.pad`, etc.) where running on multiple devices adds no meaningful coverage.** This is the DEFAULT for tests that were previously CPU-only with no device decorators. |
| S2 | `TestFoo` or `TestFooDevice` | `instantiate_device_type_tests()` | Uses `device` parameter AND running on multiple devices provides specific testing value: device transfer semantics, memory format behavior, accelerator-specific error paths, or tests originally gated behind `torch.cuda.is_available()`. **Do NOT classify as S2 just because a test creates tensors.** |
| S3 | `TestFoo` or `TestFooCUDA` | `instantiate_device_type_tests(TestFooCUDA, globals(), only_for="cuda")` when using `@dtypes`/`@dtypesIfCUDA`/`@dtypesIfCPU`; otherwise `TestCase` with `setUp` guard and `@instantiate_parametrized_tests` | Requires truly device-specific APIs (NCCL, cuDNN, etc.) |

**S3 instantiation rule**: When an S3 class uses `@dtypes`, `@dtypesIfCUDA`, `@dtypesIfCPU`, or other device-type-aware decorators (which are designed for `instantiate_device_type_tests`), use `instantiate_device_type_tests(TestFooCUDA, globals(), only_for="cuda")` instead of `@instantiate_parametrized_tests`. Each test method receives `device` as its first parameter (always `"cuda"`), eliminating the need for per-method `@onlyCUDA` decorators or hardcoded `device = "cuda"` lines. This is the preferred pattern — it keeps mechanism consistency with S2 and lets `instantiate_device_type_tests` inject device-aware dtype resolution.

## Related

- Methodology: `agent/skills/refactor-test-decoupling`
- Review: `agent/skills/review-test-refactoring`
- State machine: `flow.py`
- Data models: `state.py`
