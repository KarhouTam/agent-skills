# Evals

Two layers, same split as `teaching-technical-topics/evals`: `smoke.py` asserts everything
mechanical about `scripts/run_role.py`, and a human reads the recorded live results below.

## Running the checks

```bash
python3 evals/smoke.py            # offline: dry-run argv, config errors, resume wiring — no model calls
python3 evals/smoke.py --live     # + one sentinel round 1 and one resume per session harness
```

Offline is the gate for every edit to `scripts/run_role.py`. Live is the gate for editing the
`HARNESSES` registry or bumping a harness version. Live costs about ten tiny model calls.

Role instructions live in `prompts/implementer.md` and `prompts/challenger.md`; the brief
handoff, the debate, and the escalation rules live in `SKILL.md`. The layout checks fail if a
prompt file loses a placeholder, or if a role prompt is copied back into `SKILL.md`.

## What is asserted

| Layer | Check |
|-------|-------|
| offline | `preflight` resolves the shipped config |
| offline | role prompts live in `prompts/` with `{{ROUND}}`/`{{BRIEF}}`/`{{PAYLOAD}}`/`{{COMMS}}`, dispute markers included, and `SKILL.md` points at them |
| offline | `{{COMMS}}` is documented as a team and a reloy block for both roles, each carrying its own `## How you communicate` heading |
| offline | the brief template carries goal, criteria, artifact, constraints |
| offline | `harness.json` defines both roles |
| offline | challenger round 1 takes the read-only path on every harness (`-s read-only`, `--allowedTools=Read,Grep,Glob,Bash --disallowedTools=Edit,Write,NotebookEdit`, no `--auto`) |
| offline | implementer round 1 takes the write path on every harness (`workspace-write`, `bypassPermissions`, `--auto`) |
| offline | `model` is forwarded as that harness's own model flag |
| offline | round 2 resumes the recorded session (`--resume`, `exec resume`, `-s`, `--session-id`) and never passes codex's unsupported `-C`/`-s` |
| offline | `--reset-session` drops the recorded session |
| offline | `dsh` resumes the recorded session with `--session-id`, refuses a moved `--cwd` with exit 2, and parses the final message from the `--json` `final` event |
| offline | a `native` role is rejected with exit 3; unknown harness 2; missing binary 4; missing session 5; broken config warns and falls back to native |
| live | round 1 returns the sentinel on every harness |
| live | round 2 resumes and remembers the round-1 sentinel on `claude-code`, `codex`, `opencode`, `dsh` |

## Recorded harness contracts (2026-10-17)

Probed before the registry was written, so `run_role.py` encodes observed behaviour rather
than assumptions. One tiny prompt (`AR_PROBE_OK`) per call.

| Harness | Version | Round 1 | Resume | Session id from | Final message from |
|---------|---------|---------|--------|-----------------|--------------------|
| `dsh` | 0.x (miniconda) | 4.1s, exit 0 | supported since 2026-10-19: `--session-id <id>` adopts the persisted session (creation `--cwd` must match) | first `--json` event: `{"type":"session","sessionId":…}` | the `--json` `{"type":"final","text":…}` event |
| `claude-code` | 2.1.281 | 3.8s, exit 0 | 3.4s, exit 0, same id | caller-chosen UUID (`--session-id`) | JSON `result` |
| `codex` | 0.147.0 | 7.2s, exit 0 | exit 0, same `thread_id` | `thread_id` in the first JSONL event | `-o` last-message file |
| `opencode` | 1.19.3 | 25.4s, exit 0 | 12.5s, exit 0, same id | `ses_…` in every JSON event | text parts of the last `messageID` |

2026-10-19 dsh re-probe (source: `dsh --profile headless --help` and the `dsh-headless` runner
source): a run without `--session-id` creates `session-<uuid>` and, with `--json`, emits the
`session` event first and the `final` event last; `--session-id` with an unknown id is an
error, and adoption refuses a session recorded under a different cwd — hence the runner's
round-1 capture, `--session-id` resume wiring, and cwd guard.

Probe commands:

