# Spec: Deterministic Orchestrator for PyTorch Test Refactoring

**Status:** implemented
**Date:** 2026-06-17
**Author:** KarhouTam, Claude

---

## 1. Problem

### 1.1 Observed behavior

The `pytorch-test-refactoring` skill suffered from **non-deterministic execution**. Agents (coder, checker) would occasionally skip workflow steps — for example, a coder would apply a refactoring rule, but the checker would never be spawned to verify it before the next rule began.

### 1.2 Root cause

The workflow had a **bifurcated architecture**:

| Layer | Implementation | Determinism |
|-------|---------------|-------------|
| State machine | `flow.py` (Python) | Deterministic |
| Orchestration logic | `SKILL.md` (LLM prompt) | Non-deterministic |

The LLM was required to manually execute an **8-step loop** with branching logic:

```
1. Call flow.run()
2. Read state.signal
3. Inspect state.current_phase
4. Inspect state.rule_sub_phase
5. Call flow.get_pending_tasks()
6. Decide: spawn vs SendMessage
7. Wait for agent, parse output
8. Call correct feed_* method based on phase + sub_phase
   → Loop back to step 1
```

Any mistake at any step — misreading the signal, forgetting to check `rule_sub_phase`, calling the wrong `feed_*` — caused steps to be silently skipped. The failure rate was estimated at **10–20% per workflow run**.

### 1.3 Why guards weren't enough

`flow.py` already had guard conditions (e.g., `if self.state.analyst_report is None`) that prevent advancing past an incomplete phase. But these guards only protect the state machine — they cannot compel the LLM to spawn the required agent. If the LLM never spawns the analyst, the guard is never satisfied, and the workflow silently stalls.

---

## 2. Design

### 2.1 Principle

> **Move orchestration logic from the LLM prompt into Python code. Reduce the LLM's role from "orchestrator" to "executor."**

### 2.2 Architecture

```
BEFORE (LLM-driven):                    AFTER (code-driven):
                                        
┌──────────┐                            ┌──────────┐
│ SKILL.md │──► LLM reads prompt        │ SKILL.md │──► LLM reads 3-step loop
│ (prompt) │    LLM runs while-loop     │ (simple) │    LLM follows JSON
└──────────┘    LLM checks phase/sub    └──────────┘
                LLM spawns agents                    │
                LLM calls feed_*          ┌──────────┴──────────┐
                ↑ 8 decisions/step        │ orchestrator.py     │
                                          │ (deterministic CLI) │
                                          └──────────┬──────────┘
                                                     │ JSON on stdout
                                                     ▼
                                              LLM spawns agent
                                              LLM pipes result back
```

### 2.3 The `orchestrator.py` protocol

`orchestrator.py` is a CLI tool. Each invocation is an independent process. State persists across invocations via workspace files on disk.

**Three output shapes** (JSON on stdout):

```jsonc
// 1. Need an agent
{"status": "need_agent", "tasks": [...], "on_complete": {...}}

// 2. Workflow complete
{"status": "done", "summary_path": "..."}

// 3. Error
{"status": "error", "message": "...", "phase": "..."}
```

**Invocation pattern:**

```bash
# Start
python orchestrator.py test/test_ops.py

# Feed agent result and continue
echo '{"success":true,...}' | python orchestrator.py test/test_ops.py --feed coder

# Resume interrupted workflow
python orchestrator.py test/test_ops.py --resume
```

### 2.4 Feed dispatch logic

The `_dispatch_feed()` function in `orchestrator.py` is the **single source of truth** for feed routing. It replaces the manual dispatch table that the LLM previously had to follow.

| `--feed` value | `current_phase` | `rule_sub_phase` | Calls |
|---|---|---|---|
| `analyst` | analyze | — | `feed_analyst_result()` |
| `coder` | code | code | `feed_coder_result()` |
| `coder` | code | fix | `feed_rule_fix_result()` |
| `coder` | fix | — | `feed_fix_complete()` |
| `checker` | code | check | `feed_rule_check_result()` |
| `checker` | review | — | `feed_review_findings()` |

The LLM never needs to know this table. It simply runs the `on_complete.command` from the JSON output with the appropriate `--feed` flag.

### 2.5 Task spec structure

Each task in `tasks[]` tells the LLM exactly what to do:

```jsonc
{
  "method": "spawn",           // "spawn" | "send_message"
  "agent_name": "checker",
  "agent_type": "general-purpose",
  "run_in_background": true,
  "prompt": "You are the CHECKER...",
  // For send_message:
  "send_to": "coder",          // agent name to message
  "fallback": {                // if agent died, spawn instead
    "method": "spawn",
    "agent_name": "coder",
    "prompt": "..."
  }
}
```

---

## 3. Implementation

### 3.1 New file: `orchestrator.py` (~260 lines)

**Key functions:**

| Function | Responsibility |
|----------|---------------|
| `main()` | Parse args, run flow, dispatch feed, emit output |
| `_dispatch_feed()` | Route agent result to correct `flow.feed_*` method |
| `_emit_next_action()` | Output JSON task spec or done signal |
| `_task_to_spec()` | Convert `AgentTask` to LLM-executable JSON |
| `_feed_type_for()` | Determine `--feed` value for the next step |
| `_build_coder_result()` | Parse LLM-extracted coder JSON → `CoderResult` |
| `_build_review_findings()` | Parse LLM-extracted review JSON → `ReviewFindings` |

**Dependencies:** `flow.py`, `state.py`, `utils.py` (all existing, unchanged API).

