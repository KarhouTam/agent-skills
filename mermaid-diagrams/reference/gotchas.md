# Mermaid gotchas — measured, not folklore

Every entry below was produced by running the snippet through
`scripts/check-mermaid.mjs` against mermaid 12.1.0 (`securityLevel: 'strict'`, the
GitHub-like default). "FAIL" means the parser rejected it; "OK" means it parsed —
with a note where the *rendering* still surprises.

## Parse failures (fix these)

| Snippet | Result | Fix |
|---|---|---|
| `A[parse(data)]` | FAIL, caret at `(` | `A["parse(data)"]` |
| `A -->\|yes (fast)\| B` | FAIL | `A -->\|"yes (fast)"\| B` |
| `A --> B: hello` | FAIL — flowchart edges have no colon form | `A -->\|hello\| B` |
| `A -->\|a\|b\| C` | FAIL — unquoted `\|` inside label | `A -->\|"a \| b"\| C` |
| `end --> B` | FAIL — `end` is reserved as an id | rename the id; `A["end"]` is fine |
| `A["say "hi""]` | FAIL — nested raw quotes | `A["say #quot;hi#quot;"]` |
| `A["foo] --> B` | FAIL — unbalanced quote | close the quote |
| `A[foo]] --> B` | FAIL — extra bracket | one closing bracket per shape |
| `subgraph X` … (no `end`) | FAIL, caret at end of block | close every `subgraph` with `end` |
| two diagrams in one fence | FAIL — `flowchart TD … flowchart LR …` | one diagram per fence |
| `classDiagram` … `class A {` (no `}`) | FAIL | close the class body |
| `pie` with `A : 10` | FAIL — lexer error | quote labels: `"A" : 10` |
| `flowchart-v2 TD` | FAIL — that is the internal type name, not a keyword | `flowchart TD` |
| `zenuml` | FAIL — no diagram type detected in mermaid 12 | use `sequenceDiagram` |
| `box "Team"` … `end` in a `sequenceDiagram` | FAIL — `ReferenceError: Option is not defined`, thrown from inside mermaid 12.1.0's `parseBoxData` | use `rect rgb(...)` phase bands |

## Parses fine, renders wrong (the silent traps)

| Snippet | What actually happens |
|---|---|
| `stateDiagram-v2` + `[*] --> In Progress` | creates **two** states, `In` and `Progress`. Write `state "In Progress" as IP` |
| `api--gateway --> db` | `--` starts an edge: you get node `api`, an edge, node `gateway` |
| `%%{init: {"theme": base}}%%` (malformed JSON) | silently ignored — no error, no theme. Prefer `classDef` or frontmatter `config:` |
| duplicated `participant A as Alice` | parses without complaint; declare each participant once and you never have to find out which declaration won |
| `linkStyle 7 stroke:#c00` | edges are numbered in **declaration order** across the whole diagram (upstream: "the order number of when the link was defined"); add or move one edge and the style silently jumps to a different arrow. Re-derive the indices after every edit, or style nodes with `class` instead |
| an edge written **inside** a `subgraph` block | any node named on that line joins the group, even one defined earlier outside it. Measured: with `E --> F` outside and `F --> G` inside, `Prod` = `[G, F, H]`; moving `F --> G` below the `end` leaves `Prod` = `[G, H]`. Membership follows the edge line, not the node's first mention |
| long labels | nothing breaks the line for you — insert `<br/>` where the break should be |

## Safe unquoted (do not waste effort quoting)

Measured OK inside `[...]` / `(...)` labels, rendered as written:

`alpha, beta` · `a; b` · `x < y` (escaped to `x &lt; y`) · `issue #42` · `100%` ·
`R&D` · `port: 8080` (colon is only a problem on flowchart *edges*) ·
`调度器（Kubernetes）` (full-width punctuation) · `Deploy 🚀`

Node ids, unquoted: letters, digits, `_`, `-`, `.`, `/` all parse —
`api-gateway`, `api.v1`, `api/v1` are valid ids. A single `-` is fine; `--` is an edge.

## Escaping and line breaks

- Line break inside a label: `A["line one<br/>line two"]`
- Inner quotes: `#quot;` (or use single quotes inside a double-quoted label)
- Markdown in labels: `` A["`code`"] `` (mermaid ≥ 10.2)
- Raw HTML: only simple tags survive `securityLevel: 'strict'` (what GitHub uses).
  Keep to `<br/>`, `<b>`, `<i>`; `<script>`/event handlers are stripped.

## What the checker cannot tell you

`mermaid.parse()` proves the grammar accepts the text. It does **not** verify layout:
crossed edges, overlapping labels, a subgraph taller than the page, or a colour that
disappears on a dark theme all parse perfectly. Those are the Step 5 review pass in
`SKILL.md` — and the reason a rendering environment (mermaid live editor, GitHub preview,
`mmdc` with Chromium) is still worth a look when the stakes are high.

It also cannot see renderer differences: a self-hosted mermaid version, GitHub's pinned
version, and Obsidian may each support a different subset. Sticking to
`flowchart`/`sequenceDiagram`/`stateDiagram-v2`/`classDiagram`/`erDiagram` — plus
`classDef` rather than `%%{init}%%` — is the portable subset.

## Reproducing these results

`reference/gotchas-cases.md` is the probe corpus behind every row above —
14 deliberately broken snippets and 13 that must keep parsing:

```bash
cd ~/.agents/skills/mermaid-diagrams
node scripts/check-mermaid.mjs reference/gotchas-cases.md   # exit 1 is expected
printf 'flowchart TD\n  A[parse(data)] --> B\n' | node scripts/check-mermaid.mjs -
```

The version is pinned in the script (`mermaid@12`, `jsdom@29`); re-run the corpus after
bumping it, since the grammar does change between majors.
