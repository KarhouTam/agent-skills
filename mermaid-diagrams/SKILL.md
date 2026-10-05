---
name: mermaid-diagrams
description: Use when writing, fixing, or reviewing a Mermaid diagram in markdown or docs — flowcharts, sequence diagrams, state machines, class/ER diagrams, gantt, mindmap, timeline and friends — including "mermaid syntax error", "diagram won't render", "this diagram is unreadable", or any request to draw a process, architecture, schema, or lifecycle.
---

# Mermaid Diagrams

## Overview

A diagram ships only if it **parses**, **reads at a glance**, and **says one thing**.
Those are three independent checks, and the first one is not optional: an unparsed block
reaches the user as a red error box.

Paths below resolve against this skill's base directory (reported when the skill loads;
normally `~/.agents/skills/mermaid-diagrams/`). Call that `<base>`.

**Core rule: never hand over a mermaid block you have not parsed.**

## Step 1 — Pick the type from the shape of the content

| The content is | Use | Instead of |
|---|---|---|
| Steps, branches, one flow | `flowchart TD` / `flowchart LR` | |
| Messages between actors over time | `sequenceDiagram` | a flowchart with arrows both ways |
| One object's lifecycle or statuses | `stateDiagram-v2` | a flowchart of states |
| Types, fields, inheritance | `classDiagram` | |
| Tables, keys, cardinality | `erDiagram` | |
| Work against a calendar | `gantt` | |
| Parts of a whole | `pie` | |
| Hierarchy of ideas | `mindmap` | |
| Chronology of events | `timeline` | |

Pick one. If the answer needs two views ("the architecture" *and* "how a request flows"),
that is two diagrams, each with its own caption. For anything not in the table, check the
larger index in `<base>/reference/cheatsheet.md`; failing that, `flowchart` is the
general-purpose fallback.

## Step 2 — Sketch before you type

Write the node list, then the edge list, in plain text. Then:

- **≤ 15 nodes per flowchart.** More means the diagram answers two questions — split by layer
  or subsystem, or promote the detail into a subgraph of a second diagram.
- Every edge in a non-trivial flowchart carries a label. Unlabeled arrows are ambiguity.
- Name each node as the reader's term for the thing, not as your implementation detail.

## Step 3 — Write the block, obeying four rules

1. **Quote any label containing ASCII punctuation that could be syntax:**
   `A["parse(data)"]`, `B["port: 8080"]`, `C -->|"yes (fast)"| D`.
   Unquoted `(` in `A[parse(data)]` and in `-->|yes (fast)|` is a parse error (measured).
2. **Edge labels use pipes, never a colon:** `A -->|retry| B`. `A --> B: retry` is a parse error.
3. **`end` is reserved** — never a bare node id (`end --> B` fails; `A["end"]` is fine).
   Every `subgraph` needs its own `end`, and one fence holds exactly one diagram.
4. **Balance quotes and brackets.** Inner double quotes become `#quot;`; line breaks are `<br/>`.

Full measured failure list: `<base>/reference/gotchas.md`.

## Step 4 — Verify (mandatory, before the user ever sees it)

```bash
node <base>/scripts/check-mermaid.mjs path/to/doc.md     # or: ... - < snippet.mmd
```

It parses every ```mermaid block with the real mermaid 12 parser (installing it into a
cache dir on first use) and prints `OK file:line [type]` or `FAIL` with the offending
source line and the parser's caret. **Read the caret, fix, re-run.** The parser stops at
the first error, so a block with three mistakes takes three passes. Zero FAILs and
`N/N mermaid block(s) parsed` is the only acceptable end state — never "should render fine".

## Step 5 — Make it beautiful: the review pass

- **Direction matches the story.** `TD` for hierarchy and top-down flow, `LR` for pipelines,
  stages, and anything with a time axis. Mixed directions inside one graph are a smell.
- **Group, then label the groups.** A `subgraph` per subsystem, 3–7 members each, ≤ 2 levels
  deep. Groups are what turn a hairball into a map. Membership follows the edge lines inside
  the block, so write cross-group edges after the `end` or the neighbouring node joins the group.
- **Short node text, verbs on edges.** Noun phrase in the node, verb phrase on the arrow.
  `<br/>` for a second line (image tag, port), not for a paragraph.
- **Colour is semantic, not decorative.** At most three `classDef`s, each meaning something
  (hot path / external / deprecated), mid-tone fill with a darker stroke so it reads on
  GitHub's light *and* dark themes. Same meaning ⇒ same class, always. In a
  `sequenceDiagram` the equivalent is a `rect rgb(...)` band around a phase.
- **Prefer `classDef` over themes.** `%%{init: …}%%` is deprecated since v10.5 and hosted
  renderers may strip it; `classDef`/`style` are plain diagram syntax and work everywhere.
- **No decoration.** No emoji confetti, no rainbow palette, no legend that restates labels.
- **One sentence of prose next to the fence** stating the takeaway. The diagram carries
  structure; the sentence carries the point.

## Step 6 — Deliver

Diagram plus a one-line caption. Do not ship two competing variants of the same diagram
unless the user asked for options — pick the better one and say why in a clause.

## Common mistakes

| Symptom | Fix |
|---|---|
| `Parse error on line N` with caret at `(` | Quote the label: `A["f(x)"]`, `-->\|"f(x)"\|` |
| `A --> B: label` fails | Use `A -->\|label\| B` |
| Diagram silently splits a name (`In Progress` → two states) | Quote it: `state "In Progress" as IP` |
| Renderer shows a blank/garbled box | Remove raw HTML beyond `<br/>`; re-run the checker |
| Two diagrams in one block | One fence per diagram |
| Missing `end` | Close every `subgraph`; count them |
| Spaghetti after ~20 nodes | Split by layer; the second diagram gets its own caption |

## Reference

- `<base>/reference/cheatsheet.md` — verified minimal snippets per diagram type + styling toolbox
- `<base>/reference/gotchas.md` — every measured parse failure and silent-wrong-render trap
- `<base>/reference/upstream/` — full mermaid syntax docs, pinned at commit `97b3451`; grep it
  (`grep -rn "classDef" <base>/reference/upstream/`) rather than reading it whole
