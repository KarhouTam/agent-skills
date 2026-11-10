---
name: pytorch-test-refactoring
description: Orchestrate PyTorch test file refactoring to decouple tests from specific hardware accelerators. Use this skill whenever the user asks to refactor, decouple, or reorganize a PyTorch test file for cross-accelerator compatibility, or when they ask to apply the test decoupling workflow to a specific test file. Triggers on phrases like "refactor test_ops.py", "decouple tests in test_file.py", "apply test decoupling to X", or "use pytorch-test-refactoring on X". Also triggers on CI monitoring phrases like "look after the ci", "monitor ci", "check ci status", "watch the pr checks", or "debug ci failures". Always invoke this skill before starting any test refactoring work that involves splitting tests into CPU-only, device-agnostic, and device-specific classes.
disable-model-invocation: true
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
- **DeepSeek Harness (dsh)** — you have `subagent`, `send_message`, and
  `schedule_create` tools → `--harness dsh`

```bash
python /root/.claude/skills/pytorch-test-refactoring/orchestrator.py <test_file_path> --harness <claude|codex|dsh>
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
         │       │   → gh pr ready, delete cron/schedule, done ✓
         │       │
         │       ├── "done" + phase: "finalize"
         │       │   → Refactoring complete. User creates
         │       │     PR manually, then says "look after
         │       │     the CI" or runs --ci-check
         │       │
         │       ├── "schedule_cron"
         │       │   → Claude: CronCreate(on_complete.cron_interval,
         │       │     durable=true, prompt=on_complete.prompt)
         │       │   → dsh: schedule_create(on_complete.rrule,
         │       │     target=this_session, prompt=on_complete.prompt)
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

After each agent completes, extract the key result and feed it to the `on_complete.command`: save the result JSON to the `feed_file` path from the task spec (use `apply_patch` in Codex, the Write tool in Claude Code, or the `write` tool in dsh), then run the command as-is. Prefer this over piping via `echo ... | python ...` — on Claude Code the Bash permission matcher is whole-string prefix matching, so piped/redirected commands don't match `Bash(python *)` allow rules and get blocked in Auto/restricted modes.

**dsh wait semantics:** `subagent` has no blocking wait — it returns an agent id immediately and the runtime delivers a `Background subagent <id> finished ... Its closing message:` notice as a new user message, which starts your next turn. Dispatch, then end the turn if there is nothing else to do; when the notice arrives, extract the result and feed it back. Do not poll. If the notice is lost (the session was unloaded when the sub-agent settled), re-dispatch the same task — the orchestrator has not advanced.

**All result JSON objects may include these optional fields:**
- `agent_id`: The id returned by the harness's spawn tool — Codex's canonical task name (for example `/root/analyst`), Claude Code's Agent id, or dsh's `subagent` id. **Required after any spawn** — this is how the orchestrator learns the agent's identity for future follow-ups.
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

**Before spawning the analyst**: quickly skim the test file (first ~50 lines and a few test methods). Note whether the original file is a plain `TestCase` with no device parametrization and whether tests primarily exercise utility/library logic (`rnn_utils`, `nn.functional` helpers, padding/packing utilities, etc.). If so, expect most tests to be CPU-only. When the analyst report arrives, question any bulk device-agnostic classification — a test that merely creates tensors is not automatically device-agnostic.

**When you see `"done"` with `phase: "finalize"`, the refactoring is complete.** The `next_steps` field tells you how to proceed: create a PR manually (branch, commit, push, draft PR), then say **"look after the CI"** or run the emitted `--ci-check` command with your `--harness` to start CI monitoring. Only when you see `"done"` with `phase: "ci_done"` is the entire workflow truly finished — the `next_steps` field will tell you to mark the PR ready for review and delete the CI cron job/schedule.

**Important: When you spawn a new agent (method=spawn),** capture the id from the spawn tool's result — Codex's canonical task name (for example `/root/analyst`), Claude Code's Agent id, or dsh's `subagent` id — and include it as `agent_id` (along with `agent_name`) in the result JSON you feed back. This is how the orchestrator learns the agent's identity for future follow-ups. Without it, the follow-up has no target and the harness must fall back to a fresh spawn.

## CI Automation (Phase 8)

After refactoring completes, the user creates a PR manually. To start CI monitoring, say **"look after the CI"** or run:

```bash
python orchestrator.py <test_file_path> --harness <claude|codex|dsh> --ci-check [--pr-number N]
```

The orchestrator auto-detects the PR from the current branch. Pass `--pr-number` to skip detection.

| Status | `phase` | What to do |
|--------|---------|------------|
| `"done"` | `"ci_done"` | All CI green. Run `gh pr ready <N>`, then delete the CI cron job/schedule: Claude Code `CronDelete`, dsh `schedule_edit(action=delete)` (find it with `schedule_list`). Truly finished. |
| `"schedule_cron"` | `"ci_monitor"` | CI still running. Claude Code: `CronCreate(cron_interval, durable=true, prompt=...)`; dsh: `schedule_create` with the `on_complete` fields (`title`, `target`, `rrule`, `prompt`) — from the top-level session, since a delegated sub-agent cannot create schedules. Session exits. |
| `"need_agent"` | `"ci_debug"` | CI failures found. Spawn the debugger agent (dsh: `subagent`; Claude Code: background `Agent`). When done, feed the result back via `--feed debugger --feed-file <path>` (save JSON with the file-writing tool, then run the command). Loop. |

The debugger agent's result format and the full CI ops reference: `agent/skills/ci-automation/SKILL.md`.

## Feedback Ingest (sidecar)

Harvest reviewer feedback from KarhouTam's merged `[Test]` PRs and turn it
into ruleset edits after human approval. Runs independently of the 8-phase
refactoring workflow.

```bash
# Harvest + analyze (cron-driven; runs triage then draft steps)
python orchestrator.py --harness <claude|codex|dsh> --ingest-feedback

