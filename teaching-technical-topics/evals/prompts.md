# Prompts

Six scenarios, chosen so that each one can fail in a different way. Run them
against a fresh agent, save the answer verbatim, then score it with
`check_run.py` (see README.md).

| # | Scenario | Prompt | Mode | The point |
|---|---|---|---|---|
| 1 | First topic | `教我 vLLM prefix caching` | standard | Creates the `vllm` project, note, INDEX row from nothing |
| 2 | Related topic | `PyTorch Dynamo 是怎么工作的？` after #1 or a prior dispatcher lesson | standard | Must link the prior note in `## 和前文的联系`, bidirectionally |
| 3 | Why-design | `vLLM 的 scheduler 为什么设计成这样？` | standard | 正文 takes the constraint → design → rejected alternative → breakage shape |
| 4 | Quick follow-up | `那 chunked prefill 呢？` in the same session | quick | Stays short, opens with the nutshell, writes nothing |
| 5 | Same topic again | Repeat #1 verbatim in the same session | standard | 2-line recap + a new angle + updates the existing note; no duplicate |
| 6 | Demo without a GPU | `show me the eviction policy with a runnable example` on a CPU-only box | standard | Toy simulator, notebook executes; no fabricated expected output |

## Scoring

```bash
python3 evals/check_run.py --answer <saved-answer>.md --notes <notes-root> \
    --mode standard --notes-before <copy-taken-before-the-run>
```

`--notes-before` is only meaningful for scenario 4, where the assertion is that
nothing was written.

## What a run must also do, which no script checks

- The running example carries **real numbers**, not an abstract sketch.
- Each load-bearing claim points at a real `file::symbol` that exists in the
  version named in `verified_against`.
- The prose actually answers *why*, not just *what* — read it.
- A `## 自测` question resurfaced from an older note when one is relevant.

## Baseline

The no-skill measurements are recorded in README.md. Re-measure them rather than
trusting the numbers once the skill has changed shape.
