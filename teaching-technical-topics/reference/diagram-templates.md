# Verified mermaid templates

Every snippet below was parse-checked against mermaid 12. Adapt them rather than
inventing structure. Validate anything you change with the mermaid-diagrams skill:

```bash
node <mermaid-diagrams-base>/scripts/check-mermaid.mjs <file-with-the-diagram>
```

## Measured syntax facts

| Input | Parses? |
|---|---|
| `flowchart`: `A[torch.add(x, y)]` — **unquoted ASCII parens** | **no — parse error** |
| `flowchart`: `A["torch.add(x, y)"]` | yes |
| `flowchart`: `A[调度器]`, `A[DispatchKey（TLS）]` — bare Chinese / full-width | yes |
| `sequenceDiagram` message containing `(in O(1))` | yes |
| `stateDiagram-v2` transition label containing `allocate_slots()` | yes |
| `classDiagram` relation label containing `(if requires_grad)` | yes |
| `mindmap` leaf containing `(thread local)` | yes |

So the quoting rule is narrow: **unquoted ASCII `(` / `)` breaks `flowchart` node
labels**, and only those. Full-width `（）` is safe everywhere, which is why Chinese
labels rarely need quoting.

Two more traps, both measured in mermaid-diagrams:

- `end` is reserved and cannot be a node id (`end --> B` fails). `A["end"]` is fine.
- An edge written inside a `subgraph` pulls every node named on that line into the
  group, even one defined outside it.

## flowchart — control flow, where a decision is made

```mermaid
flowchart TD
  API["PyTorch API 调用"] --> KS{"DispatchKeySet"}
  KS --> K["Kernel 函数指针"]
  K --> Out["结果"]
```

Edge labels and grouping:

```mermaid
flowchart TD
  subgraph EngineCore
    S["Scheduler"] --> KV["KVCacheManager"]
  end
  KV -->|"block 不足"| P["抢占"]
  KV --> Runner["ModelRunner"]
```

## sequenceDiagram — ordering across components over time

```mermaid
sequenceDiagram
  participant P as Python
  participant C as C++ Dispatcher
  P->>C: aten::add
  C->>C: 计算 DispatchKeySet
  C-->>P: kernel 结果
```

## stateDiagram-v2 — lifecycle, states, preemption

```mermaid
stateDiagram-v2
  [*] --> Waiting
  Waiting --> Running: 有 block
  Running --> Preempted: 显存不足
  Preempted --> Waiting: 释放 block
  Running --> [*]: 结束
```

## classDiagram — data structures and their relations

Use this instead of forcing a struct/ownership diagram into a flowchart:

```mermaid
classDiagram
  class TensorImpl {
    +DispatchKeySet key_set_
  }
  class AutogradMeta {
    +Node* grad_fn_
    +int64_t version_counter_
  }
  class Node {
    +edge_list next_edges_
  }
  TensorImpl --> AutogradMeta : owns when requires_grad
  AutogradMeta --> Node : grad_fn_
  Node --> Node : next_edges_
```

## mindmap — landscape of a subsystem

```mermaid
mindmap
  root((Dispatcher))
    DispatchKey
      TLS
      Tensor
    Kernel
      Boxed
      Unboxed
```
