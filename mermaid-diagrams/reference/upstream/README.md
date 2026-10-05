# Mermaid syntax docs (upstream, pinned)

Verbatim copies of the mermaid project's own syntax documentation, taken from
`mermaid-js/mermaid` at commit `97b3451` (mermaid 12.1.0) — the same pages served at
<https://mermaid.ai/open-source/intro/syntax-reference.html>. Upstream is MIT licensed;
these files exist so the skill works offline and greps fast.

**Grep, do not read wholesale** — the set is ~500 KB:

```bash
grep -rn "classDef" .
grep -n "cardinality" entityRelationshipDiagram.md
```

| File | Covers |
|---|---|
| `flowchart.md` | nodes, shapes, edges, subgraphs, classDef/style/linkStyle |
| `sequenceDiagram.md` | actors, arrows, notes, alt/loop/par, activation |
| `stateDiagram.md` | states, composite states, choice/fork/join, notes |
| `classDiagram.md` | classes, members, relations, generics, annotations |
| `entityRelationshipDiagram.md` | entities, attributes, cardinality |
| `mindmap.md`, `timeline.md`, `pie.md`, `quadrantChart.md` | the small structured types |
| `gantt.md`, `gitgraph.md` | scheduling and branch history |
| `c4.md`, `architecture.md`, `block.md`, `packet.md`, `kanban.md` | structural/system types |
| `xyChart.md`, `sankey.md`, `radar.md`, `treemap.md` | data-ish charts |
| `requirementDiagram.md`, `userJourney.md`, `venn.md`, `swimlanes.md`, `usecase.md`, `treeView.md`, `ishikawa.md`, `wardley.md`, `cynefin.md`, `eventmodeling.md`, `railroad.md`, `agentflow.md` | niche types — check the parser accepts them before shipping |
| `config-usage.md`, `config-configuration.md`, `config-directives.md`, `config-theming.md`, `config-accessibility.md` | configuration, themes, directives, a11y |

Unsupported in mermaid 12 despite upstream docs existing: `zenuml` (no diagram type is
detected by the bundled parser). Verify any niche type with `scripts/check-mermaid.mjs`
before using it; the checker is the authority, these docs are only the reference.
