# Data model

What the collectors record, how `analyze.py` joins it, and where the numbers are blind.

Three collectors write the same record kinds into `<data>/sessions/<session>.jsonl`:

- **Claude Code** — `hooks/collect.py` is a hook entry point; `hooks/hooks.json` wires it
  to the events below. All are `async` except `SessionEnd`, which has to finish before
  Claude Code exits.
- **Codex** — `hooks/collect_codex.py` is a hook entry point; `hooks/hooks_codex.json` is
  installed into `~/.codex/hooks.json` and every handler needs one trust review (`/hooks`)
  before Codex will run it. Codex has no Skill tool, so activations come from the rollout.
- **dsh** — `hooks/collect_dsh.py` reads the session logs dsh writes anyway, so nothing is
  installed. `scripts/dsh_logs.py` decodes them (multi-frame zstd, through
  `scripts/dsh_decode.mjs` and Node's built-in `node:zlib`) and the collector maps each
  event onto the same records. A log is re-read only past the `seq` cursor in
  `<data>/state/dsh.json`, so a live session is picked up incrementally.

## Claude Code hooks → records

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

## Codex hooks → records

| Event | Record(s) | Carries |
|---|---|---|
| `SessionStart` | `session_start` | `source`, `model`, `cwd`, `project`, `authoring` (cwd inside this repo) |
| `UserPromptSubmit` | `prompt`; an `activation` `via=user` per `$skill` mention | `preview`, `chars`, `slash` |
| `PostToolUse` `Bash` | `resource` `op=exec` for each existing skill file the command names | `ok` from the exit code in `tool_response`, `None` when it does not say |
| `PostToolUse` `apply_patch` | `resource` `op=edit` | as above |
| `SubagentStart` / `SubagentStop` | `subagent_start` / `subagent_stop`, plus the child rollout's usage | `agent_id` |
| `Stop` / `SessionEnd` | `turn_end` / `session_end`, plus a rollout flush | `prompt_id` = Codex `turn_id` |

Codex turns a hook's stdout into model context on several events, so the collector
never prints. `Interrupt`, `PreCompact` and `PostCompact` are ignored.

## Codex rollout log → records

`~/.codex/sessions/<Y>/<M>/<D>/rollout-<ts>-<id>.jsonl`, read past a byte cursor in
`<data>/state/codex-<session>-<scope>.json` (mirrors `usage_lib.parse_transcript`).

| Rollout record | Record(s) | Notes |
|---|---|---|
| `turn_context` | — | `model` and `cwd` for follow-on calls |
| `event_msg task_started` / `item_completed` `UserMessage` | — | turn id and its start time |
| `event_msg token_count` | `usage` | `last_token_usage` is *this* API call: `input` = `input_tokens - cached_input_tokens`, `cache_read` = `cached_input_tokens`, `output` = `output_tokens` |
| `response_item function_call` (`exec_command`, `apply_patch`, …) | `t_activation` `via=read` | a skill is activated by a command that reads its `SKILL.md` |
| `response_item message` `developer` with `<skills_instructions>` | `catalog` | `entries` counted, `tokens` estimated, entry paths resolved through the block's own `rN` roots |
| `event_msg turn_aborted` | `turn_failed` | `turn_end` comes from the `Stop` hook, not from here |

A turn is one `turn_id`; the stack resets per turn. A call is charged to the stack as
it stood **before** that call's own tool calls — the rollout writes a call's function
calls before its token count — so the call that decides to load a skill is not part of
that skill's cost. `load` is the next call's context minus the activating call's
context and output, and `clean` when the activating call ran no other tool. The cache
split matters: Codex's `input_tokens` **includes** the cached prefix.

## dsh session log → records

One `session.v3.jsonl.zstd` per session under `$DSH_HOME/sessions/<workspace>/<id>/`.
The collector reads these event types; everything else in the log is ignored.

| dsh event | Record(s) | Notes |
|---|---|---|
| `session` (header) | `session_start` | `cwd` → `project`, `authoring`; `origin: subagent` marks a child session |
| `user/message` `source.kind=user` | `prompt` | the text the user typed, plus a `slash` when it starts with `/` |
| `user/message` `source.kind=goal` | `prompt` | the goal tool restates the objective every autonomous round; the objective is recorded once, per goal |
| `user/message` `source.kind=skill-catalog` | `catalog` | the listing the harness sent: `entries`, `tokens` (estimated), and which of *our* skills it named |
| `tool/call` `skill` | `activation` `via=model`, one `usage` segment break | a refused call surfaces as `activation_result` `ok=false` |
| `read` of a `SKILL.md` | `activation` `via=read` | the child-skill path: a sub-skill that is read, not invoked |
| `/name` in a prompt or a dsh command | `activation` `via=user` | dsh's own gesture regex, so a plugin-namespaced name is not a match |
| `read`/`write`/`edit`/`bash` on a skill file | `resource` | the tool result patches `ok`/`error` onto the record it belongs to |
| `subagent` / `subagent_fork` call | `spawn` | `skills` = the stack that dispatched it, `subagent_type` = the tool's description |
| child session, matched by creation time | `agent_link` | filed under the **parent** session, because that is where the join looks |
| `assistant/message` | `usage`, one per stack segment | see below |
| `turn/end` | `turn_end`, or `turn_failed` when the reason is an error | `prompt_id` = `t<turn>` |

`turn` numbers are per session, so a `prompt_id` is `t<turn>`. A resumed session keeps
appending to one log and its turn numbers keep rising, so `t12` can be days after `t1`.

## Token attribution

- **Turn** = one dsh turn (one user-role message and everything the agent did for it).
- **Stack** = the distinct skills activated so far in the session, in load order. It
  never resets within a session: a skill loaded in turn 1 is still in context in turn 9.
- **Run** = `(session, turn, skill)`. Several activations of one skill in one turn are
  one run.
- **Segment** = a stretch of one turn under one stack. `assistant/message` carries the
  step's `usage`; the collector splits the turn at every activation, so the step that
  *decides* to load a skill is not part of that skill's cost and every later step is.
  A segment's `skills` are the stack at the moment it opened; the turn's last segment
  takes the stack as it stood when the turn ended. Their spans overlap, so the tokens
  of a turn exceed the tokens of any single run in it.
- **Tokens/run** is inclusive: every usage segment whose stack contains the skill,
  subagents included. `cache_read` is one of the four columns and dominates under dsh
  (≈99%), where each step re-reads the context prefix; `Load` and `Out/run` are the parts
  the skill itself caused.
- **Load** = the next step's context minus what the issuing step ended with (its fresh
  input plus its cached prefix plus its output), i.e. what the activation added.
