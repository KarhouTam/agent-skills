---
name: pytorch-test-refactoring
description: Orchestrate PyTorch test file refactoring to decouple tests from specific hardware accelerators. Use this skill whenever the user asks to refactor, decouple, or reorganize a PyTorch test file for cross-accelerator compatibility, or when they ask to apply the test decoupling workflow to a specific test file. Triggers on phrases like "refactor test_ops.py", "decouple tests in test_file.py", "apply test decoupling to X", or "use pytorch-test-refactoring on X". Also triggers on CI monitoring phrases like "look after the ci", "monitor ci", "check ci status", "watch the pr checks", or "debug ci failures". Always invoke this skill before starting any test refactoring work that involves splitting tests into accelerator-unrelated, accelerator-agnostic, and accelerator-specific classes.
---

# PyTorch Test Refactoring

8-phase workflow (7 refactoring + 1 CI automation) driven by `flow.py` + `ci_ops.py` state machines + `orchestrator.py` deterministic wrapper.

## Usage (one command)

First, determine which harness you are running in, then pass the matching
`--harness` flag on the first launch:

- **Claude Code** — you have `Agent`, `SendMessage`, `CronCreate`, and
  `Write`/`Read` tools → `--harness claude`
- **Codex** — you have `spawn_agent`, `followup_task`, and
  `wait_agent` collaboration tools → `--harness codex`

```bash
python /root/.claude/skills/pytorch-test-refactoring/orchestrator.py <test_file_path> --harness codex
```

The flag is sticky: every resume/feed command the orchestrator emits carries
the same `--harness`, so you only set it once. Use it on `--ci-check` and
`--ingest-feedback` launches too. Omitting it defaults to `claude`.

## Test Fields

Every test file is assigned one of three fields: `core` (default),
`distributed`, or `graph`. Field detection is exact membership in
`reference/distributed/test_list.txt` or `reference/graph/test_list.txt`;
an unmatched path is `core`, and a path present in both non-core lists is an
error.

- `core` uses the legacy `reference/` directory and the full accelerator
  decoupling workflow.
- Non-core fields use `reference/<field>/` and currently run a safe baseline:
  import/symbol cleanup only, field-agnostic verification and review, and no
  local-test gate until a field-specific profile is added.

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
         │    │  method=spawn → spawn_agent    │
         │    │    task_name/message/          │
         │    │    fork_turns from task spec   │
         │    │  → wait_agent(timeout_ms)      │
         │    │  method=send_message →         │
         │    │    followup_task(target=       │
         │    │    task.target, message=...)   │
         │    │    if agent dead/unreachable   │
         │    │    → use fallback (spawn_agent)│
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

After each agent completes, extract the key result and feed it to the `on_complete.command`: save the result JSON to the `feed_file` path from the task spec (use `apply_patch` in Codex or the Write tool in Claude Code), then run the command as-is. Prefer this over piping via `echo ... | python ...` — the Bash permission matcher is whole-string prefix matching, so piped/redirected commands don't match `Bash(python *)` allow rules and get blocked in Auto/restricted modes.

**All result JSON objects may include these optional fields:**
- `agent_id`: The canonical task name returned by `spawn_agent` (for example `/root/analyst`) when spawning. **Required after any spawn** — this is how the orchestrator learns the agent's identity for future `followup_task` calls.
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

- `agent_id` / `agent_name`: **Include after spawn** so the orchestrator can target this agent for future `followup_task` calls
- `success`: did the coder apply the rule without errors?
- `tests_moved`: list of "test_name: OldClass -> NewClass"
- `errors`: any error messages (empty if success)
- Parse the coder's output: look for "error"/"success" indicators, test movement summary

### Coder — local test fix (`--feed coder` when phase=test)

The coder receives the local test failures and returns one verdict per failure
— `fixed` (refactor-caused, fixed) or `deferred` (pre-existing/environmental):

```json
{
  "agent_name": "coder",
  "verdicts": [
    {
      "test_name": "test_foreach.TestFooDeviceCPU.test_foo",
      "verdict": "fixed",
      "fix_applied": "removed incorrect @onlyAccelerator"
    },
    {
      "test_name": "test_foreach.TestOther.test_bar",
      "verdict": "deferred",
      "defer_reason": "pre-existing CPU failure unrelated to the refactor"
    }
  ]
}
```

