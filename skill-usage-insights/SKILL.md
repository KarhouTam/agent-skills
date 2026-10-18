---
name: skill-usage-insights
description: Turns the usage data this repo's collectors record into a dated insight — which skills each prompt triggered, tokens per skill run, whether declared sub-skills fired, which scripts fail and which files nobody reads — and records a keep / update / retire decision per skill. Covers Claude Code (hooks) and dsh (session logs). Never triggered automatically; the user invokes it explicitly.
disable-model-invocation: true
---

# Skill Usage Insights

## Overview

Two halves. **Collectors** record every skill activation, the files and scripts a
skill touches, and token usage, into `<data>/sessions/<session>.jsonl`. **This
skill** turns those records into `insights/<date>.md` and then judges them: one
decision per skill that needs one.

| Harness | How usage is collected | What to install |
|---|---|---|
| dsh | `hooks/collect_dsh.py` decodes `$DSH_HOME/sessions/**/session.v3.jsonl.zstd` | nothing |
| Claude Code | `hooks/collect.py` runs as a hook on every event | `scripts/install_hooks.py` |
| Codex | `hooks/collect_codex.py` runs as a hook; its rollout log (`~/.codex/sessions/**/rollout-*.jsonl`) supplies tokens and activations | `scripts/install_hooks.py`, then trust the handlers once with `/hooks` |

All three write the same record kinds, so one insight covers every harness and
nothing in the analysis is harness-specific. `analyze.py` collects new dsh logs
itself before it reads, so on dsh the only command you need is Step 1.

The script does the counting and the agent does the judgment. Every number in an
insight comes from `analyze.py`. The agent's job is the `## Decisions` section, and
each decision must cite numbers that are in the file.

`<base>` is this skill's directory, as reported by the harness.

## Step 0 — Is anything being collected?

```bash
python3 <base>/hooks/collect_dsh.py --status      # dsh: is the log corpus readable?
python3 <base>/scripts/install_hooks.py --status  # every hook harness found here: wired or not?
```

Under dsh, `--status` exits 0 when the corpus is usable and prints something like:

```
dsh sessions root  /root/.dsh/sessions
node decoder       available
session logs       118
not yet collected  1
```

Exit 1 with `node decoder MISSING` means the `node` on PATH predates zstd in
`node:zlib` (dsh ships its own recent Node, so also try that one). Exit 1 with
`session logs 0` means this machine has never run dsh. Either way, say so and stop —
there is nothing to build an insight from.

A non-zero `not yet collected` is normal and needs no action: Step 1 collects.

The installer has no harness flag: it looks for each harness on this machine and
wires the ones it finds, skipping the rest with a one-line reason. Exit 0 means
every harness it found is wired. Anything else means collection is off or partial:
tell the user, show them the command, and **ask before running it**, because it
rewrites up to three files — `~/.claude/settings.json`, `~/.codex/hooks.json`, and
`$DSH_HOME/cordis.patch.yml`. Each is backed up first and other hooks are left alone.

```bash
python3 <base>/scripts/install_hooks.py            # --dry-run to preview, --uninstall to remove
```

Codex keeps its hook set in `~/.codex/hooks.json` and **runs nothing until you
trust it**: run `/hooks` once in Codex, review the handlers, and trust them. Until
then a Codex session records nothing, which looks exactly like a quiet day.

### The dsh bridge is not required

dsh is measured from its own session logs, so it needs no hook. The installer
mounts the live bridge only when the dsh install actually ships
`@deepseek-ai/dsh-hooks-claude-code`; otherwise it says so and skips. Leave it
that way unless you are debugging live capture: the bridge records the events the
log already holds, and its `prompt_id` is not the log's turn number, so a prompt
can show up twice.

A fresh install has no history. Say so and stop. An insight built on minutes of
data is noise.

## Step 1 — Build the insight

```bash
python3 <base>/scripts/analyze.py --since 30d      # or the window the user asked for: 7d, 2w, 2026-09-01
```

