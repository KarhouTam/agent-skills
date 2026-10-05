# DeepSeek Harness Tool Mapping

Skills speak in actions ("dispatch a subagent", "create a todo", "read a file"). On DeepSeek Harness (`dsh`) these resolve to the tools below.

## Tools

| Action skills request | DeepSeek Harness tool |
|----------------------|----------------------|
| Read a file | `read` |
| Read an image | `read_image` |
| Create a new file | `write` |
| Edit a file (targeted change) | `edit`; read it first, and pass `replace_all` for a literal that repeats |
| Run a shell command | `bash` — a fresh shell per call, so pass `workdir` instead of `cd` between calls |
| Search file contents | `grep` |
| Find files by name | `glob` |
| Fetch a URL | `web_fetch` |
| Search the web | `web_search` |
| Invoke a skill | `skill`, with the exact name from the session catalog |
| Dispatch a subagent (`Subagent (general-purpose):` template) | `subagent`, or `subagent_fork` when the child should inherit this conversation |
| Multiple parallel dispatches | Several `subagent` calls in one assistant message |
| Fan work out across many subagents | `workflow` (only when your human partner asks for it) |
| Task tracking ("create a todo", "mark complete") | `todo_write` |
| Ask the user a question | `ask_user_question` |
| Hand over deliverables | `present` — required for files your human partner asked to receive |
| Run long work without blocking | `bash` with `run_in_background`; read it with `job_output`, stop it with `job_kill` |
| Persist a long-running objective | `create_goal` / `get_goal` / `update_goal` |
| Fresh-agent iteration | `ralph` (only when your human partner asks for Ralph) |

## Instructions file

When a skill mentions "your instructions file", on DeepSeek Harness this is **`AGENTS.md`**: the user-global file at **`$DSH_HOME/AGENTS.md`** (`~/.dsh/AGENTS.md`), then the project chain from the project root down to the session working directory, broad to specific. `CLAUDE.md` is an alternative candidate at the same scope and is not repeated when it duplicates its `AGENTS.md`; `AGENTS.local.md` and `CLAUDE.local.md` are additive overlays.

## Invoking a skill

The `skill` tool takes the exact name from the session catalog. The catalog carries summaries only, so invoke a skill before acting on it and load every skill that applies. A skill your human partner invoked directly arrives inline as `<skill_content>` — follow it and do not load it again through the tool.

Local skills come from the project roots (`.dsh/skills`, then `.agents/skills`), configured custom roots, and the user roots (`$DSH_HOME/skills`, then `~/.agents/skills`), in that priority order. A skill is a directory bundle `<name>/SKILL.md` or a flat `<name>.md`; frontmatter carries `name` and `description`, plus optional `whenToUse` and `disable-model-invocation`.

## Subagent dispatch

`subagent` runs in the **background by default** and returns a durable agent id immediately; `subagent_fork` is the same, seeded with the current conversation. Start independent delegations together in one message and keep working while they run — set `run_in_background: false` only when your next action depends on that result. The runtime delivers a notice when a run settles, so never poll for one.

- `send_message` steers a running child at its nearest step, and starts a turn for an idle one; direct children only.
- `list_agents` shows your children with mode, activity, and lineage; `interrupt_agent` stops a descendant's current turn without destroying the child.
- Fill every `*-prompt.md` template completely before dispatching — the template carries the child's role, review criteria, and output format.
- Where the deployment exposes `provider`, `model`, or `reasoning_effort` on the schema, route the child deliberately per the skill's Model Selection rules. Where the schema exposes none, do not invent a route; the child inherits.
- For one or two delegations use plain `subagent`. `workflow` is for work your human partner asked to fan out, and it composes its own agents from a script rather than prompt templates.

## Task tracking

`todo_write` replaces the entire list on every call, so send every item rather than a delta. Statuses are `pending`, `in_progress`, and `completed`; mark each item complete as it finishes, and keep at least one `in_progress` while work remains.

## Plan mode

Plan mode explores and designs before execution, then presents the plan for approval; `exit_plan_mode` leaves it. That is where this skill's "before entering plan mode, brainstorm first" rule applies.

## Sandbox and approval

File and shell access run under the session's DSH sandbox mode, and a blocked operation reports a policy denial rather than failing quietly. When a command is denied and a wider mode would let it succeed, retry that exact command once with `sandbox_permissions` and a one-sentence `justification` — the approval prompt raised by that retry is how your human partner consents. If approval prompts are disabled for the session, a denial is final: stop and explain instead of working around it.
