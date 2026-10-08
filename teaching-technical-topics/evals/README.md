# Evals

Two halves: `check_run.py` asserts everything mechanical about a run, and a human
reads the answer for the rest. The point is to be able to tell a SKILL.md edit that
helped from one that regressed.

## Running a scenario

1. Pick a scenario from `prompts.md` and run it against a fresh agent with this
   skill loaded. For scenario 4, snapshot the notes root first:
   `cp -r <notes> /tmp/notes-before`.
2. Save the answer verbatim to a file.
3. Score it:

```bash
python3 evals/check_run.py \
    --answer /tmp/answer.md \
    --notes <notes-root> \
    --mode standard \
    [--notes-before /tmp/notes-before]
```

Exit code 0 means every mechanical assertion held. The script uses the
mermaid-diagrams skill's parser for diagram checks; point `--mermaid-check` at it
if it lives somewhere unusual.

Add `--expect-deepwiki` when the run had DeepWiki reachable (scenario 9, or any
run you want to hold to the map-not-source rule).

## What is asserted

| Assertion | Scenario |
|---|---|
| `## In a nutshell` is the first heading, one sentence | all |
| 正文 is predominantly Chinese regardless of the question's language | all |
| At most 2 mermaid blocks, and every one parses | all |
| `## 来源` present, with `file::symbol` or an explicit `unverified` | standard / deep |
| `## 来源` points at the DeepWiki map, and a local `file::symbol` survives | `--expect-deepwiki` |
| No note names DeepWiki in its `verified_against` | all |
| `## 延伸` present with at most 4 items | standard / deep |
| Quick mode stays short and writes nothing to the notes root | quick |
| `note.py check` passes (links, frontmatter, INDEX) | all |
| Every notebook ran — each code cell has outputs | all |
| The lesson is still a draft, not promoted, before the user confirms | `--stage draft --topic project/topic` |
| After confirmation the note is active, in INDEX, and the draft is gone | `--stage promoted --topic project/topic` |

The two lifecycle assertions are the point of the draft step: an unconfirmed lesson
must never reach `INDEX.md`, and a confirmation must actually consume the draft.

## Recorded no-skill baseline

Measured before this skill existed (2026-10-05), same prompts, no skill loaded:

| | vLLM scheduler | PyTorch dispatcher |
|---|---|---|
| Size | 178 lines / 837 words | 592 lines / 2252 words |
| Mermaid blocks | 0 — ASCII art in a plain fence | 0 — ASCII "全景图" in a plain fence |
| Opening summary | `## 一、一句话定义` | `> **一句话**:` blockquote |
| Demo | none | 8 inline python snippets, no notebook |
| Notes written | none | none |

A third baseline run in a clean workspace produced 10 loose `.py` scratch files, no
`.ipynb` and no notes.

Provenance caveat: the dispatcher baseline answer file was later overwritten by its
own still-running agent once this skill appeared in that agent's catalog mid-session.
The figures above are from the analysis taken while the original file was intact.
Baselines 1 and 3 are unmodified.

Re-measure rather than trusting these numbers after any significant change.

## Recorded DeepWiki baseline (2026-10-08)

Scenario 9 (PyTorch Dynamo guards, local checkout `2e9b4aff8d4`, deepwiki MCP
reachable), same prompt and machine for both arms. The pre-change SKILL.md was
frozen (`e0fa8d4`) and run from a copy, so the arms differ only in the skill:

| | before the DeepWiki step | after |
|---|---|---|
| `check_run.py --expect-deepwiki` | 16/17 — no `deepwiki:` pointer | 17/17 |
| DeepWiki consulted | yes, as a cross-check; cited by search URL | yes; cited as `deepwiki:pytorch/pytorch` |
| `read_wiki_contents` called | no | no |
| Wiki/source divergence surfaced | not reported | reported — the run says the wiki put `RootGuardManager` in `guards.h` where the local source has it in `guards.cpp` |
| Draft note / notebook | `pytorch/dynamo-guards`, `verified_against: torch@2.15.0a0+git2e9b4af`, every code cell executed | same |

Two caveats, both measured rather than assumed:

- **Discovery is not the delta.** Once the MCP server is connected, its instructions
  are injected into the system prompt and the pre-change arm found the tool on its
  own. What the step changed mechanically was the citation form, plus making the
  map-not-source and report-the-divergence rules explicit. A plan-only probe
  (2 reps per arm: "which actions would you take?") did separate the arms: 0/2
  mentioned DeepWiki before, 2/2 planned `read_wiki_structure` →
  `ask_wiki_question` → open every named `file::symbol` locally after.
- **One repo, one model, one day.** Re-measure before trusting these numbers.

### Split into a standalone skill, then narrowed to one transport

Same scenario 9 prompt, once the procedure moved out of this skill into its own
skill, linked with a REQUIRED SUB-SKILL line:

| | pre-split | post-split |
|---|---|---|
| scenario 9, `check_run.py --expect-deepwiki` | 17/17 | 17/17 |
| whole-wiki dump calls | 0 | 0 |
| wiki/source divergence reported | yes | yes |
| plan probe, 2 reps: loads the linked skill and plans page tree + one question | n/a (inline) | 2/2 |
| plan probe: respects the whole-wiki ban | 1/2 | 2/2 |

The ban was made unconditional between those two rows: it had a "small repos only"
escape hatch, and a planner took it. There is no size preview, so the choice has to
be made before the tokens land — the escape hatch was the whole problem.

The standalone skill was probed on its own subject as well: a review question about
prefix-cache block hashing in `vllm-project/vllm` with no local checkout drove it
through page tree → one question → pin a ref → fetch the named files → label every
symbol `verified-in-source` or `unverified`. It reported the wiki's
"`BlockHashToBlockMap` is nested in `BlockPool`" as wrong (module-level,
`block_pool.py:35`, held at `:181`). Every line number it reported was re-checked
against `raw.githubusercontent` at `db9527a4` and all matched.

**Final shape: one transport, no harness awareness.** An intermediate revision had
the skill check whether the harness had the DeepWiki MCP server, offer to install it,
and fall back to the script — probes measured that branch working (2/2 asked before
writing any config; a DSH-variant probe reproduced `$DSH_HOME/cordis.patch.yml`,
merge-not-overwrite, `--dump-config`). It was then removed and the skill renamed
`deepwiki-mcp` → `deepwiki`, because the server exposes three tools and nothing else:
`prompts/list`, `resources/list` and `resources/templates/list` all return empty, and
the script covers the two the skill uses (the third is the banned dump). So the
harness branch bought no capability while making the skill carry other products'
config syntax and a teaching-flow `_project.md` convention. The harness entries
themselves stay — they are useful on their own; the skill no longer knows about them.
