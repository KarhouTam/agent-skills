# Mermaid cheatsheet (mermaid 12.1, every snippet parse-verified)

All snippets in this file were parsed with `scripts/check-mermaid.mjs`; if you edit one,
re-run the checker before trusting it. Deeper detail: `upstream/` (pinned mermaid docs).

## Which type

| Content | Type |
|---|---|
| Steps, branches, one flow | `flowchart TD` / `LR` |
| Messages between actors over time | `sequenceDiagram` |
| Lifecycle, statuses, modes | `stateDiagram-v2` |
| Types, fields, inheritance | `classDiagram` |
| Tables, keys, cardinality | `erDiagram` |
| Work against a calendar | `gantt` |
| Parts of a whole | `pie` |
| Hierarchy of ideas | `mindmap` |
| Chronology | `timeline` |
| Two-axis judgement | `quadrantChart` |
| Branch/merge history | `gitGraph` |
| System context, containers | `C4Context` / `C4Container` |
| Numeric trends | `xychart-beta` |
| Flow volumes | `sankey-beta` |
| Layout blocks | `block-beta` |
| Cloud/infra topology | `architecture-beta` |
| Board of work | `kanban` |

## flowchart

```mermaid
flowchart TD
  A["Client"] -->|"POST /orders"| B{"Valid?"}
  B -->|yes| C["Queue"]
  B -->|no| D["400 response"]
  C --> E["Worker"]
```

Shapes (`id` is what edges use; the shape holds the label):

| Syntax | Shape |
|---|---|
| `A[Text]` | rectangle |
| `A(Text)` | rounded |
| `A([Text])` | stadium / pill |
| `A[[Text]]` | subroutine |
| `A[(Text)]` | database / cylinder |
| `A((Text))` | circle |
| `A(((Text)))` | double circle |
| `A{Text}` | rhombus / decision |
| `A{{Text}}` | hexagon |
| `A[/Text/]` | parallelogram |
| `A[\Text\]` | parallelogram (alt) |
| `A[/Text\]` | trapezoid |
| `A[\Text/]` | trapezoid (alt) |
| `A>Text]` | asymmetric |

Edges: `-->` arrow, `---` open link, `-.->` dotted, `==>` thick, `~~~` invisible,
`--o` / `--x` circle and cross ends, `<-->` bidirectional.
Chain and fan out: `A --> B --> C`, `A --> B & C`.

Grouping, with a declared direction inside the group:

```mermaid
flowchart LR
  subgraph Edge["Edge tier"]
    direction TB
    CDN["CDN"] --> GW["API gateway"]
  end
  subgraph Core["Services"]
    API["Orders API"]
  end
  GW -->|"REST"| API
```

Styling — semantic classes, no theme directives:

```mermaid
flowchart LR
  A["Hot path"] --> B["External"]
  classDef hot fill:#ffe0b2,stroke:#e65100,stroke-width:2px
  classDef ext fill:#e3f2fd,stroke:#1565c0
  class A hot
  class B ext
  linkStyle 0 stroke:#e65100,stroke-width:2px
```

## sequenceDiagram

```mermaid
sequenceDiagram
  autonumber
  participant U as User
  participant A as API
  participant Q as Queue
  U->>A: POST /orders
  activate A
  A->>Q: publish order.created
  A-->>U: 202 Accepted
  deactivate A
  alt payment declined
    Q-->>U: notification
  else payment cleared
    Q-->>U: receipt
  end
  loop every 30s
    A->>Q: poll dead letters
  end
```

Arrows: `->>` solid with head, `-->>` dashed reply, `-)` open (fire and forget),
`-x` cross. `Note over A,B: text`, `Note right of A: text`. `break` and `par` also exist.

Phase bands inside a sequence diagram (the sequence-flavoured equivalent of a subgraph):

```mermaid
sequenceDiagram
  participant U as User
  participant A as API
  rect rgb(232, 245, 233)
    Note over U,A: happy path
    U->>A: POST /orders
    A-->>U: 201 Created
  end
  par audit trail
    A->>A: write audit log
  and metrics
    A->>A: increment counter
  end
```

`actor U as User` draws a stick figure instead of a box. `box … end` (documented upstream)
throws `Option is not defined` in mermaid 12.1.0 — use `rect` bands instead.

## stateDiagram-v2

```mermaid
stateDiagram-v2
  [*] --> Queued
  Queued --> Running: worker picks up
  state "Retrying (backoff)" as Retry
  Running --> Retry: transient error
  Retry --> Running: backoff elapsed
  Running --> Done: success
  Running --> Failed: retries exhausted
  Done --> [*]
  Failed --> [*]
  note right of Retry: max 3 attempts
```

Names with spaces need `state "In Progress" as IP` — unquoted `[*] --> In Progress`
silently creates two states. Composite and pseudo-states:

