# Data model

What the hooks record, how `analyze.py` joins it, and where the numbers are blind.

## Hooks → records

`hooks/hooks.json` wires one entry point, `hooks/collect.py`, to these events. All are
`async` except `SessionEnd`, which has to finish before Claude Code exits.

| Event | Record(s) | Carries |
|---|---|---|
| `SessionStart` | `session_start` | `source`, `model`, `cwd`, `project`, `authoring` (cwd inside this repo) |
| `UserPromptSubmit` | `prompt` | `prompt_id`, `preview` (first `$SKILL_USAGE_PROMPT_CHARS`), `chars`, `slash` |
| `UserPromptExpansion` | `activation` `via=user` | the `/skill` the user typed, `source`, `args` |
| `PreToolUse` `Skill` | `activation` `via=model` | `tool_use_id`, `hash` (SKILL.md sha1[:12]), `agent_id` if in a subagent |
| `PostToolUse[Failure]` `Skill` | `activation_result` | `ok`, `error` — refusals such as `disable-model-invocation` land here |
| `PostToolUse[Failure]` `Read`/`Edit`/`Write`/… | `resource` `op=read\|edit`; a `SKILL.md` read also yields `activation` `via=read` | `file` relative to the skill |
| `PostToolUse[Failure]` `Bash` | `resource` `op=exec` for each existing skill file the command names | `ok`, `error` first line, `duration_ms` |
| `PostToolUse` `Agent`/`Task` | `agent_link` | `tool_use_id` ↔ `child_agent_id` |
| `SubagentStart` / `SubagentStop` | `subagent_start` / `subagent_stop`, plus the subagent transcript's usage | `agent_id`, `agent_type` |
| `Stop` / `StopFailure` | `turn_end` / `turn_failed`, plus a transcript flush | `prompt_id`, `error` |
| `SessionEnd` | `session_end`, plus a final flush | `reason` |

Every record has `kind`, `ts` (UTC, ms), and `session_id`, and `prompt_id`,
`agent_id` and `agent_type` when the hook supplies them.

## Transcript flush → records

Hooks don't see token usage. `usage_lib.parse_transcript` gets it from the transcript
instead. It reads **only the complete lines appended since the last flush** (the
cursor is kept in `<data>/state/<session>.json`), so the transcript lagging behind a
hook just pushes those lines to the next flush.

- **Turn** = one `promptId` (older transcripts: one non-meta user line).
- **Stack** = the skills activated so far in that turn, in order. A Skill `tool_use`,
  a `<command-name>` tag, a `Base directory for this skill:` injection and a Read of a
  `SKILL.md` each push onto it. It resets at the next turn.
- **API call** = one `message.id`. Claude Code writes one line per content block, and
  every line repeats the `usage`. The first line counts. A later line adds only the
  amount by which a field grew.
- Each call is charged to the stack *before* that call's own tool calls are applied.
  So the call that decides to invoke a skill is not part of the skill's cost.

| Record | Meaning |
|---|---|
| `usage` | Sums for one `(prompt_id, skills stack, model)`: `calls`, `input`, `cache_creation`, `cache_read`, `output`, plus `turn_ts` (timestamp of the prompt line) |
| `t_activation` | An activation as the transcript shows it. It dedupes against the hook record on `tool_use_id`, or on `(turn, skill, via)` for slash commands |
| `load` | Context growth across an activation: the next call's context minus (the activating call's context + output). `clean` = the activating call made no other tool call |
| `spawn` | An `Agent` tool call and the stack at that moment, so subagent usage can inherit it |

Subagent transcripts get the same parser on `SubagentStop`, with `scope=<agent_id>`.
Sidechain lines in the main transcript are skipped, so nothing is counted twice.

## Analysis joins

- **Run** = `(session, turn, skill)`. Several activations of one skill in one turn
  are one run.
- **Tokens/run** is inclusive: every usage chunk whose stack contains the skill,
  subagents included. A subagent inherits the stack of the `Agent` call that spawned
  it (via `agent_link` → `spawn`). When that link is missing, it is charged to every
  main-thread skill of its turn, and the Method section reports how many subagents
  that happened to.
- **Turn ids.** Hook `prompt_id` and transcript `promptId` normally match. When they
  don't, a transcript turn maps to the hook prompt nearest its `turn_ts`, within 30 s.
- **Outcome.** `completed` = the turn reached `Stop`. `failed` = `StopFailure`.
  `interrupted` = no Stop, but the session went on to another prompt. `open` = no
  Stop and nothing came after.
- **Authoring.** In a session whose cwd is inside this repo, `SKILL.md` reads and
  `resource` events count as maintenance, not use. Skill and slash activations there
  still count.
- **Sub-skill edges.** An edge comes from a `REQUIRED SUB-SKILL` line, where any
  catalog name on the line is the child and the surrounding paragraph is the
  condition, or from a nested `SKILL.md` (`<top>:<dir>`). *Same turn* = the child ran
  in the parent's turn. *Later* = it ran in a later turn of the same session.
  *Scripts only* = the child's files were executed in the parent's turn without the
  child being loaded.

## Blind spots

- Lifecycle ends with the turn. A skill stays in context afterwards, and follow-up
  turns it shapes are not attributed to it.
- Skills preloaded into a subagent through its `skills:` frontmatter are injected at
  startup and never show up as activations.
- A forked skill (`context: fork`) starts its subagent without an `Agent` call, so its
  usage takes the turn-wide fallback.
- `claude -p` kills async hooks at teardown. The synchronous `SessionEnd` flush still
  recovers the usage and the transcript-side activations.
- Cloud sessions don't read `~/.claude/settings.json`, and Codex has no equivalent
  hooks, so neither leaves data.
- `Load` for a slash command is shown as `~n`, an estimate from the SKILL.md size,
  because no API call precedes the injection.
