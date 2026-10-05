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

## What is asserted

| Assertion | Scenario |
|---|---|
| `## In a nutshell` is the first heading, one sentence | all |
| 正文 is predominantly Chinese regardless of the question's language | all |
| At most 2 mermaid blocks, and every one parses | all |
| `## 来源` present, with `file::symbol` or an explicit `unverified` | standard / deep |
| `## 延伸` present with at most 4 items | standard / deep |
| Quick mode stays short and writes nothing to the notes root | quick |
| `note.py check` passes (links, frontmatter, INDEX) | all |
| Every notebook ran — each code cell has outputs | all |

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
