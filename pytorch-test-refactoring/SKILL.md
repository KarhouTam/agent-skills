---
name: pytorch-test-refactoring
description: Orchestrate PyTorch test file refactoring to decouple tests from specific hardware accelerators. Use this skill whenever the user asks to refactor, decouple, or reorganize a PyTorch test file for cross-accelerator compatibility, or when they ask to apply the test decoupling workflow to a specific test file. Triggers on phrases like "refactor test_ops.py", "decouple tests in test_file.py", "apply test decoupling to X", or "use pytorch-test-refactoring on X". Always invoke this skill before starting any test refactoring work that involves splitting tests into accelerator-unrelated, accelerator-agnostic, and accelerator-specific classes.
---

# PyTorch Test Refactoring

7-phase workflow driven by `RefactorFlow`. Claude Code is the AI runtime — the Flow returns `FlowSignal` values that tell you when to spawn agents.

## Setup

```python
import sys
from pathlib import Path
skill_dir = str(Path(__file__).parent)
if skill_dir not in sys.path:
    sys.path.insert(0, skill_dir)
from flow import RefactorFlow
```

## Workflow

1. **Assess** — deterministic: file size, class layout, coder count, line ranges
2. **Analyze** — `SPAWN_SINGLE`: spawn analyst agent to classify every test
3. **Distribute** — deterministic: create one task per applicable refactoring rule (strategy_1, strategy_2, strategy_3, cleanup); a single coder applies them sequentially
4. **Code** — per-rule loop with single coder: first rule spawns coder (`SPAWN_SINGLE`), subsequent rules sent via `SEND_MESSAGE`. After each rule: checker verifies → pass? next rule : fix → re-check.
5. **Verify** — deterministic: 7 checks (syntax, test count, class structure, DecorateInfo, external refs, stale patterns, imports)
6. **Review** — `SPAWN_SINGLE`: spawn checker agent (ALWAYS runs, mandatory quality gate)
7. **Finalize** — deterministic: generate summary report

## Usage Pattern

```python
flow = RefactorFlow()
state = flow.run("test/test_ops.py")

while state.signal.value != "done":
    tasks = flow.get_pending_tasks()
    if state.signal.value == "spawn_single":
        # Spawn 1 agent, wait for result
        result = ...  # agent output
        if state.current_phase == "analyze":
            flow.feed_analyst_result(result)
        elif state.current_phase == "code":
            if state.rule_sub_phase == "check":
                # Checker completed per-rule verification — parse pass/fail
                passed = _checker_passed(result)
                flow.feed_rule_check_result(passed)
            else:
                # Coder completed applying a rule
                flow.feed_coder_result("coder", result)
        elif state.current_phase == "review":
            flow.feed_review_findings(result)
    elif state.signal.value == "send_message":
        # Send follow-up to existing coder, wait for response
        tasks = flow.get_pending_tasks()
        for t in tasks:
            SendMessage(to=t.context["send_message_to"], message=t.prompt)
        # ... when coder responds ...
        if state.rule_sub_phase == "fix":
            flow.feed_rule_fix_result("coder", result)
        else:
            flow.feed_coder_result("coder", result)
    elif state.signal.value == "relay_findings":
        # Send review findings to coders for fixing
        tasks = flow.get_pending_tasks()
        for t in tasks:
            SendMessage(to=t.context["send_message_to"], message=t.prompt)
        # ... when coder responds with fixes ...
        flow.feed_fix_complete()
    state = flow.run(state.file_path)
```

`_checker_passed(result)` parses the checker agent's output: returns `True` if the checker reports no errors for the scoped rule, `False` otherwise.

## Flow Signals

| Signal | Action |
|--------|--------|
| `SPAWN_SINGLE` | Spawn 1 agent; dispatch on `current_phase` AND `rule_sub_phase`: analyze → `feed_analyst_result()`; code+code → `feed_coder_result()`; code+check → `feed_rule_check_result(passed)`; review → `feed_review_findings()` |
| `SEND_MESSAGE` | Send follow-up to existing coder agent via `SendMessage`; on response dispatch on `rule_sub_phase`: code → `feed_coder_result()`; fix → `feed_rule_fix_result()` |
| `RELAY_FINDINGS` | Send findings to coders via `SendMessage` (fix tasks from `get_pending_tasks()`), wait for fixes, then `feed_fix_complete()` |
| `DONE` | Continue to next phase |

## Key Rules (from agent/skills/refactor-test-decoupling)

- **KEEP blacklist skips**: `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta`, `@onlyNativeDeviceTypesAnd`
- **ENLARGE whitelist**: `@onlyCUDA` -> `@onlyAccelerator`, `@onlyOn` -> `@onlyAccelerator`
- **Class naming**: `TestFoo` (S1), `TestFooDevice` (S2), `TestFooCUDA` (S3)
- **Phase 6 is mandatory** — checker always reviews, even if verification passes
- **External refs after rename**: When classes are renamed, check `common_methods_invocations.py`, `test/dynamo_skips/`, and `test/dynamo_expected_failures/` for stale references to old class names

## Workspace

```
agent_space/refactor/{file_name}/
├── assessment.json
├── analyst_report.md / .json
├── coder_tasks.json
├── verification.json
├── review_findings.json
└── final_summary.md
```

## Related

- Methodology: `agent/skills/refactor-test-decoupling`
- Review: `agent/skills/review-test-refactoring`
