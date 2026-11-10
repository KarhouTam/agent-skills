---
name: teaching-technical-topics
description: Use when the user wants to learn or understand how or why something works — "教我", "讲讲", "原理", "explain X", "how does X work", "why is X designed this way", "step by step", "give me an example", or pasting code/logs and asking why they behave that way — especially system internals (PyTorch dispatcher / Dynamo / autograd, vLLM scheduler / paged attention), and whenever a question follows on from something already taught. Skip for quick factual lookups and pure coding tasks.
disable-model-invocation: true
---

# Teaching Technical Topics

## Overview

A teaching answer is a **build**, not a dump: pick a mode, answer in a fixed shape,
and teach into a draft that only becomes reference material once the user confirms
the lesson landed. Steps 0–2 always run; every lesson first pulls the private cloud
store (main plus every draft branch, so the same teaching memory is visible on every
machine), every Standard or Deep lesson fetches the subject repo's DeepWiki map first
(Step 1) — as a reading list, never as a source — and a prompt naming an ELI level also
loads `eli5` for the register (Step 2). The answer shape below applies to Standard and
Deep.

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

Then bring the shared store up to date before reading anything:

```bash
python3 <base>/scripts/sync.py pull <notes>
python3 <base>/scripts/note.py index <notes>
```

`pull` fetches `main` and every `script/*` draft branch, so drafts started on another
machine are already local. If it exits 1 (offline / conflict) or prints `local-only`,
keep teaching from the local copy and mention it **once at the end** of the answer —
never before `## In a nutshell`, and just above the 归档 ask when there is one, so the
ask still closes the message. One-time setup per machine:
`python3 <base>/scripts/sync.py init <private-repo-url> <notes>`.

`<project>` is the ecosystem the question lives in — `pytorch`, `vllm`, `k8s`, …

## Step 1 — Read before answering

Read `<notes>/INDEX.md` first: it is the generated global landscape — global counts,
per-project topic tables, related edges, open threads, re-verification order, and links
to each topic's materials. Then read `<project>/_project.md` (including its **Learner
model**), then the note of any topic the question builds on.

Only notes at `<project>/<topic>/note.md` are reference material. `.drafts/<project>/
<topic>/note.md` — including drafts pulled from another machine — holds lessons the
user has not signed off on yet; read a draft only to continue that same unfinished
lesson, never to ground an answer to a new question. Materials (notebooks, figures)
live beside the note in the same topic directory; open them when a demo is the point.

**Treat every note as a hypothesis, not a fact.** A note records what was true for
the version in its `verified_against`. If that differs from what is installed now,
re-verify before repeating the claim; if the source contradicts the note, fix the
note and tell the user what changed. For fast-moving internals an unrefreshed note
is worse than no note.

### External map: DeepWiki (every Standard / Deep lesson; skipped in Quick)

Take the subject repo's wiki map before drafting — no condition on the question, only
on the mode. Nearly every lesson here is PyTorch or vLLM, so the repo is almost always
a public one with a map waiting.

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

### First lesson in a new project: one calibration line

A Standard or Deep lesson that a prompt explicitly commissions as teaching —
`教我` / `教教我` / `teach me` / `我想系统学` / `walk me through end to end` — and that
opens a `<project>` with no `_project.md` and no notes yet, and where neither the
prompt nor the session carries a background signal (a self-description, an ELI
level, or fluent use of the vocabulary all count as signals), gets one
calibration exchange before the build. The first reply is a single line that
fixes the starting level, and the message ends at the question:

> 先校准一下：你用过 vLLM 的上层 API 吗？KV cache 这类词需要先给名词地图，还是直接进机制？

No nutshell, no lesson, nothing written to `<notes>` yet. On the next turn teach
at the revealed level, and record the answer in `_project.md`'s Learner model as
its first background line. Everything else answers in one turn: a prompt that
states its own background, a project that already has a Learner model, a plain
question ("X 是怎么工作的？"), and Quick mode. If the user waves it off
("直接讲"), teach at the zero-background default.

### ELI register — only when the prompt names a level

When the prompt carries an explicit `/teaching-technical-topics` invocation **and** a
level (`ELI5`, `ELI10`, `ELI15`, "像给五岁小孩讲"), load the `eli5` skill and take its
audience table as this lesson's register.

**REQUIRED SUB-SKILL:** `eli5`. Which audience a level maps to, and what that audience
does to vocabulary, analogies and tone, live there; do not re-derive them here.

The number is the **level, not the reader's age**: `ELI5` means no term appears before
an analogy has made it usable; `ELI10` / `ELI15` let terms appear with this file's
one-clause definition. A literal human audience ("讲给我妈听", an actual five-year-old)
is the standalone `eli5` flow, not this one. The user is still an expert somewhere
else, so 正文 keeps real numbers and never talks down — ELI5's own reminder.

