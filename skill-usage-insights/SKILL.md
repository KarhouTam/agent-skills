---
name: skill-usage-insights
description: Turns the usage data that this repo's Claude Code hooks collect into a dated insight — which skills each prompt triggered, tokens per skill run, whether declared sub-skills fired, which scripts fail and which files nobody reads — and records a keep / update / retire decision per skill. Never triggered automatically; the user invokes it explicitly.
disable-model-invocation: true
---

# Skill Usage Insights

## Overview

Two halves. **Hooks** (`hooks/`) record every skill activation, the files and scripts
a skill touches, and per-turn token usage from the transcript, into
`<data>/sessions/<session>.jsonl`. **This skill** turns those records into
`insights/<date>.md` and then judges them: one decision per skill that needs one.

The script does the counting and the agent does the judgment. Every number in an
insight comes from `analyze.py`. The agent's job is the `## Decisions` section, and
each decision must cite numbers that are in the file.

`<base>` is this skill's directory, as reported by the harness.

## Step 0 — Is anything being collected?

```bash
python3 <base>/scripts/install_hooks.py --status
```

Exit 0 means every hook is wired. Anything else means collection is off or partial.
Tell the user, show them the install command, and **ask before running it**: it
rewrites `~/.claude/settings.json` (it makes a backup first and leaves other hooks
alone).

```bash
python3 <base>/scripts/install_hooks.py            # --dry-run to preview, --uninstall to remove
```

A fresh install has no history. Say so and stop. An insight built on minutes of
data is noise.

## Step 1 — Build the insight

```bash
python3 <base>/scripts/analyze.py --since 30d      # or the window the user asked for: 7d, 2w, 2026-09-01
```

It writes `insights/<today>.md`, saves a snapshot for next time's Δ column, and
prints the flags. Re-running the same day refreshes the numbers and keeps any
Decisions already written. `--stdout` and `--json` write nothing. Use `--json` to
dig into one skill's runs.

Read the whole insight before you judge anything. If the header says **Thin
evidence**, decisions are limited to **Watch** and **Update**.

## Step 2 — Judge each flag

Each flag is a lead, not a verdict. Check it against the section it points to, and
open the skill's `SKILL.md` before you recommend changing it.

| Flag | Look at | Usual decision |
|---|---|---|
| `model-blocked` | Errors: the model reached for a `disable-model-invocation` skill | Usually **Update**: allow model invocation (frontmatter and `agents/openai.yaml` together, per AGENTS.md). Keep it manual if the skill has side effects |
| `activation-failures` | The error text | **Update**: wrong name, broken frontmatter, missing install |
| `script-failing` | Scripts and files → last error | **Update** that script. Name the failure mode |
| `abandoned` | Prompts → skills for the interrupted runs | **Update** if the skill derails the turn. **Watch** if the prompts were simply abandoned |
| `sub-skill-gap` | The gated-on text and the missed prompts under Sub-skills | Decide per miss whether the condition held (see Step 3). It held → **Update** the REQUIRED SUB-SKILL line. It didn't → the gap is correct, and you say so |
| `sub-skill-bypassed` | Child scripts ran but the child skill never loaded | **Update** the parent to load the child, or accept the shortcut on purpose |
| `under-triggered` | A model-invocable skill that only ever ran via `/` | **Update** the description so its trigger phrases match the real prompts listed |
| `costly` | Tokens/run, Load, Subagent share | **Update**: trim SKILL.md (high Load), cut subagent fan-out (high share), or move detail into `reference/` |
| `changed` | Versions: tokens and interruptions before and after the edit | Report whether the edit helped. That closes the loop on the last update |
| `undeclared-pair` | A child that always follows a parent | **Update** the parent with a REQUIRED SUB-SKILL line, or explain why the pair is a coincidence |
| `retire?` | Zero runs over ≥ 14 days, plus the listing cost it pays every turn | **Retire**, or **Watch** when the skill is rare by design (manual workflows such as `pytorch-test-refactoring`) |
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

## Step 4 — Write the decisions

Replace the comment under `## Decisions` in the insight with one line per skill that
needs a decision, most urgent first. A skill with nothing to report gets no line.

```markdown
- **Update** `deepwiki` — `scripts/deepwiki.py` failed 5/12, all `Exit code 1` timeouts; add one retry with backoff.
- **Update** `teaching-technical-topics` — `deepwiki` loaded in 2/9 runs; 5 of the 7 misses were Standard lessons about public repos, so the condition held; make the line unconditional for Standard/Deep.
- **Retire** `context7-mcp` — 0 runs in 42 days; ~66 listing tokens on every turn; `tavily-web-access` covered the 3 library-doc prompts.
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
| any | Reversible soft retire: `"skillOverrides": {"<skill>": "off"}` (or `"name-only"` to keep `/` access) in `~/.claude/settings.json` |

To update a public skill, use an overlay. Never edit it in place (AGENTS.md).

## Data, privacy, limits

- Data lives in the first writable of `$SKILL_USAGE_DATA_DIR`, `<base>/data/`, and
  `~/.local/share/skill-usage-insights/`. Both `data/` and `insights/` are gitignored:
  they hold prompt previews.
- `SKILL_USAGE_PROMPT_CHARS=0` stops recording prompt text. `SKILL_USAGE_DISABLE=1`
  pauses collection without uninstalling. Hook errors go to `<data>/errors.log`, and
  the insight reports how many there were.
- A run's lifecycle ends with its turn. Later turns where the skill is still in
  context are not attributed to it. Claude Code is the only harness covered: Codex
  sessions and cloud sessions (which skip `~/.claude/settings.json`) leave no data.
- For record kinds, the attribution rules and the known blind spots, read
  `reference/data-model.md`.

## Common mistakes

| Mistake | Correction |
|---|---|
| Recommending retirement from a few days of data | No **Retire** under 14 days covered. Use **Watch** |
| Treating every sub-skill miss as a bug | Most REQUIRED SUB-SKILL lines are conditional. Check the condition before you call it a gap |
| Retiring a manual skill for being rare | `disable-model-invocation` skills are rare by design. Weigh what a run is worth, not how often it happens |
| Decisions with no numbers | Each line cites a row of the insight |
| Editing or deleting skills straight away | Report first. Act only after the user confirms |
| Reading every SKILL.md body for the trigger review | Descriptions are what trigger a skill. Read the frontmatter only |
| Hand-editing the tables | Re-run `analyze.py`. Only `## Decisions` is written by hand |
