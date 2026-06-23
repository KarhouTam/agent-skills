---
name: pytorch-test-refactoring
description: Orchestrate PyTorch test file refactoring to decouple tests from specific hardware accelerators. Use this skill whenever the user asks to refactor, decouple, or reorganize a PyTorch test file for cross-accelerator compatibility, or when they ask to apply the test decoupling workflow to a specific test file. Triggers on phrases like "refactor test_ops.py", "decouple tests in test_file.py", "apply test decoupling to X", or "use pytorch-test-refactoring on X". Always invoke this skill before starting any test refactoring work that involves splitting tests into accelerator-unrelated, accelerator-agnostic, and accelerator-specific classes.
---

# PyTorch Test Refactoring

7-phase workflow driven by `flow.py` state machine + `orchestrator.py` deterministic wrapper.

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
     ┌───────────────┐
     │ Read JSON     │
     │ status=?      │
     └───┬───────┬───┘
         │       │
   "need_agent"  "done" → workflow complete ✓
         │
         ▼
     ┌────────────────────────────────┐
     │ For each task in tasks[]:      │
     │  method=spawn → Agent tool     │
     │    → capture agent_id from     │
     │      Agent tool result         │
     │  method=send_message →         │
     │    SendMessage(to=send_to);    │
     │    if agent dead/unreachable   │
     │    → use fallback.spawn        │
     │      → capture new agent_id    │
     └────────────┬───────────────────┘
                  │ agent completes
                  ▼
     ┌────────────────────────────────┐
     │ Read agent output              │
     │ Extract result → JSON (below)  │
     │ Include agent_id + agent_name  │
     │   if agent was spawned/new     │
     │ Pipe to on_complete.command    │
     └────────────┬───────────────────┘
                  │
                  ▼
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

**Important: When you spawn a new agent (method=spawn),** capture the `agent_id` from the Agent tool result and include it (along with `agent_name`) in the result JSON you pipe back. This is how the orchestrator learns the agent's identity for future `SendMessage` calls. Without it, `SendMessage` will fail because it needs an agent ID, not a name.

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

## Key Rules (non-negotiable, from agent/skills/refactor-test-decoupling)

- **KEEP blacklist skips**: `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd`
- **ENLARGE whitelist**: `@onlyCUDA` → `@onlyAccelerator`, `@onlyOn` → `@onlyAccelerator`
- **Class naming**: `TestFoo` (S1), `TestFooDevice` (S2), `TestFooCUDA` (S3)
- **Phase 6 is mandatory** — checker always reviews, even if verification passes
- **External refs after rename**: When classes are renamed, update `common_methods_invocations.py` DecorateInfo entries, and rename stale entries in `test/dynamo_skips/` and `test/dynamo_expected_failures/`. **CRITICAL: dynamo skip/expected-failure files are sentinels (often 0 bytes). Search by FILENAME (`find -name`), NEVER by content (`grep`).** When a device-parametrized class is renamed (TestFoo → TestFooDevice), `instantiate_device_type_tests` renames device variants too (TestFooCUDA → TestFooDeviceCUDA). Files named after old variants must be renamed to match

### Three strategies

| Strategy | Class naming | Mechanism | When |
|----------|-------------|-----------|------|
| S1 | `TestFoo` (original name) | `@instantiate_parametrized_tests` or `TestCase` | No device dependency, pure CPU logic |
| S2 | `TestFooDevice` | `instantiate_device_type_tests()` | Uses `device` parameter with generic accelerator APIs |
| S3 | `TestFooCUDA` | `@instantiate_parametrized_tests` or `TestCase` | Requires truly device-specific APIs (NCCL, cuDNN, etc.) |

## Related

- Methodology: `agent/skills/refactor-test-decoupling`
- Review: `agent/skills/review-test-refactoring`
- State machine: `flow.py`
- Data models: `state.py`