Register is all a level changes: mode, answer shape, draft note, `## 来源` and the
归档 ask stay as this file fixes them, and 正文 stays Chinese. "Simplify ruthlessly —
80% accuracy beats losing the audience" licenses dropping detail, never asserting it:
every load-bearing claim stays `file::symbol` or `unverified`.

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
  INDEX.md                        # generated landscape — local-only, never hand-edit
  .sync/                          # internal cloud mirror — never read or edit
  .drafts/<project>/<topic>/note.md   # in-progress lesson, NOT reference material
  <project>/
    _project.md                   # learning map: taught, dependencies, Learner model
    <topic>/
      note.md                     # one *confirmed* note per topic taught
      <topic>.py                  # percent-format source, the editable copy
      <topic>.ipynb
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
- <1–3 questions, answers withheld; the first is the check question in the 归档
  ask, so a draft always carries at least one>
```

`confidence` is `verified` (a source or a run confirmed it) or `background` (model
knowledge only). `verified_against` is `<pkg>@<version>`, or `unverified`.

## Cloud store: main + draft branches

`<notes>/.sync/` is a git clone of the user's **private** `teaching-notes` repo. It is
internal: never read it for teaching, never edit it. The cloud holds only these:

| Cloud ref | Contains |
|---|---|
| `main` | archived topics `<project>/<topic>/...` plus every `_project.md` |
| `script/<project>/<topic>` | one unfinished lesson's `note.md` + materials, in final layout |

`INDEX.md` and `.drafts/` stay local; the landscape is regenerated from whatever the
pull brought, so generated files never produce merge conflicts.

- **Setup once per machine:** `python3 <base>/scripts/sync.py init <private-repo-url> <notes>`.
  Without it syncs run `local-only` and cross-machine memory is not shared.
- **Every skill invocation:** `sync.py pull` in Step 0, then `note.py index`.
- **Every draft write or revision:** `sync.py draft <project>/<topic>` immediately —
  again after rebuilding its notebook or editing `_project.md` (a `_project.md` edit
  with no draft on the table uses `sync.py push`).
- **Archive:** after the user confirms, `note.py promote` → `note.py check` →
  `sync.py archive <project>/<topic>`. `archive` refreshes the branch, merges it into
  `main` with `--no-ff`, pushes, and deletes the branch. Never leave the merge undone.
- **Archived-note correction / Learner model edit:** `note.py add` (or `note.py index`)
  then `sync.py push`.
- Conflicts are reported with the files and the local copy is kept; nothing is
  force-pushed. Compare the two versions, then rerun with `--force` to settle in the
  chosen direction (`pull --force` takes cloud, `push`/`draft --force` keep local).
- Every network call times out (`TEACHING_SYNC_TIMEOUT`, default 30s), so an
  unreachable remote degrades to local teaching instead of stalling the lesson.

## Note lifecycle: draft → promote

**Nothing a lesson produced becomes reference material until the user says it was
good.** An explanation that lands badly, or that the user is still pushing back on,
must never be read back as fact six sessions later.

**1. Teach into a draft.** After the answer, write
`<notes>/.drafts/<project>/<topic>/note.md` (same frontmatter and body as a note).
Drafts are excluded from `INDEX.md`, from `related` links, and from Step 1 — an
unconfirmed draft cannot poison anything. Then back it up immediately:

```bash
python3 <base>/scripts/sync.py draft <project>/<topic> <notes>
```

Push again after every revision, and after rebuilding or replacing the notebook.

The **notebook is not drafted**: it goes straight to
`<notes>/<project>/<topic>/<topic>.py` and gets linked into the workspace, because the
user runs it during the lesson. It is executed, not asserted, so it carries no claim
that could rot; only the note's prose needs the user's sign-off. It still travels on
the draft branch as that topic's material.

**2. Ask once, in one line, at the end of the first Standard answer — carrying the
draft's first `## 自测` question.** The question is what turns confirmation into
measurement, so put it last and end the message there: no hint, no answer, nothing
after it. Something like:

> 这节讲清楚了吗？我先考一道，答上来我就归档成 `pytorch/dynamo` 的笔记——dispatcher 查不到 op 的 overload key 时，会走哪条 fallback 路径？

Once per topic — never on a follow-up, never on a revision, never in Quick. After
that the draft waits quietly; asking again on every turn is noise, not diligence.

**3. React to what the user does:**

| The user | You |
|---|---|
| Asks a follow-up, or says part of it was wrong | Revise *the same draft* — no second note, no re-ask |
| Answers the check question correctly | Promote — a correct answer **is** the confirmation |
| Answers it wrongly or vaguely | Not correct is not correct: say exactly what was off, patch the draft section that failed to teach it, re-teach that one point in a few sentences, then archive on their word. Never re-quiz |
| Says it was good **and** that the lesson is over (懂了 / 没问题 / 归档吧 / 清楚了) | Promote |
| Never comes back to it | The draft stays a draft. Mention it once when it becomes relevant again |
| Says to skip the ceremony (直接归档 / 不用问) | Promote immediately — the user's instruction wins |