It collects any new dsh logs (`--no-collect` skips that), writes `insights/<today>.md`,
saves a snapshot for next time's Δ column, and prints the flags. Re-running the same
day refreshes the numbers and keeps any Decisions already written. `--stdout` and
`--json` write nothing. Use `--json` to dig into one skill's runs.

Read the whole insight before you judge anything. If the header says **Thin
evidence**, decisions are limited to **Watch** and **Update**.

## Step 2 — Judge each flag

Each flag is a lead, not a verdict. Check it against the section it points to, and
open the skill's `SKILL.md` before you recommend changing it.

| Flag | Look at | Usual decision |
|---|---|---|
| `model-blocked` | Errors: the model reached for a `disable-model-invocation` skill | Usually **Update**: allow model invocation (`disable-model-invocation` in frontmatter and `agents/openai.yaml` together, per AGENTS.md). Keep it manual if the skill has side effects |
| `activation-failures` | The error text | **Update**: wrong name, broken frontmatter, missing install |
| `script-failing` | Scripts and files → last error | **Update** that script. Name the failure mode |
| `abandoned` | Prompts → skills for the interrupted runs | **Update** if the skill derails the turn. **Watch** if the prompts were simply abandoned |
| `sub-skill-gap` | The gated-on text and the missed prompts under Sub-skills | Decide per miss whether the condition held (see Step 3). It held → **Update** the REQUIRED SUB-SKILL line. It didn't → the gap is correct, and you say so |
| `sub-skill-bypassed` | Child scripts ran but the child skill never loaded | **Update** the parent to load the child, or accept the shortcut on purpose |
| `under-triggered` | A model-invocable skill that only ever ran via `/` | **Update** the description so its trigger phrases match the real prompts listed |
| `costly` | Tokens/run, Cache read/run, Load, Subagent share | **Update**: trim SKILL.md (high Load), cut subagent fan-out (high share), or move detail into `reference/`. A high cache-read share with a normal Load is the session's context, not the skill |
| `changed` | Versions: tokens and interruptions before and after the edit | Report whether the edit helped. That closes the loop on the last update |
| `undeclared-pair` | A child that always follows a parent | **Update** the parent with a REQUIRED SUB-SKILL line, or explain why the pair is a coincidence |
| `retire?` | Zero runs over ≥ 14 days, plus what the catalog spends on it | **Retire**, or **Watch** when the skill is rare by design (manual workflows such as `pytorch-test-refactoring`) |
| `dead-weight?` | Untouched shipped files | **Update**: delete or merge them, but only if they are not upstream copies (public tier) |

Only flag a skill yourself when you can point to a specific row in the insight that
backs it.

## Step 3 — Two reviews the script cannot do

Both read prompt **previews** (the first 200 characters). Treat each conclusion as a
hypothesis and label it that way.

1. **Triggering.** Read the frontmatter `description` of every model-invocable skill
   (read the frontmatter only, not the bodies). Then go through "Prompts that ran no
   skill". Name **at most five** prompts that clearly matched a description that never
   fired. Then go through "Prompts → skills" for runs that should not have happened
   (false positives). Two or more misses for the same skill is an **Update** to its
   description. A single miss is only a note.
2. **Sub-skill conditions.** For each missed parent run listed under Sub-skills,
   compare the prompt with the gated-on text. Example: `teaching-technical-topics` →
   `eli5` applies only when the prompt names an ELI level, so a run whose prompt names
   no level was **correct** to skip it. Count real misses separately from correct skips.

On dsh, a subagent's prompts are its dispatcher's text, not the user's, so they are
deliberately absent from the prompt tables. Judge sub-skill behaviour by the parent
turn's prompt.

## Step 4 — Write the decisions

Replace the comment under `## Decisions` in the insight with one line per skill that
needs a decision, most urgent first. A skill with nothing to report gets no line.