- **Subagents.** A child process's log names its parent session; the collector matches it
  to the `subagent` call made at its creation time, files its usage under the parent
  session with `scope=<child id>`, and sets `parent_prompt_id` to the parent turn. That
  is what `analyze.py` joins on. When the parent log is not in the corpus (a deleted or
  older log), the usage is charged to every main-thread skill of its turn, and the Method
  section reports how many subagents that happened to.
- **Outcome.** `completed` = the turn reached `turn/end` with a completed reason.
  `failed` = it ended with an error. `interrupted` = no end, but the session went on to
  another prompt. `open` = no end and nothing came after.
- **Authoring.** In a session whose cwd is inside this repo, `SKILL.md` reads and
  `resource` events count as maintenance, not use. Skill and slash activations there
  still count.
- **Sub-skill edges.** An edge comes from a `REQUIRED SUB-SKILL` line, where any
  catalog name on the line is the child and the surrounding paragraph is the
  condition, or from a nested `SKILL.md` (`<top>:<dir>`). *Same turn* = the child ran
  in the parent's turn. *Later* = it ran in a later turn of the same session.
  *Scripts only* = the child's files were executed in the parent's turn without the
  child being loaded. dsh does not discover nested `SKILL.md` files at all, so a nested
  child can only ever be read by path.

## Claude Code transcript flush → records

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

- **Turn ids.** Claude Code's hook `prompt_id` and its transcript `promptId` normally
  match; when they don't, a transcript turn maps to the hook prompt nearest its
  `turn_ts`, within 30 s. dsh turns already agree, so the mapping is a no-op there.
- **Listing cost.** Claude Code's `listing_tokens` is an estimate from the frontmatter
  description; dsh's `catalog_tokens` is *measured* from the catalog message the harness
  actually sent, so the insight reports `N×tokens` for it and falls back to the estimate
  only when no catalog was observed.

## Blind spots

- Lifecycle ends with the turn. A skill stays in context afterwards, and follow-up
  turns it shapes are not attributed to it.
- Skills preloaded into a subagent through its `skills:` frontmatter are injected at
  startup and never show up as activations.
- A forked skill (`context: fork`) starts its subagent without an `Agent` call, so its
  usage takes the turn-wide fallback.
- `claude -p` kills async hooks at teardown. The synchronous `SessionEnd` flush still
  recovers the usage and the transcript-side activations.
- Cloud sessions don't read `~/.claude/settings.json`, so they leave no data. On dsh,
  deleting a session log deletes its records: nothing re-derives them.
- Codex hooks are skipped until trusted, and the trust is keyed to the handler's hash, so
  an edited hook stops running until you trust it again. Codex writes no hook record at
  all in that state: the session looks like one that never used a skill.
- Deleting a Codex rollout deletes its activations and tokens; the hook records remain,
  so a session can show prompts and resources with no cost.
- A dsh log whose parent log is missing (rotated away, another machine) leaves its child
  unjoined, and that child's tokens are charged to every main-thread skill of the turn.
- A subagent's own prompts are never recorded as prompts: they are the dispatcher's
  text, so counting them would double-count a `/skill` the user typed once and fill the
  trigger review with another agent's words.
- `Load` for a slash command is shown as `~n`, an estimate from the SKILL.md size,
  because no API call precedes the injection.
- The optional dsh hook bridge (mounted only when the plugin is installed) records the
  events dsh also logs, and its
  `prompt_id` is not the log's turn number, so the same prompt can appear twice (once
  with a preview, once from the log). Use it only to confirm live capture — the logs
  are the authority and the bridge adds nothing the analysis needs.