Grading is strict, in both directions. A vague or partly-correct answer is not
correct; no insight the user did not actually express counts as theirs. The reply
to a wrong answer is the sentence that exposes the contradiction ("那第 3 个块没
写满，哈希为什么还对得上？"), never "不错的尝试" — and a failed check is a
confusion point for `_project.md`'s Learner model, same as any other.

**4. Promote:**

```bash
python3 <base>/scripts/note.py promote <notes>/.drafts/pytorch/dynamo/note.md --related vllm/scheduler
python3 <base>/scripts/note.py check <notes>
python3 <base>/scripts/sync.py archive pytorch/dynamo <notes>
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

**Run `check` and `archive` after promoting — that is the gate before you send.**
`check` proves the local note graph is sound; `archive` is what actually puts the
confirmed topic on cloud `main` and closes its draft branch. A draft alone needs no
gate; a promotion does. If `archive` fails, say so in one line: the note is archived
locally, the merge will be retried.

## Notebooks

Write the demo as percent-format `.py` (`# %%` code cells, `# %% [markdown]`
markdown cells), then build, execute and link it:

```bash
python3 <base>/scripts/build_notebook.py <notes>/pytorch/<topic>/<topic>.py --link-dir <workspace>
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
zero: record that, and start the next lesson at their real level. `_project.md` lives on
cloud `main`: after editing it, run `sync.py push` so the learner model is shared too.

## Common mistakes

| Mistake | Correction |
|---|---|
| Answering in English because the question was | 正文 and the nutshell are Chinese whatever language the question used |
| Background before the point | `## In a nutshell` is the first thing in the answer |
| Terms every practitioner knows, left undefined | Assume zero background in *this* field: define each term in one clause at first use |
| Asking to archive on every turn | One ask per topic, on the first Standard answer; follow-ups just revise the draft |
| Same 正文 shape for every question | *how* / *why* / *step by step* / *example* take different shapes |
| Full ceremony for a small follow-up | Quick mode: nutshell plus answer, no note, no notebook |
| An ELI level read as licence to skip the build | ELIx sets register only: same sections, same draft, same `file::symbol` provenance |
| ASCII-art diagram inside a code fence | Mermaid, fence language `mermaid`, validated with `check-mermaid.mjs` |
| One diagram per section | One per load-bearing mechanism |
| Confident claims with no provenance | Every load-bearing claim is `file::symbol` or says `unverified` |
| Treating DeepWiki prose as a conclusion | It is only a map: every `file::symbol` it names gets opened in the local source, and wiki-only claims are `unverified` |
| Repeating a note's claim without re-checking | Notes are hypotheses; a version mismatch means re-verify |
| Hand-editing `INDEX.md` | Run `note.py promote`; INDEX is generated |
| Promoting a note the user never confirmed | Ask first; a draft is not reference material |
| Writing a second note for a follow-up | Revise the same draft — no second note, no re-ask |
| Shipping a notebook with empty outputs | Let `build_notebook.py` execute it, or say it was not executed |
| Writing a draft but leaving it local-only | Run `sync.py draft <project>/<topic>` after the write and after every revision |
| Promoting without merging the branch | `note.py promote` then `sync.py archive`; an unmerged branch is an unfinished archive |
| Putting a draft on cloud `main` | Unconfirmed content lives only on `script/<project>/<topic>` |
| Reading or editing `<notes>/.sync/` | It is the internal mirror; use it only through `sync.py` |
| Force-pushing over a conflict without reading it | Compare both versions first, then `--force` in the direction you chose |
| Expecting `INDEX.md` in the cloud | The landscape is generated locally after each pull |
| Promoting on a polite 懂了 that the check question just contradicted | A wrong or vague answer defers promotion: patch the draft, re-teach the gap, archive on their word |
| Asking the check question and answering it in the same message | The question is the last sentence of the ask; hint and answer come only after the user replies |
| A calibration quiz for a plain question, a known project, or a prompt that already carries background | Only an explicitly commissioned first lesson of a new project with no background signal calibrates; everything else answers in one turn |

## Red flags

- The answer has no `## In a nutshell`.
- `<notes>` was neither read before answering nor written after.
- A note was promoted without the user confirming the lesson landed.
- `note.py check` was not run after a promotion, or was run and failed.
- `<notes>` holds a related topic but `## 和前文的联系` is missing.
- A claim about internals carries no `file::symbol` and no `unverified` marker.
- The 归档 question is asked more than once for the same topic, or at all in Quick
  mode.
- A Standard/Deep lesson that never consulted DeepWiki, and never said why not.
- A wiki-sourced claim wearing a local `file::symbol`, or `verified_against` naming DeepWiki.
- Every question starts a new project directory instead of extending one.
- A Standard/Deep lesson never pulled the cloud, or a pull conflict was ignored and the
  remote note was treated as read.
- A draft was written or revised without `sync.py draft`.
- The user confirmed the lesson but `sync.py archive` never ran.
- The 归档 ask carried no check question, or answered it in the same breath.
- An explicitly commissioned first lesson of a new project was taught at an assumed
  level, with no calibration line and no background signal in the prompt.
- `<notes>/.sync/` was treated as teaching material.