- `test_name` / `outcome` / `device_type` come from the failure list the coder received
- `verdict`: exactly `"fixed"` or `"deferred"` per failure
- `fix_applied` (for `fixed`) and `defer_reason` (for `deferred`) are free text

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

Your ONLY job: run the command → follow the JSON → extract result → feed the result JSON back via the `on_complete` command (write it to the `feed_file` path, then run the command).

**Before spawning the analyst**: quickly skim the test file (first ~50 lines and a few test methods). Note whether the original file is a plain `TestCase` with no device parametrization and whether tests primarily exercise utility/library logic (`rnn_utils`, `nn.functional` helpers, padding/packing utilities, etc.). If so, expect most tests to be S1. When the analyst report arrives, question any bulk S2 classification — a test that merely creates tensors is not automatically S2.

**When you see `"done"` with `phase: "finalize"`, the refactoring is complete.** The `next_steps` field tells you how to proceed: create a PR manually (branch, commit, push, draft PR), then say **"look after the CI"** or run `python orchestrator.py <file> --ci-check` to start CI monitoring. Only when you see `"done"` with `phase: "ci_done"` is the entire workflow truly finished — the `next_steps` field will tell you to mark the PR ready for review and delete cron jobs.

**Important: When you spawn a new agent (method=spawn),** capture the canonical task name from the `spawn_agent` result (for example `/root/analyst`) and include it as `agent_id` (along with `agent_name`) in the result JSON you feed back. This is how the orchestrator learns the agent's identity for future `followup_task` calls. Without it, `followup_task` will fail because it needs that canonical target, not a name.

## CI Automation (Phase 8)

After refactoring completes, the user creates a PR manually. To start CI monitoring, say **"look after the CI"** or run:

```bash
python orchestrator.py <test_file_path> --harness <claude|codex> --ci-check [--pr-number N]
```

The orchestrator auto-detects the PR from the current branch. Pass `--pr-number` to skip detection.

| Status | `phase` | What to do |
|--------|---------|------------|
| `"done"` | `"ci_done"` | All CI green. Run `gh pr ready <N>`, then `CronDelete` all CI cron jobs. Truly finished. |
| `"schedule_cron"` | `"ci_monitor"` | CI still running. `CronCreate(cron_interval, durable=true, prompt=...)` using the `on_complete` fields. Session exits. |
| `"need_agent"` | `"ci_debug"` | CI failures found. Spawn debugger agent (background, `mode: bypassPermissions`). When done, feed the result back via `--feed debugger --feed-file <path>` (save JSON with the Write tool, then run the command). Loop. |

The debugger agent's result format and the full CI ops reference: `agent/skills/ci-automation/SKILL.md`.

## Feedback Ingest (sidecar)

Harvest reviewer feedback from KarhouTam's merged `[Test]` PRs and turn it
into ruleset edits after human approval. Runs independently of the 8-phase
refactoring workflow.

```bash
# Harvest + analyze (cron-driven; runs triage then draft steps)
python orchestrator.py --harness <claude|codex> --ingest-feedback

# Apply approved findings (after editing a findings file's checkboxes)
python orchestrator.py --harness <claude|codex> --apply-ingest agent_space/ingest/findings/PR-<n>.md
```

The AI steps are harness-dependent, same as the review queue:

- **Claude Code** — status=`need_agent` with `tasks[]` (`method=spawn`): spawn
  the triage/analyst/ruleset-editor agent, then feed its result back.
- **Codex** — status=`need_agent` with `method="inline"`: YOU are the
  triage/analyst/ruleset-editor agent. Follow `instruction`, write the result
  JSON to the `feed_file` path, then run `on_complete.command`. Do NOT spawn
  sub-agents.

**Daily cron:** `CronCreate` with a durable prompt:

```
Run `python orchestrator.py --ingest-feedback`. Read the JSON output.
If status is `need_agent`:
- `method=spawn` (Claude): spawn the agent with the provided parameters
  (run_in_background=true). When done, save the result JSON to the
  `feed_file` path (Write tool), then run the `on_complete.command`.
- `method=inline` (Codex): perform the task yourself — follow `instruction`,
  write the result JSON to the `feed_file` path, then run the
  `on_complete.command`. Do NOT spawn sub-agents.
Loop until status is `done`.
```

Workspace: `agent_space/ingest/` (state in `state.json`, reviewable findings
in `findings/`). State cursor is per-PR `last_checked_at`; comments already
processed are skipped. Only merged PRs (the `Merged` label) and replied
inline threads + `claude[bot]` summaries are harvested.

