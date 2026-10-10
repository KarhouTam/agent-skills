---
name: teaching-technical-topics
description: Use when the user wants to learn or understand how or why something works — "教我", "讲讲", "原理", "explain X", "how does X work", "why is X designed this way", "step by step", "give me an example", or pasting code/logs and asking why they behave that way — especially system internals (PyTorch dispatcher / Dynamo / autograd, vLLM scheduler / paged attention), and whenever a question follows on from something already taught. Skip for quick factual lookups and pure coding tasks.
disable-model-invocation: true
---

# Teaching Technical Topics

## Overview

A teaching answer is a **build**, not a dump: pick a mode, answer in a fixed shape,
and teach into a draft that only becomes reference material once the user confirms
the lesson landed. Steps 0–2 always run; a Standard or Deep lesson about a public
repo also fetches that repo's DeepWiki map first (Step 1) — as a reading list, never
as a source. The answer shape below applies to Standard and Deep.

Resolve `<base>` from the base directory the harness reports for this skill —
normally `~/.agents/skills/teaching-technical-topics/`, also reachable as
`~/.claude/skills/teaching-technical-topics/`.

## Step 0 — Locate the notes root

```bash
for d in "${TEACHING_NOTES_DIR:-}" "<base>/memory" "${XDG_DATA_HOME:-$HOME/.local/share}/teaching-notes"; do
  [ -n "$d" ] || continue
  if mkdir -p "$d" 2>/dev/null && [ -w "$d" ]; then echo "NOTES=$d"; break; fi
done
```

The first writable candidate wins and is called `<notes>` below. `$TEACHING_NOTES_DIR`
overrides everything; `<base>/memory` is the default. If **none** is writable, still
answer, print the note inline in a fenced block instead of saving it, and say plainly
that it was not saved.

`<project>` is the ecosystem the question lives in — `pytorch`, `vllm`, `k8s`, …

## Step 1 — Read before answering

Read `<notes>/INDEX.md`, then `<project>/_project.md` (including its **Learner
model**), then the note of any topic the question builds on.

Only notes that reached the project folder are reference material. `<notes>/.drafts/`
holds lessons the user has not signed off on yet — read a draft only to continue
that same unfinished lesson, never to ground an answer to a new question.

**Treat every note as a hypothesis, not a fact.** A note records what was true for
the version in its `verified_against`. If that differs from what is installed now,
re-verify before repeating the claim; if the source contradicts the note, fix the
note and tell the user what changed. For fast-moving internals an unrefreshed note
is worse than no note.

### External map: DeepWiki (one lookup for Standard / Deep; skipped in Quick)

When the question sits inside a **public GitHub repository** (PyTorch, vLLM, k8s…),
take that repo's wiki map before drafting.

**REQUIRED SUB-SKILL:** `deepwiki`. What to query, in what order, and which answers may not be trusted all live there; do not re-derive them here.

Three consequences that belong to teaching:

- The page tree is where `## 延伸` candidates come from, and the wiki is cited as
  `deepwiki:<owner>/<repo>` in `## 来源` — it never replaces a `file::symbol`, never
  raises `confidence` to `verified`, and never becomes `verified_against`; a claim
  only it supports is `unverified`.
