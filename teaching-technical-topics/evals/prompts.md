# Prompts

Twelve scenarios, chosen so that each one can fail in a different way. Run them
against a fresh agent, save the answer verbatim, then score it with
`check_run.py` (see README.md).

| # | Scenario | Prompt | Mode | The point |
|---|---|---|---|---|
| 1 | First topic, calibrated | `教我 vLLM prefix caching`, then the user answers the calibration question (e.g. `完全没接触过 LLM 推理，只写过 Python 后端，从名词地图开始吧`) | standard, 2 turns | Turn 1 is the calibration line alone (`--mode calibration`, nothing written yet); turn 2 creates the `vllm` project, note, INDEX row from nothing, taught at the revealed level with the background recorded in `_project.md` |
| 2 | Related topic | `PyTorch Dynamo 是怎么工作的？` after #1 or a prior dispatcher lesson | standard | Must link the prior note in `## 和前文的联系`, bidirectionally |
| 3 | Why-design | `vLLM 的 scheduler 为什么设计成这样？` | standard | 正文 takes the constraint → design → rejected alternative → breakage shape |
| 4 | Quick follow-up | `那 chunked prefill 呢？` in the same session | quick | Stays short, opens with the nutshell, writes nothing |
| 5 | Same topic again | Repeat #1 verbatim in the same session | standard | 2-line recap + a new angle + updates the existing note; no duplicate |
| 6 | Demo without a GPU | `show me the eviction policy with a runnable example` on a CPU-only box | standard | Toy simulator, notebook executes; no fabricated expected output |
| 7 | Confirmation | Reply `懂了，归档吧` to scenario 1 | — | Promotes the draft; note enters INDEX; `note.py check` passes |
| 8 | No confirmation | Reply `嗯，那 X 呢？` to scenario 1 | quick | Draft stays a draft; nothing enters INDEX |
| 9 | DeepWiki-reachable repo internals | `教我 PyTorch Dynamo 的 guard 系统是怎么工作的？源码在 /root/pytorch` | standard | Follows the teaching skill's REQUIRED SUB-SKILL line to `deepwiki`, cites the map as `deepwiki:pytorch/pytorch`, and still opens every `file::symbol` in the local checkout. Run after #2, or with background stated, so the calibration guard is not the thing under test |
| 10 | Newcomer + easy side question | `教我 vLLM 的 prefix caching。我是做后端开发的，完全没接触过 LLM 推理，KV cache 是什么？` then `顺便问一下，prefix caching 和 paged attention 是一回事吗？` | standard, then quick | 正文 names the problem before the mechanism and defines the vocabulary at first use; the side question gets ≤ ~150 words and never an 归档 ask |
| 11 | ELI level register | `/teaching-technical-topics 用五岁小孩也能听懂的话讲讲 vLLM 的 paged attention`, and the same again with a literal `ELI5` | standard | Loads `eli5` for the register instead of improvising one; the level changes 正文 only — same sections, same draft, same `file::symbol` provenance |
| 12 | Cross-machine draft | On notes root A teach a topic and leave it unconfirmed. On a second notes root B, join the same private remote (`sync.py init`), then ask the same topic. Archive on B, then `sync.py pull` on A. | standard | Step 0 pulls every `script/*` draft branch; B sees A's draft; after confirmation B merges it into `main` and deletes the branch; A's local draft disappears and the archived note appears |
| 13 | Calibration negatives | (a) `教我 vLLM 的 prefix caching。我是做后端开发的，完全没接触过 LLM 推理。` (b) `vLLM 的 scheduler 是怎么工作的？` on a fresh root (c) `教我 PyTorch Dynamo` after #2 | standard, 1 turn each | The guard fires only for an explicitly commissioned first lesson with no background signal: all three answer in one turn with the full Standard shape, no calibration question |
| 14 | Check question in the 归档 ask | `教我 vLLM 的 prefix caching。我写 Python 后端，懂 attention 和 KV cache 的概念，但没读过 vLLM 源码。` | standard | Background is stated, so no calibration; the 归档 ask carries exactly one check question — the draft's first `## 自测` question — and the message ends with it (`--expect-check-ask`) |
| 15 | Correct answer promotes | Continue 14: the user answers the check question correctly, with no confirmation word | — | A correct answer **is** the confirmation: the note is promoted (`--stage promoted`), with no second quiz |
| 16 | Wrong answer does not | Fresh run of 14's prompt, then the user answers the check question wrongly | — | The draft stays a draft (`--stage draft`); the reply names the gap without flattery, patches the draft section that failed to teach it, re-teaches that one point, and does not re-quiz (human read) |

## Scoring

```bash
python3 evals/check_run.py --answer <saved-answer>.md --notes <notes-root> \
    --mode standard --topic pytorch/dynamo --stage draft
```

Pass `--expect-deepwiki` for scenario 9, or whenever the run had DeepWiki reachable:
the answer must then point at the wiki it used in `## 来源` and must still carry a
local `file::symbol`.

`--notes-before` is meaningful for scenario 4 and for scenario 1's first turn,
where the assertion is that nothing was written.

`--mode calibration` scores scenario 1's first turn: no nutshell, at most ~150
words, the message ends with its question, and nothing was written to the notes
root. `--expect-check-ask` scores a first Standard answer (scenario 1 turn 2,
scenario 14): the 归档 ask is present exactly once, the message ends with its
check question, and the draft's `## 自测` carries that question.

## Two stages per lesson

A lesson is scored twice, because it has two states:

```bash
# after the agent answers, before the user has said anything
... --topic pytorch/dynamo --stage draft

# after the user says 懂了 / 没问题, and the agent has promoted it
... --topic pytorch/dynamo --stage promoted
```

`--stage draft` fails if the note reached `INDEX.md` before the user confirmed it.
`--stage promoted` fails if the draft survived the promotion. Re-run both after any
change to `note.py`.

## What a run must also do, which no script checks

- The running example carries **real numbers**, not an abstract sketch.
- Each load-bearing claim points at a real `file::symbol` that exists in the
  version named in `verified_against`.
- Every `file::symbol` the wiki pointed at was opened in the local checkout, and a
  wiki/source disagreement is told to the user rather than smoothed over. The
  DeepWiki procedure itself belongs to the `deepwiki` skill; scenario 9 is where
  this skill's REQUIRED SUB-SKILL link to it is exercised.
- DeepWiki supplied *where to read* and the repo's own vocabulary — the running
  example still comes from the local source, not from the wiki prose.
- The prose actually answers *why*, not just *what* — read it.
- A `## 自测` question resurfaced from an older note when one is relevant.
- The **first** Standard answer asks once whether the lesson landed, and that ask is
  never repeated on a follow-up or revision; the draft is not promoted on the
  agent's own initiative.
- The calibration question targets level — familiar vocabulary versus a 名词地图 —
  not trivia, and it is the whole message: no lesson, no hint after the question.
- A wrong check answer gets the gap named ("Not quite — ..." style, no 不错的尝试),
  the draft section that failed to teach it patched, that one point re-taught in a
  few sentences, and no second quiz; a correct answer promotes without re-asking.
- When the prompt named an ELI level, the register came from `eli5`'s audience table
  rather than from a re-invented one, and it excused nothing: same sections, same
  draft note, same provenance.
- If the harness had no DeepWiki MCP tools, the user was offered the config once
  (with their harness's entry) and the lesson still went ahead on the fallback.

## Baseline

The no-skill measurements are recorded in README.md. Re-measure them rather than
trusting the numbers once the skill has changed shape.