```markdown
- **Update** `deepwiki` — `scripts/deepwiki.py` failed 5/12, all `Exit code 1` timeouts; add one retry with backoff.
- **Update** `teaching-technical-topics` — `deepwiki` loaded in 2/9 runs; 5 of the 7 misses were Standard lessons about public repos, so the condition held; make the line unconditional for Standard/Deep.
- **Retire** `context7-mcp` — 0 runs in 42 days; listed in 95 catalogs at ~1.7k tokens each; `tavily-web-access` covered the 3 library-doc prompts.
- **Watch** `pytorch-test-refactoring` — 1 run in 30 days, manual by design; revisit next month.
- **Keep** `mermaid-diagrams` — 14 runs, 0 script failures; the edit on 10-02 halved tokens/run (8.1k → 3.9k).
```

The verbs are **Keep**, **Update**, **Retire**, and **Watch**. Every line names its
evidence. An Update says what to change. A Retire says what replaces the skill, or
that nothing needs to.

## Step 5 — Report, then wait

Tell the user the insight's path and its three to five most important decisions. **Do
not apply any decision** until the user confirms. When they do, retiring depends on
the skill's tier (Tier column):

| Tier | Retire by |
|---|---|
| `self` | Delete `<skill>/`, its `!/<skill>/` line in `.gitignore`, and its README entry |
| `public` | Remove the entry from `skills.json` and delete the directory. Drop `overlays/<skill>/` if one exists |
| any | Reversible: add `disable-model-invocation: true` to its frontmatter so the harness stops advertising it, and keep `/` access. Claude Code can instead set `"skillOverrides": {"<skill>": "off"}` (or `"name-only"`) in `~/.claude/settings.json` |

To update a public skill, use an overlay. Never edit it in place (AGENTS.md).

## Data, privacy, limits

- Data lives in the first writable of `$SKILL_USAGE_DATA_DIR`, `<base>/data/`, and
  `~/.local/share/skill-usage-insights/`. Both `data/` and `insights/` are gitignored:
  they hold prompt previews.
- `SKILL_USAGE_PROMPT_CHARS=0` stops recording prompt text. `SKILL_USAGE_DISABLE=1`
  pauses collection without uninstalling. Collection errors go to `<data>/errors.log`,
  and the insight reports how many there were.
- A run's lifecycle ends with its turn. Later turns where the skill is still in
  context are not attributed to it, and `analyze.py` says how many subagents could not
  be joined to a parent.
- Hook records and log records are complete only for sessions the machine still holds:
  dsh keeps its logs under `$DSH_HOME/sessions` and Codex under `~/.codex/sessions`, so
  a deleted log is a deleted record. Cloud sessions that skip the settings file leave no
  data, and untrusted Codex hooks leave none at all.
- For record kinds, the attribution rules and the known blind spots, read
  `reference/data-model.md`.

## Common mistakes

| Mistake | Correction |
|---|---|
| Recommending retirement from a few days of data | No **Retire** under 14 days covered. Use **Watch** |
| Treating every sub-skill miss as a bug | Most REQUIRED SUB-SKILL lines are conditional. Check the condition before you call it a gap |
| Retiring a manual skill for being rare | `disable-model-invocation` skills are rare by design. Weigh what a run is worth, not how often it happens |
| Reading a token number without its harness | dsh counts cache reads as context re-read each step, so `Cache read/run` dominates there; Load and Out/run are the comparable columns |
| Decisions with no numbers | Each line cites a row of the insight |
| Editing or deleting skills straight away | Report first. Act only after the user confirms |
| Reading every SKILL.md body for the trigger review | Descriptions are what trigger a skill. Read the frontmatter only |
| Installing the Codex hooks and assuming they run | Codex skips untrusted hooks silently; say "run `/hooks` and trust them" in the report |
| Comparing Tokens/run without the Auth column | A run in an authoring session is maintenance; the cost flags already exclude it, and the column says how many |
| Hand-editing the tables | Re-run `analyze.py`. Only `## Decisions` is written by hand |