- Settle `<owner>/<repo>` from the checkout (`upstream`, not a fork's `origin`) and
  record `repo: <owner>/<repo>` in `<project>/_project.md` for the next lesson.
- Not indexed, private, or offline: one line saying so, then teach as usual.

## Step 2 — Pick a mode

| Mode | When | Shape |
|---|---|---|
| **Quick** | A follow-up, a side question, or a small clarification ("那 X 呢?", "顺便问一下…") | ≤ ~150 words, still opens with the nutshell; no notebook, no new note, no DeepWiki lookup, no 归档 ask |
| **Standard** | The default for a new topic | The full answer shape below |
| **Deep** | "walk me through end to end", or a mechanism the user will act on | Standard + a source dive with `file::symbol` + a notebook |

**An easy side question is still Quick.** The user is curious, not commissioning a
lesson: answer it and stop. Escalate to Standard only when they ask to be taught it
("仔细讲讲 X") or the answer genuinely needs a mechanism trace.

**Already taught?** Two-line recap, then a new angle worth the user's time, then
update the existing note. Never open a duplicate note for a topic `<notes>` covers.

## Answer shape (Standard / Deep, fixed order)

1. **`## In a nutshell`** — one sentence, in Chinese, the whole thing. Not a
   definition list, not a preamble. If it needs two sentences, you have not decided
   what it is.
2. **正文** — Chinese prose, English technical terms (`scheduler`, `dispatcher`,
   `prefix caching`). Code identifiers stay English; comments may be Chinese. Its
   internal shape depends on what was asked — see the next section.
3. **关键图** — 1–2 mermaid diagrams, placed in the prose where they carry the
   mechanism.
4. **Demo** — only when running something teaches more than prose can. Default to a
   notebook (see Notebooks). A plain `.py` is acceptable when a notebook genuinely
   cannot run (needs a GPU, a server, or a CLI); say which and why.
5. **`## 来源`** — for each load-bearing claim: `file::symbol` plus the version it
   was checked against, or the literal word `unverified`. Doc links and blog posts
   go here; a DeepWiki pointer is written `deepwiki:<owner>/<repo>` and never
   replaces the `file::symbol`.
6. **`## 和前文的联系`** — required as soon as `<notes>` holds a related topic: name
   the note and the mechanism-level link. For a genuinely first topic, say so in one
   line instead.
7. **`## 延伸`** — 0–4 next topics, each with why *this* user would care.

## 正文 shape by question type

| The user asked | 正文 does this |
|---|---|
| *how* it works | Trace one concrete input end to end — one real call, in order |
| *why* it is designed this way | Constraint → chosen design → one rejected alternative → where it breaks |
| *step by step* | The state after each step, plus the failure that motivates the next one |
| *an example* | Real numbers first (`block_size=16`, a 40-token prompt → 3 blocks), then a harder variant |

Every Standard answer carries at least one running example with real numbers, not
an abstract sketch.

**Assume zero background in *this* field.** The user is an expert somewhere else,
not here: default to never having seen the vocabulary, and let `_project.md` record
where that default was wrong. So 正文 does two things in order — say what problem
the thing exists to solve, then define each term, acronym and name in one clause the
first time it appears (`KV cache（把每个 token 的中间结果存下来，下次直接复用）`).
Definitions stay inline at first use: not a glossary paragraph, and never before the
nutshell.

## Diagrams

One diagram per load-bearing mechanism. A second is allowed only for a genuinely
different mechanism; a table or plain prose covers everything else.

| The thing being explained | Mermaid form |
|---|---|
| Control flow, where a decision is made | `flowchart TD` |
| Ordering across components over time | `sequenceDiagram` |
| Lifecycle, states, preemption | `stateDiagram-v2` |
| Data structures and their relations | `classDiagram`, or a table |
| Landscape of a subsystem | `mindmap` |

Start from `reference/diagram-templates.md` — every snippet there parses. Two
measured traps: an unquoted ASCII `(` or `)` in a label is a parse error (full-width
`（）` is fine), and `end` is reserved, so it cannot be a node id.

**REQUIRED SUB-SKILL:** use mermaid-diagrams for syntax questions and to validate
before sending:

```bash
node <mermaid-diagrams-base>/scripts/check-mermaid.mjs <file-with-the-diagram>
```

## Notes layout

```
<notes>/
  INDEX.md                 # generated from note frontmatter — never hand-edit
  .drafts/<project>/<topic>.md   # in-progress lesson, NOT reference material
  <project>/
    _project.md            # learning map: taught, dependencies, Learner model, open threads
    <topic>.md             # one *confirmed* note per topic taught
    notebooks/<topic>.py   # percent-format source, the editable copy
    notebooks/<topic>.ipynb
```

A note's id is `project/topic`. Topic note — frontmatter is the machine-readable
part, so `nutshell` lives there and the body does not repeat it:

```markdown
---
project: pytorch
topic: dispatcher
date: 2026-02-14
nutshell: <the same sentence the answer opened with>
related: [pytorch/autograd]
verified_against: torch@2.12.1
confidence: verified
---
# PyTorch Dispatcher

## 讲了什么
- <3–6 bullets: mechanism, not restatement>

## 关键图
<mermaid source>

## 已建立的连接
<`note.py add` writes and maintains the `- → [...]` bullets here>

## 待深入
- <threads worth pursuing next>

## 自测
- <2–3 questions, answers withheld; optional, but keep them if you add them>
```

`confidence` is `verified` (a source or a run confirmed it) or `background` (model
knowledge only). `verified_against` is `<pkg>@<version>`, or `unverified`.

## Note lifecycle: draft → promote

**Nothing a lesson produced becomes reference material until the user says it was
good.** An explanation that lands badly, or that the user is still pushing back on,
must never be read back as fact six sessions later.

**1. Teach into a draft.** After the answer, write
`<notes>/.drafts/<project>/<topic>.md` (same frontmatter and body as a note). Drafts
are excluded from `INDEX.md`, from `related` links, and from Step 1 — an unconfirmed
draft cannot poison anything.

The **notebook is not drafted**: it goes straight to
`<notes>/<project>/notebooks/<topic>.py` and gets linked into the workspace, because
the user runs it during the lesson. It is executed, not asserted, so it carries no
claim that could rot; only the note's prose needs the user's sign-off.

**2. Ask once, in one line, at the end of the first Standard answer.** Something like:

> 这节讲清楚了吗？确认没问题我就归档成 `pytorch/dynamo` 的笔记；要补的地方我先改草稿。

Once per topic — never on a follow-up, never on a revision, never in Quick. After
that the draft waits quietly; asking again on every turn is noise, not diligence.

**3. React to what the user does:**

| The user | You |
|---|---|
| Asks a follow-up, or says part of it was wrong | Revise *the same draft* — no second note, no re-ask |
| Says it was good **and** that the lesson is over (懂了 / 没问题 / 归档吧 / 清楚了) | Promote |
| Never comes back to it | The draft stays a draft. Mention it once when it becomes relevant again |
| Says to skip the ceremony (直接归档 / 不用问) | Promote immediately — the user's instruction wins |

**4. Promote:**

```bash
python3 <base>/scripts/note.py promote <notes>/.drafts/pytorch/dynamo.md --related vllm/scheduler
python3 <base>/scripts/note.py check <notes>
```

`promote` validates first and only then moves the file, so a rejected promotion
leaves the draft untouched. It then runs the `add` pass: normalises frontmatter,
regenerates `INDEX.md` from frontmatter, creates `_project.md` if missing, and makes
every `related` link bidirectional in both frontmatter and body bullets. `check`
fails on dangling or one-way links, broken relative links, missing frontmatter, and
INDEX drift, and lists the drafts still awaiting confirmation.

**A lesson that links to another unconfirmed lesson fails promotion.** That is the
point: ask the user about the whole chain at once ("dynamo 可以归档了，它引用的
dispatcher 还是草稿，一起归档吗？") rather than leaving a note pointing at nothing.

**Run `check` after promoting — that is the gate before you send.** A draft alone
needs no gate; a promotion does.

## Notebooks

Write the demo as percent-format `.py` (`# %%` code cells, `# %% [markdown]`
markdown cells), then build, execute and link it:

```bash
python3 <base>/scripts/build_notebook.py <notes>/pytorch/notebooks/<topic>.py --link-dir <workspace>
```

The script converts, **executes** the notebook and embeds the outputs, so a demo
that raises exits non-zero and you must fix it rather than ship a dead cell. If the
demo genuinely cannot run here, build it with `--no-execute` and say in the answer
that it was not executed — never write expected outputs by hand. Keep demos seeded
and CPU-runnable: a toy simulator of the mechanism beats a real cluster.

Choose `--link-dir` from the workspace's own convention for agent-generated files
(its `AGENTS.md`, `.wolf/anatomy.md`, a `docs/` layout); when there is none, use
`<workspace>/.teaching-notes/` and make sure that directory is ignored — prefer
`.git/info/exclude` over editing a tracked ignore file — so the link stays out of
the user's `git status`. Re-running is idempotent; `--force` replaces a foreign file.

## 延伸 rules

Recommend what the user's *phrasing* implies they are circling, not the next chapter
of a syllabus: "why designed like that" wants a competing design and its trade-off;
"step by step" wants the failure mode that motivates the next step; "give me an
example" wants the same mechanism under a harder input. One line of why per item.
Return fewer than 4 items rather than padding to a count — 0 is a valid answer when
the topic is genuinely closed.

## Learner model

`_project.md` records what the user *knows and got wrong*, not just what was taught:
assumed background, confusions they voiced, preferred depth. Update it when they
push back, misread something, or ask for more/less detail. At Step 1, resurface one
question from an older note's `## 自测` when it is relevant to the new topic.
Words they use fluently are the signal that the assumed background is higher than
zero: record that, and start the next lesson at their real level.

## Common mistakes

| Mistake | Correction |
|---|---|
| Answering in English because the question was | 正文 and the nutshell are Chinese whatever language the question used |
| Background before the point | `## In a nutshell` is the first thing in the answer |
| Terms every practitioner knows, left undefined | Assume zero background in *this* field: define each term in one clause at first use |
| Asking to archive on every turn | One ask per topic, on the first Standard answer; follow-ups just revise the draft |
| Same 正文 shape for every question | *how* / *why* / *step by step* / *example* take different shapes |
| Full ceremony for a small follow-up | Quick mode: nutshell plus answer, no note, no notebook |
| ASCII-art diagram inside a code fence | Mermaid, fence language `mermaid`, validated with `check-mermaid.mjs` |
| One diagram per section | One per load-bearing mechanism |
| Confident claims with no provenance | Every load-bearing claim is `file::symbol` or says `unverified` |
| Treating DeepWiki prose as a conclusion | It is only a map: every `file::symbol` it names gets opened in the local source, and wiki-only claims are `unverified` |
| Repeating a note's claim without re-checking | Notes are hypotheses; a version mismatch means re-verify |
| Hand-editing `INDEX.md` | Run `note.py promote`; INDEX is generated |
| Promoting a note the user never confirmed | Ask first; a draft is not reference material |
| Writing a second note for a follow-up | Revise the same draft — no second note, no re-ask |
| Shipping a notebook with empty outputs | Let `build_notebook.py` execute it, or say it was not executed |

## Red flags

- The answer has no `## In a nutshell`.
- `<notes>` was neither read before answering nor written after.
- A note was promoted without the user confirming the lesson landed.
- `note.py check` was not run after a promotion, or was run and failed.
- `<notes>` holds a related topic but `## 和前文的联系` is missing.
- A claim about internals carries no `file::symbol` and no `unverified` marker.
- The 归档 question is asked more than once for the same topic, or at all in Quick
  mode.
- A Standard/Deep lesson about a public repo's internals that never consulted DeepWiki, and never said why not.
- A wiki-sourced claim wearing a local `file::symbol`, or `verified_against` naming DeepWiki.
- Every question starts a new project directory instead of extending one.