```bash
dsh --profile headless "Reply with exactly this token and nothing else: AR_PROBE_OK"

~/.local/bin/claude -p --output-format json --session-id "$UUID" \
    --tools "Read,Grep,Glob,Bash" --allowedTools "Read,Grep,Glob,Bash" \
    --permission-prompts none "Reply with exactly …"
~/.local/bin/claude -p --output-format json --resume "$UUID" "What token did I ask …?"

printf 'Reply with exactly …' | codex exec --json -C "$PWD" -s read-only \
    --skip-git-repo-check -o /tmp/codex.out -
printf 'What token did I ask …?' | codex exec resume "$THREAD" --json \
    --skip-git-repo-check -o /tmp/codex2.out -

~/.opencode/bin/opencode run --format json --dir "$PWD" "Reply with exactly …"
~/.opencode/bin/opencode run --format json -s "$SID" --dir "$PWD" "What token did I ask …?"
```

## Recorded live results

| Date | Layer | Result | Notes |
|------|-------|--------|-------|
| 2026-10-20 | offline | **18/18** | `{{COMMS}}` communication method: the orchestrator now picks team vs relay from `harness.json` (team only when both roles run natively on a team-capable harness) and inserts the matching block; four blocks + a heading check added |
| 2026-10-19 | offline | **17/17** | dsh session-keep-alive upgrade (`--json`, `--session-id`, cwd guard); the stale claude-code flag expectation was realigned to the runner's `--allowedTools`/`--disallowedTools` |
| 2026-10-19 | live | 23/25 | `--live --keep` (workspace `/tmp/ar-smoke-z0thk161`): **dsh round 1 + resume pass** (round 2 remembered the sentinel through `--session-id`); claude-code, codex pass. The two opencode FAILs are `402 Insufficient account funds` from its inference upstream — account/billing, not a code regression (its argv is unchanged) |
| 2026-10-17 | offline | **15/15** | after the brief + debate refactor (`prompts/` split, BRIEF/LEDGER checks added) |
| 2026-10-17 | offline | 12/12 | before that refactor |
| 2026-10-17 | live | **20/20** | `python3 evals/smoke.py --live --keep`; the runner is unchanged by the refactor |

## End-to-end run with the shipped config

Recorded before the brief + debate refactor; the runner path it exercises is unchanged.
`prompts.md` scenario 1 is the post-refactor rerun recipe, which now starts from `BRIEF.md`.

`harness.json` as shipped (`implementer: native`, `challenger: opencode`), run dir
`/tmp/ar-e2e/run`:

| Round | Path | Duration | Session | Verdict |
|-------|------|----------|---------|---------|
| 1 | implementer: native DSH subagent wrote `/tmp/ar-e2e/fizzbuzz.py`; challenger: `run_role.py` round 1 | 128.5s | `ses_eb882afd5ffeIegCWmPl0Hwtyd` created | PASS |
| 2 | fatigue-guard re-review, challenger resumed with a delta prompt only | 67.3s | same id, `resume: true` | PASS |

Evidence: `run.jsonl` (two lines, same `session_id`, `resume` false then true),
`sessions.json`, and both `round-*.out.md` files in the run dir. The challenger process was
`opencode`, not a DSH subagent — that is the property this configuration exists to guarantee.

## All-native regression

With `{"implementer": {"harness": "native"}, "challenger": {"harness": "native"}}`,
`preflight` reports both roles `in-process` and the runner refuses either role with exit 3 —
so a native-only loop never spawns a process, exactly the pre-change behaviour.

## Gotchas found while probing

- `codex exec resume` accepts **no** `-C`/`-s`; cwd and sandbox come from the session. Outside
  a git repo or trusted directory it still needs `--skip-git-repo-check` (first resume attempt
  failed on exactly that).
- `claude` and `opencode` are installed outside `PATH` here (`~/.local/bin/claude`,
  `~/.opencode/bin/opencode`). The registry's candidate list finds them; `path` in
  `harness.json` is the override.
- `dsh --profile headless` supports session persistence since the 2026-10-19 re-probe: round 1
  records `session-<uuid>` from the `--json` stream, later rounds adopt it with `--session-id`,
  and the session is bound to its creation `--cwd` — the runner guards that locally (exit 2).
- Harness heterogeneity does not change the model: this machine's `claude` harness routes to
  its own default (`deepseek-flash[1m]`, with a harmless `unrecognized_model` warning on
  stderr). Use `harness.json.model` when you also want a different model family.
- `opencode --format json` puts `sessionID` on every event and groups assistant text by
  `part.messageID`; the runner concatenates the last message's text parts.
- A real review round on `opencode` takes minutes, not seconds — run it as a background job
  and collect it, never with a short foreground timeout.
