---
name: ci-automation
description: Use when the pytorch-test-refactoring orchestrator emits "schedule_cron" or "need_agent" with phase "ci_debug", when a CronCreate cron job fires and you need to run orchestrator.py --ci-check, or when the user says "look after the ci", "monitor ci", "check ci status", "watch the pr", or "debug ci failures".
---

# CI Automation

Monitors CI for an existing PR, classifies failures, and spawns a debugger agent to fix regressions. PR creation and pushing is handled by the user — this skill only looks after CI.

Reference for the CI loop. The root SKILL.md covers the status vocabulary — this file has the debugger format and allowlist.

## Actions by Status

### "done" + phase: "ci_done"

All green. Run `gh pr ready <N>` to mark the draft PR ready for review, then `CronDelete` all CI cron jobs. Truly finished.

### "schedule_cron" + phase: "ci_monitor"

CI still running. `CronCreate` with the `on_complete` fields (cron_interval, prompt, durable=true). Save the job ID, session exits.

### "need_agent" + phase: "ci_debug"

1. Spawn debugger via `Agent` tool: `run_in_background=true`, `mode=bypassPermissions`
2. Capture `agent_id` from result
3. Wait for completion, then save the result JSON to the `feed_file` path from the task spec (use the **Write tool**), and run:
   `python orchestrator.py <file> --ci-check --feed debugger --feed-file <feed_file>`

> **Why `--feed-file`, not `echo ... | python ...`:** the Bash permission matcher is whole-string prefix matching — `echo '{...}' | python orchestrator.py ...` matches no `Bash(python *)` allow rule and is blocked in Auto/restricted modes. A plain `python ... --feed-file <path>` command matches and is auto-approved.

> **Permission caveat for the debugger spawn:** some harnesses ignore the `Agent` tool's `mode` parameter, so the debugger inherits the parent session's permission mode instead of `bypassPermissions`. And an explicit `permissions.deny` rule for `git commit`/`git push` blocks the debugger **in every mode**. The orchestrator now fails fast with an actionable message if it detects such a deny — if you see that error, allow `git commit`/`git push` (see allowlist below) and re-run.

## Debugger Result Format

```json
{
  "agent_id": "c3fa28...",
  "agent_name": "debugger",
  "fixes_applied": [
    {
      "check_name": "pull / linux-bionic-cuda12.1 / test_ops",
      "failure": "Description of the failure",
      "verdict": "caused_by_us",
      "rationale": "Why the refactoring caused this",
      "change": "What code change was made"
    }
  ],
  "unrelated": [
    {
      "check_name": "pull / win-vs2019 / test_ops",
      "failure": "Description",
      "verdict": "unrelated",
      "rationale": "Why this is not our fault"
    }
  ]
}
```

- `agent_id`: **Required** after spawn.
- `agent_name`: Must be `"debugger"`.

## Max Fix Rounds

5 rounds max. On limit: orchestrator leaves a PR comment and marks ready.

## Required Bash Allowlist

```json
{
  "permissions": {
    "allow": [
      "Bash(gh:*)",
      "Bash(git:*)",
      "Bash(lintrunner:*)",
      "Bash(python orchestrator.py:*)"
    ]
  }
}
```

**`git commit` / `git push` must not be denied.** The debugger pushes its fixes, and a `permissions.deny` entry for `Bash(git commit *)` / `Bash(git push *)` blocks the agent in **every** permission mode (deny wins over allow and bypassPermissions). If your setup has git guardrails (e.g. the git-guardrails skill) that deny these, either:
- remove them from `permissions.deny` (add to `permissions.allow` instead), or
- expect CI automation to stop at the "fixes ready" stage and push manually.

The orchestrator emits an actionable error at the start of the debug phase if it detects such a deny, so you'll know before spawning a blocked agent.

## Related

- CI state machine: `ci_ops.py`
- CI operations: `scripts/ci.py`
- Debugger prompt: `agent/prompts/debugger.md`