# Apply approved findings (after editing a findings file's checkboxes)
python orchestrator.py --harness <claude|codex|dsh> --apply-ingest agent_space/ingest/findings/PR-<n>.md
```

The AI steps are harness-dependent, same as the review queue:

- **Claude Code** — status=`need_agent` with `tasks[]` (`method=spawn`): spawn
  the triage/analyst/ruleset-editor agent, then feed its result back.
- **dsh** — same shape as Claude Code (`tasks[]`, `method=spawn`): dispatch each
  task with `subagent`, then feed the result back when its completion notice
  arrives.
- **Codex** — status=`need_agent` with `method="inline"`: YOU are the
  triage/analyst/ruleset-editor agent. Follow `instruction`, write the result
  JSON to the `feed_file` path, then run `on_complete.command`. Do NOT spawn
  sub-agents.

**What the two stages decide:** the triage stage dedups the harvested
comments to their root cause — grouped by the rule each would change, not by
comment text — so several comments proposing the same rule change become ONE
item carrying several comment ids. The analyst then drafts one edit per
`intent`, each citing its comment id(s), naming the exact rule it targets in
`review-test-refactoring/SKILL.md` (a Part I section or a Part II
sub-section) or the deterministic check it implies, and stating the
consequence of not making the change. A comment that maps to a
deterministically-decidable rule drafts a `verify.py` check rather than
prose, and a draft that cannot point at BOTH a rule and a comment is dropped
rather than guessed at.

**Daily trigger:** Claude Code `CronCreate` (durable) — dsh `schedule_create` with `target: this_session` — carrying this prompt:

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

dsh runs this same prompt scheduled with `schedule_create`
(`target: this_session`) on the spawn branch: dispatch with `subagent` and
feed the result when the completion notice arrives.

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
`agent_space/pr_needs_review.txt` in daily batches, applying the review
criteria of `agent/skills/review-test-refactoring/SKILL.md`. The review output
is NOT published automatically: each batch renders a **pending-publication
ledger** (`agent_space/pr_reviews/pending_publication.md`) that **the user
edits and approves**, and only after that explicit approval does the publish
step submit one GitHub review per PR (summary body + inline comments, posted
in English by the gh account the user is logged in as). Runs independently
of the 8-phase refactoring workflow.

```bash
# One daily batch (defaults to 10 PRs per run)
python orchestrator.py --review-queue --harness <claude|codex|dsh> [--limit N]