Why Codex differs: Codex MultiAgentV2 records the `spawn_agent` task `message`
as an assistant/commentary mailbox envelope rather than a user/task message
(openai/codex#25458), so spawned ingest agents ignore their assignment and
re-run the orchestrator — the same bug that forced the review queue to inline
execution. The executor therefore performs triage/analyst/apply steps itself.

## PR Review Queue (sidecar)

Review open PyTorch test-decoupling PRs listed in
`agent_space/pr_needs_review.txt` in daily batches and post the results as
one issue comment per day to
`cosdt/pytorch-initial-pr-reviews#1`. Runs independently of the 8-phase
refactoring workflow.

```bash
# One daily batch (defaults to 10 PRs per run)
python orchestrator.py --review-queue --harness <claude|codex> [--limit N]
```

**What each run does**

1. **Select** (deterministic): classify pending PRs FIFO via `gh pr view`.
   Open PRs with changed test files (`test/**` or `torch/testing/**`) fill
   the review queue up to `--limit`; merged/closed PRs and PRs without test
   changes are marked not-applicable; PRs whose metadata cannot be fetched
   are silently skipped and stay pending.
2. **Review** (AI, harness-dependent):
   - **Claude Code**: one `reviewer` sub-agent per PR, emitted in waves of up
     to 4 concurrent tasks. Each sub-agent runs the diff-based mode of the
     `review-test-refactoring` skill and writes a structured result to
     `agent_space/pr_reviews/pr_<n>_result.json`.
   - **Codex**: ONE inline instruction for the harness executor (main agent),
     which reviews every PR itself with its own tools (no sub-agents) and
     writes the same per-PR result files.
3. **Publish** (deterministic): render one comment with a collapsed
   `<details>` block per PR (full Blocker/Major/Minor findings, `@author`
   mention), post it to the tracking issue, archive processed PRs in
   `agent_space/pr_reviews/pr_reviewed.json`, and rewrite
   `agent_space/pr_needs_review.txt` (processed PRs removed, failures kept).

**Executor loop** (the JSON the orchestrator prints):

```
- Claude, status=need_agent (tasks[]): spawn each reviewer sub-agent; after
  each finishes, run that task's feed_cmd to feed the result back.
- Claude, status=waiting: wait for the remaining in-flight reviewers and feed
  them.
- Codex, status=need_agent (method="inline"): YOU are the reviewer — follow
  the instruction, write one result JSON per PR, then run on_complete.feed_cmd.
- status=done: batch complete; the comment URL is in comment_url.
```

**Daily trigger:**

```
Claude Code: run
`python orchestrator.py --review-queue --limit 10 --harness claude`.
On `need_agent`, spawn the reviewer sub-agents and feed each result; on
`waiting`, wait for the remaining agents. Loop until `done`.

Codex: run
`python orchestrator.py --review-queue --limit 10 --harness codex`.
On `need_agent` with `method="inline"`, perform the reviews yourself (do NOT
spawn sub-agents), write the result files, then run `feed_cmd`. Loop until
`done`.
```

Failure semantics: a reviewer that fails (or a PR whose metadata cannot be
fetched) is **not mentioned in the comment** and stays in the pending list
for the next run. Not-applicable PRs (merged/closed or no test changes) ARE
listed in the comment as `不适用` and archived.

Why Codex differs: Codex MultiAgentV2 records the `spawn_agent` task `message`
as an assistant/commentary mailbox envelope rather than a user/task message
(openai/codex#25458), so spawned reviewers ignore their assignment and re-run
the orchestrator. Claude Code's `Agent` tool delivers the task correctly, so
Claude keeps the per-PR sub-agent model.

## Workspace

```
agent_space/refactor/{field}/{file_name}/
├── assessment.json
├── analyst_report.md / .json
├── coder_tasks.json
├── verification.json
├── review_findings.json
├── local_test.json
├── final_summary.md
├── audit.jsonl
├── status.json
└── flow_state.json
```

## Resuming After Interruption

```bash
python orchestrator.py test/test_ops.py --harness <claude|codex> --resume
```

The orchestrator loads all artifacts from the workspace and continues from where it left off.

## Workflow Phases (reference)

1. **Assess** — deterministic: file size, class layout, coder count, line ranges
2. **Analyze** — AI agent (analyst): classify every test, identify stale imports, review skip decorators
3. **Distribute** — deterministic: convert strategy assignments into per-rule coder tasks
4. **Code + Check** — AI loop: coder applies one rule → checker verifies → next rule (single coder, per-rule iteration, max 3 fix retries)
5. **Verify** — deterministic: automated checks (syntax, test count, class structure, DecorateInfo alignment, external refs, stale patterns, import audit, lint). A **lint hard gate** runs after verify — if the test linter reports error-severity messages, the flow synthesizes findings and routes them to the coder to fix before the final review (max 3 retries).
6. **Final Review** — AI agent (checker): **mandatory** full-file quality review; findings → coder fix → re-verify (max 3 retries)
6.5 **Local test gate** — deterministic: run the whole refactored file on CPU (and CUDA when available) via `--use-pytest --junitxml`, parse JUnit XML, relay `FAIL`/`ERROR` to the coder to fix-or-defer (pre-existing/environmental), re-run, bounded soft-fail at 3 rounds
7. **Finalize** — deterministic: generate `final_summary.md`
8. **CI Ops** — user creates PR manually, then triggers CI monitoring (via "look after the CI" or `--ci-check`). The state machine cron-monitors CI, classifies failures, spawns a debugger agent to fix regressions, pushes fixes, and marks the PR ready. See CI Automation section above.

## Key Rules (non-negotiable, from agent/skills/refactor-test-decoupling)

- **KEEP blacklist skips**: `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta` (`@onlyNativeDeviceTypes` / `@onlyNativeDeviceTypesAnd` are redundant — REMOVE)
- **ENLARGE whitelist**: `@onlyCUDA` → `@onlyAccelerator`, `@onlyOn` → `@onlyAccelerator`
- **Class naming**: Renaming is OPTIONAL (the future `hw_classification` member handles classification). Recommended names if renaming: `TestFoo` (S1), `TestFooDevice` (S2), S3 keeps the original name — `instantiate_device_type_tests` appends the device suffix. Agent decides based on external reference impact.
- **Phase 6 is mandatory** — checker always reviews, even if verification passes
- **External refs after rename**: When classes are renamed, update `common_methods_invocations.py` DecorateInfo entries, and rename stale entries in `test/dynamo_skips/` and `test/dynamo_expected_failures/`. **CRITICAL: dynamo skip/expected-failure files are sentinels (often 0 bytes). Search by FILENAME (`find -name`), NEVER by content (`grep`).** When a device-parametrized class is renamed (TestFoo → TestFooDevice), `instantiate_device_type_tests` renames device variants too (TestFooCUDA → TestFooDeviceCUDA). Files named after old variants must be renamed to match

### Three strategies

| Strategy | Class naming | Mechanism | When |
|----------|-------------|-----------|------|
| S1 | `TestFoo` (original name) | `@instantiate_parametrized_tests` or `TestCase` | No device dependency, pure CPU logic. **Also includes tests of device-agnostic utility logic (`rnn_utils`, `pad_sequence`, `pack_sequence`, `F.pad`, etc.) where running on multiple devices adds no meaningful coverage.** This is the DEFAULT for tests that were previously CPU-only with no device decorators. |
| S2 | `TestFoo` or `TestFooDevice` | `instantiate_device_type_tests()` | Uses `device` parameter AND running on multiple devices provides specific testing value: device transfer semantics, memory format behavior, accelerator-specific error paths, or tests originally gated behind `torch.cuda.is_available()`. **Do NOT classify as S2 just because a test creates tensors.** |
| S3 | `TestFoo` (original name — `instantiate_device_type_tests` appends the device) | `instantiate_device_type_tests(TestFoo, globals(), only_for="cuda")` | Requires truly device-specific APIs (NCCL, cuDNN, etc.) |

**S3 instantiation rule**: S3 classes ALWAYS use `instantiate_device_type_tests(TestFoo, globals(), only_for="cuda")` (or `"mps"`/`"xpu"`). Each test method receives `device` as its first parameter (always the target device), eliminating the need for per-method `@onlyCUDA` decorators or hardcoded `device = "cuda"` lines. Do NOT use a plain `TestCase` with a `setUp` guard — the test linter rejects it. This keeps mechanism consistency with S2 and lets `instantiate_device_type_tests` inject device-aware dtype resolution.

## Related

- Methodology: `agent/skills/refactor-test-decoupling`
- Review: `agent/skills/review-test-refactoring`
- State machine: `flow.py`
- Data models: `state.py`
