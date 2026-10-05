# Testing this skill (RED → GREEN evidence)

Written 2026-10-05 against mermaid 12.1.0. Raw outputs live in
`../../.baseline/mermaid/` (`b*` = before, `green/g*` = after).

## Method

Seven identical diagram-writing prompts were given to fresh subagents twice: once with no
skill (`b1`–`b7`), once with this skill loaded and followed (`g1`–`g7`). Every artefact was
then scored with `scripts/check-mermaid.mjs`. Prompts: CI/CD pipeline, OAuth+PKCE sequence,
TCP lifecycle, library class/state model, Kubernetes request flow, Python control flow,
23-component platform architecture.

## Result

| | Baseline (no skill) | With skill |
|---|---|---|
| Blocks produced | 11 | 12 |
| Blocks that parse | 11/11 | 12/12 |
| Agents that actually ran a parser | 2/7 (one self-installed it, one found the checker mid-run) | 7/7 |
| Largest single flowchart | 23 nodes, ~50 edges (one hairball) | 15 nodes max (split into two views) |
| Stated node budget respected | — | 7/7 |
| Competing duplicate variants shipped | yes (b1) | no |

**The syntax axis did not separate the two groups** — five baseline agents produced valid
mermaid on the first try and admitted in their write-ups that they were guessing. The
separation is in evidence and shape:

- **Verification.** One baseline agent installed mermaid on its own initiative; one later
  found the checker that had appeared in the workspace mid-run and used it. The other five
  wrote hedges like "manual syntax review only, not a render test" and "could not
  render-verify locally". With the skill, all seven ran the checker and quoted its output.
- **Complexity.** The 23-component prompt became a 23-node/50-edge diagram at baseline and
  two diagrams of 15 + 12 nodes with the skill — same content, readable shape.
- **Faithfulness.** The library baseline invented an enumeration class and a cancel
  transition the user never asked for; the skill run kept exactly the four requested classes.

## Repair path (added after the seven authoring runs)

An eighth subagent was given a deliberately broken block (two unquoted `(`, an unterminated
label, a `subgraph` with no `end`) plus the sentence a user would actually write: "this shows
a red error box in our README". It ran the checker, fixed the errors one at a time, and
reported the pre-fix output (`0/1 parsed`, caret at the `(`) next to the post-fix output
(`1/1 parsed`, exit 0). It also caught a defect the checker cannot see — a node pulled into a
`subgraph` by an edge line inside the block — which is now documented in `reference/gotchas.md`.

## What this means for maintenance

The skill earns its keep by making verification non-optional and by capping diagram size,
not by teaching mermaid grammar the model already knows. Its measured facts
(`reference/gotchas-cases.md`) are the part that decays: re-run that corpus after any
mermaid version bump, and re-run the seven prompts if the workflow steps change.