# After the user reviewed and approved the ledger:
python orchestrator.py --review-queue --publish
```

**What each run does**

1. **Select** (deterministic): classify pending PRs FIFO via `gh pr view`.
   Open PRs with changed test files (`test/**` or `torch/testing/**`) fill
   the review queue up to `--limit`; merged/closed PRs and PRs without test
   changes are marked not-applicable (archived silently when drafting — they
   never receive a published comment); PRs whose metadata cannot be fetched
   are silently skipped and stay pending. PRs previously published as
   `changes_requested` are re-admitted ahead of the pending list when their
   head commit has moved (see Re-admit below).
2. **Pre-pass + lint gate** (deterministic, before any reviewer): the four
   machine-decidable criteria of the review skill (II.12) run over each PR's
   ADDED diff lines, and the **mandatory TEST_LINTER gate** runs the official
   test linter (vendored into `scripts/linter.py` at a pinned upstream SHA,
   with the official allowlist) over the PR-head version of every changed
   test file. Both land in
   `agent_space/pr_reviews/pr_<n>_prepass.json` (the gate inside the
   `lint_gate` key, the diff cached beside it). A PR whose pre-pass or gate
   reports `ran: false` cannot be reviewed — the gate is mandatory — so it
   stays pending for the next run. A reviewer takes these findings as given
   (a `TEST_LINTER` error is a Blocker) instead of re-deriving them.
3. **Review** (AI, harness-dependent). Each reviewer applies BOTH parts of
   `agent/skills/review-test-refactoring/SKILL.md` in diff-based mode and
   writes all human-readable content in **English**: one reviewer sub-agent
   per PR (Claude Code and dsh, waves of up to 4) or one inline instruction
   (Codex).
   Results: `agent_space/pr_reviews/pr_<n>_result.json` — findings carry a
   one-sentence `inline` text meant to become the GitHub inline comment.
4. **Draft** (deterministic, the gate for humans): every reviewed PR becomes
   one section of the pending-publication ledger — the review body
   (summary + terse findings list + the `_Generated by my agent. Reviewed
   by the user._` note) and one inline-comment block per line-anchored
   finding. `event:` is drafted as COMMENT — the
   machine never submits REQUEST_CHANGES on its own (the semantic verdict
   lives in the `verdict:` header and the archive, where it keeps driving the
   re-review-on-new-commit watch); a REQUEST_CHANGES event exists only if
   the user deliberately edits that line. The orchestrator then reports
   `needs_publish` and STOPS; nothing reaches GitHub yet.
5. **Publish** (deterministic, only on user approval): re-reads the ledger —
   the document is the single source of truth for the final published text —
   and submits one review per section via `gh api` (`event`, verbatim body,
   inline comments anchored to the PR diff's added lines). Posted PRs are
   archived with their `review_url`, removed from the pending list, and
   their sections deleted from the ledger.

**Executor loop** (the JSON the orchestrator prints):

```
- Claude, status=need_agent (tasks[]): spawn each reviewer sub-agent; after
  each finishes, run that task's feed_cmd to feed the result back.
- Claude, status=waiting: wait for the remaining in-flight reviewers and feed
  them.
- dsh, status=need_agent (tasks[]): dispatch each reviewer with `subagent`;
  run that task's feed_cmd as each completion notice arrives.
- dsh, status=waiting: wait for the remaining completion notices and feed them.
- Codex, status=need_agent (method="inline"): YOU are the reviewer — follow
  the instruction, write one result JSON per PR, then run on_complete.feed_cmd.
- status=needs_publish: review done. Show the user the ledger path; the user
  edits/approves it; publish only on the user's explicit go-ahead.
- status=done (batch, after --publish): the review URLs are in outcomes.
```

**Daily trigger:**

```
Claude Code: run
`python orchestrator.py --review-queue --limit 10 --harness claude`.
On `need_agent`, spawn the reviewer sub-agents and feed each result; on
`waiting`, wait for the remaining agents. On `needs_publish`, report the
ledger to the user and wait for approval. Loop until `done`.

Codex: run
`python orchestrator.py --review-queue --limit 10 --harness codex`.
On `need_agent` with `method="inline"`, perform the reviews yourself (do NOT
spawn sub-agents), write the result files, then run `feed_cmd`. On
`needs_publish`, report the ledger to the user and wait. Loop until `done`.

dsh: run
`python orchestrator.py --review-queue --limit 10 --harness dsh`.
On `need_agent`, dispatch each reviewer with `subagent` and run its
`feed_cmd` as the completion notice arrives; on `waiting`, wait for the
remaining notices. On `needs_publish`, report the ledger to the user and
wait. Loop until `done`.
```

Failure semantics: a reviewer that fails, a PR whose pre-pass or lint gate
could not run, or a PR whose metadata cannot be fetched is not published and
stays in the pending list for the next run.

**Re-admit:** an archived PR whose verdict was `changes_requested` is watched
for a new head commit — `head_oid` is recorded in
`agent_space/pr_reviews/pr_reviewed.json` at publish time. When the head has
moved and the PR is still open, it re-enters the queue at the front, so an
author's fix is reviewed in the next batch instead of being lost forever.
At most the 30 most recent `changes_requested` records are probed, to bound
the extra `gh` calls.

Why Codex differs: Codex MultiAgentV2 records the `spawn_agent` task `message`
as an assistant/commentary mailbox envelope rather than a user/task message
(openai/codex#25458), so spawned reviewers ignore their assignment and re-run
the orchestrator. Claude Code's `Agent` tool and dsh's `subagent` deliver the
task correctly, so both keep the per-PR sub-agent model.

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
python orchestrator.py test/test_ops.py --harness <claude|codex|dsh> --resume
```

The orchestrator loads all artifacts from the workspace and continues from where it left off.

## Workflow Phases (reference)

1. **Assess** — deterministic: file size, class layout, coder count, line ranges
2. **Analyze** — AI agent (analyst): classify every test, identify stale imports, review skip decorators
3. **Distribute** — deterministic: convert strategy assignments into per-rule coder tasks
4. **Code + Check** — AI loop: coder applies one rule → checker verifies → next rule (single coder, per-rule iteration, max 3 fix retries)
5. **Verify** — deterministic: automated checks (syntax, test count, class structure, DecorateInfo alignment, external refs, stale patterns, review mechanics (the II.12 checks, over the lines this change added), import
audit, lint). A **lint hard gate** runs after verify — if the test linter reports error-severity messages, the flow synthesizes findings and routes them to the coder to fix before the final review (max 3 retries).
6. **Final Review** — AI agent (checker): **mandatory** full-file quality review; findings → coder fix → re-verify (max 3 retries)
6.5 **Local test gate** — deterministic: run the whole refactored file on CPU (and CUDA when available) via `--use-pytest --junitxml`, parse JUnit XML, relay `FAIL`/`ERROR` to the coder to fix-or-defer (pre-existing/environmental), re-run, bounded soft-fail at 3 rounds
7. **Finalize** — deterministic: generate `final_summary.md`
8. **CI Ops** — user creates PR manually, then triggers CI monitoring (via "look after the CI" or `--ci-check`). The state machine cron-monitors CI, classifies failures, spawns a debugger agent to fix regressions, pushes fixes, and marks the PR ready. See CI Automation section above.

## Key Rules (non-negotiable, from agent/skills/refactor-test-decoupling)

- **KEEP blacklist skips**: `@skipXPU`, `@skipCUDAIf`, `@skipMPS`, `@skipMeta` (`@onlyNativeDeviceTypes` / `@onlyNativeDeviceTypesAnd` are redundant — REMOVE)
- **ENLARGE whitelist**: `@onlyCUDA` → `@onlyAccelerator`, `@onlyOn` → `@onlyAccelerator`
- **Class naming**: Renaming is OPTIONAL (the future `hw_classification` member handles classification). Recommended names if renaming: `TestFoo` (CPU-only), `TestFooDevice` (device-agnostic), device-specific keeps the original name — `instantiate_device_type_tests` appends the device suffix. Agent decides based on external reference impact.
- **Phase 6 is mandatory** — checker always reviews, even if verification passes
- **External refs after rename**: When classes are renamed, update `common_methods_invocations.py` DecorateInfo entries, and rename stale entries in `test/dynamo_skips/` and `test/dynamo_expected_failures/`. **CRITICAL: dynamo skip/expected-failure files are sentinels (often 0 bytes). Search by FILENAME (`find -name`), NEVER by content (`grep`).** When a device-parametrized class is renamed (TestFoo → TestFooDevice), `instantiate_device_type_tests` renames device variants too (TestFooCUDA → TestFooDeviceCUDA). Files named after old variants must be renamed to match

### Three strategies

| Strategy | Class naming | Mechanism | When |
|----------|-------------|-----------|------|
| CPU-only | `TestFoo` (original name) | `@instantiate_parametrized_tests` or `TestCase` | No device dependency, pure CPU logic. **Also includes tests of device-agnostic utility logic (`rnn_utils`, `pad_sequence`, `pack_sequence`, `F.pad`, etc.) where running on multiple devices adds no meaningful coverage.** This is the DEFAULT for tests that were previously CPU-only with no device decorators. |
| device-agnostic | `TestFoo` or `TestFooDevice` | `instantiate_device_type_tests()` | Uses `device` parameter AND running on multiple devices provides specific testing value: device transfer semantics, memory format behavior, device-specific error paths, or tests originally gated behind `torch.cuda.is_available()`. **Do NOT classify as device-agnostic just because a test creates tensors.** |
| device-specific | `TestFoo` (original name — `instantiate_device_type_tests` appends the device) | `instantiate_device_type_tests(TestFoo, globals(), only_for="cuda")` | Requires truly device-specific APIs (NCCL, cuDNN, etc.) |

**Device-specific instantiation rule**: Device-specific classes ALWAYS use `instantiate_device_type_tests(TestFoo, globals(), only_for="cuda")` (or `"mps"`/`"xpu"`). Each test method receives `device` as its first parameter (always the target device), eliminating the need for per-method `@onlyCUDA` decorators or hardcoded `device = "cuda"` lines. Do NOT use a plain `TestCase` with a `setUp` guard — the test linter rejects it. This keeps mechanism consistency with device-agnostic classes and lets `instantiate_device_type_tests` inject device-aware dtype resolution.

## Related

- Methodology: `agent/skills/refactor-test-decoupling`
- Review: `agent/skills/review-test-refactoring`
- State machine: `flow.py`
- Data models: `state.py`