```mermaid
stateDiagram-v2
  [*] --> Active
  state Active {
    [*] --> Idle
    Idle --> Busy: request
    Busy --> Idle: done
  }
  state pick <<choice>>
  Active --> pick
  pick --> Archived: age > 30d
  pick --> Active: keep
```

`<<fork>>` / `<<join>>` also exist. Multi-line notes use an explicit terminator:

```mermaid
stateDiagram-v2
  [*] --> Failed
  note right of Failed
    retry is the per-job failure count,
    incremented on each failed run
  end note
```

`stateDiagram-v2` accepts `classDef` / `class` exactly like a flowchart.

## classDiagram

```mermaid
classDiagram
  direction LR
  class Order {
    +String id
    +Date createdAt
    +total() Money
  }
  class Customer {
    +String email
  }
  class OrderState {
    <<enumeration>>
    NEW
    PAID
    SHIPPED
  }
  Customer "1" --> "0..*" Order : places
  Order --> "1" OrderState : has
```

Relations: `<|--` inheritance, `*--` composition, `o--` aggregation, `-->` association,
`..>` dependency, `..|>` realization. Generics: `class Box~T~`. Interfaces: `<<interface>>`.

## erDiagram

```mermaid
erDiagram
  CUSTOMER ||--o{ ORDER : places
  ORDER ||--|{ LINE_ITEM : contains
  CUSTOMER {
    string id PK
    string email UK "unique, login"
  }
```

Cardinality: `||` exactly one, `o|` zero or one, `}|` one or more, `o{` zero or more.
Entity names with hyphens need quotes: `"ORDER-ITEM"`.

## mindmap, timeline, pie

```mermaid
mindmap
  root((Platform))
    Compute
      Pods
      Jobs
    Storage
      Postgres
      Object store
```

```mermaid
timeline
  title Release history
  2024 : v1.0 launch
  2025 : v2.0 (multi-region)
```

```mermaid
pie showData
  title Requests by region
  "us-east" : 60
  "eu-west" : 40
```

`pie` requires quoted labels — unquoted values are a parse error.

## gantt

```mermaid
gantt
  title Migration plan
  dateFormat YYYY-MM-DD
  axisFormat %b %d
  section Design
    Discovery :done, a1, 2025-01-06, 5d
    Review :active, after a1, 3d
  section Build
    Implementation :b1, after a1, 10d
    Cutover :milestone, after b1, 0d
```

## quadrantChart, xychart, gitGraph

```mermaid
quadrantChart
  title Effort vs impact
  x-axis Low effort --> High effort
  y-axis Low impact --> High impact
  quadrant-1 Do now
  quadrant-2 Plan
  quadrant-3 Drop
  quadrant-4 Delegate
  Point A: [0.2, 0.8]
```

```mermaid
xychart-beta
  title "Revenue by quarter"
  x-axis [Q1, Q2, Q3, Q4]
  y-axis "USD (k)" 0 --> 200
  bar [40, 80, 120, 190]
  line [40, 80, 120, 190]
```

```mermaid
gitGraph
  commit id: "init"
  branch develop
  commit id: "feature"
  checkout main
  merge develop tag: "v1.0"
```

## C4, block, architecture

```mermaid
C4Context
  title Checkout context
  Person(user, "Customer")
  System(shop, "Shop")
  System_Ext(psp, "Payment provider")
  Rel(user, shop, "Buys from")
  Rel(shop, psp, "Charges via", "HTTPS")
```

```mermaid
block-beta
  columns 3
  Client["Client"]:3
  Gateway["Gateway"] API["API"] Worker["Worker"]
  Gateway --> API
```

```mermaid
architecture-beta
  group cloud(cloud)[Cloud]
  service gw(server)[Gateway] in cloud
  service db(database)[Postgres] in cloud
  gw:R -- L:db
```

## Configuration

Prefer diagram-level styling (`classDef`, `style`, `linkStyle`) — it survives every renderer.
For global config, frontmatter beats the deprecated directive:

```mermaid
---
config:
  theme: base
  themeVariables:
    fontFamily: Inter
    primaryColor: "#e8f0fe"
  flowchart:
    curve: basis
    nodeSpacing: 40
---
flowchart LR
  A --> B
```

`%%{init: {"theme":"dark"}}%%` still parses (and malformed JSON inside it is silently
ignored — no error, no effect), but it is deprecated since mermaid v10.5 and hosted
renderers may strip it.

## Text, line breaks, escaping

- Line break inside a label: `A["line one<br/>line two"]`
- Inner double quotes: `A["say #quot;hi#quot;"]`
- Commas, semicolons, `#`, `%`, `<`, `>` are **safe unquoted** inside `[...]` (measured)
- ASCII `(` `)` are **not** safe unquoted — quote them
- Full-width punctuation (Chinese `（）`, `：`) is safe unquoted
- Markdown strings: `` A["`code`"] ``