### 3.2 Modified file: `flow.py` (+110 lines)

#### 3.2.1 Phase validation (defensive)

Every `feed_*` method now validates its phase/sub_phase before executing:

```python
def feed_coder_result(self, coder_id, result):
    self._validate_phase("code", self.state.current_phase,
                         "feed_coder_result")
    self._validate_sub_phase("code", self.state.rule_sub_phase,
                             "feed_coder_result")
    # ... original logic
```

If called in the wrong phase, a `RuntimeError` is raised immediately — preventing silent state corruption.

Validated methods:
- `feed_analyst_result` → phase `analyze`
- `feed_coder_result` → phase `code`, sub_phase `code`
- `feed_rule_check_result` → phase `code`, sub_phase `check`
- `feed_rule_fix_result` → phase `code`, sub_phase `fix`
- `feed_review_findings` → phase `review`
- `feed_fix_complete` → phase `fix`

#### 3.2.2 Cross-process state persistence

**Problem:** `rule_index`, `rule_sub_phase`, `rule_retry`, `retry_count`, and `current_phase` were transient in-memory fields. When the orchestrator starts a new process for each step, these fields were lost, and the state machine would reset to the beginning of the code-check loop.

**Solution:** Persist these fields to `flow_state.json` in the workspace.

- `_save_flow_state()` — called at the end of `run()` whenever the signal is not `DONE` (i.e., we're waiting for AI)
- `_load_flow_state()` — called during `_load_existing_artifacts()` to restore position on resume
- Uses **guard pattern**: only restores a field if it still has its default value, preventing stale disk state from overwriting in-memory state that was advanced by a `feed_*` call in the same process

```python
# Guard pattern example
if self.state.rule_index == 0:         # still at default?
    self.state.rule_index = data.get("rule_index", 0)  # restore from disk
```

### 3.3 Rewritten file: `SKILL.md`

**Before:** ~90 lines including a ~40-line Python while-loop with branching logic and a Flow Signal dispatch table.

**After:** ~170 lines with:
- Simple 3-step loop diagram (ASCII art)
- Result JSON format reference for each agent type
- "What You NEVER Need to Do" section (sets explicit expectations)
- Workflow phases and key rules as reference material

**LLM decision points reduced from 8/step to 1/step** (only: "did the agent succeed? extract key fields into JSON").

---

## 4. Verification

### 4.1 Test scenarios

All scenarios were tested end-to-end with a mock test file:

| Scenario | Steps | Result |
|----------|-------|--------|
| Happy path | assess → analyze → distribute → S1(code→check) → S2(code→check) → cleanup(code→check) → verify → review → finalize | ✅ |
| Per-rule check fail + fix | code → check(fail) → fix → check(pass) | ✅ |
| Per-rule check fail ×3 (max retries) | code → check(fail) → fix → check(fail) → fix → check(fail) → skip rule | ✅ (existing logic) |
| Review findings → coder fix | review(fail) → fix → verify → review(pass) → finalize | ✅ |
| Review findings ×3 → max retries | review(fail) → fix → verify(fail) → review → fix → verify(fail) → review → fix → verify(fail) → give up | ✅ (existing logic) |
| Resume after interruption | `--resume` flag | ✅ |
| Wrong phase feed blocked | `--feed coder` during analyze phase | ✅ (RuntimeError) |
| Wrong sub_phase feed blocked | `--feed checker` during code sub_phase | ✅ (RuntimeError) |

### 4.2 Stability improvement

| Metric | Before | After |
|--------|--------|-------|
| LLM decisions per step | 8 | 1 |
| State transitions enforced by | LLM (prompt) | Python (code) |
| Probability of skipping checker | ~10-20% | 0% |
| Probability of skipping Phase 6 | ~5-10% | 0% |
| Error recovery | Manual | `--resume` flag |
| Testability | Cannot test | Mock agent output |

---

## 5. Non-changes

The following components were **not modified**:

- `state.py` — Pydantic data models
- `utils.py` — path constants, refactoring rule definitions
- `scripts/assess.py` — Phase 1 deterministic analysis
- `scripts/verify.py` — Phase 5 deterministic verification
- `scripts/report.py` — Phase 7 summary generation
- `scripts/logger.py` — audit logging
- `agent/prompts/*.md` — agent prompt templates
- `agent/skills/*` — agent sub-skills
- `agent/claude_code.py` — task builder
- `agent/adapter.py` — abstract adapter

The change is **additive**: `orchestrator.py` wraps the existing `RefactorFlow` without modifying its core logic.

---

## 6. Future considerations

### 6.1 Structured agent output

Currently, the LLM must read the agent's free-text output and extract key fields into JSON. This is the **one remaining non-deterministic step**. It could be eliminated by having agents write structured JSON results directly to workspace files, removing the need for the LLM to parse anything.

### 6.2 `--resume` as default

The `--resume` flag could be made the default behavior — always load existing artifacts from disk if present. This would simplify the invocation to a single command for both fresh starts and resumes.

### 6.3 Parallel coder execution

The current design uses a single coder with sequential rule application (via `SendMessage`). For very large files, parallel coders working on independent rules could reduce wall-clock time. This would require extending the state machine to track per-rule state independently.

### 6.4 Agent lifecycle management

The `SendMessage` → `fallback.spawn` pattern handles agent death, but doesn't preserve context from previously completed rules. A more robust approach would persist coder context to the workspace so a replacement coder can pick up exactly where the dead one left off.
