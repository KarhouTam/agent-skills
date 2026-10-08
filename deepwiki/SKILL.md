---
name: deepwiki
description: Use when a question is about the internals of a public GitHub repository and you need to know which files, classes or modules own a mechanism — "这个仓库里 X 在哪", "how does X work in <repo>", onboarding into an unfamiliar codebase, or a review that must name real symbols. Skip when no public repo sits behind the question.
---

# DeepWiki

## Overview

DeepWiki serves a generated wiki per public GitHub repo: a page tree, and answers
that cite file and class names. It is a **map of where to read** and nothing more —
the prose is model-generated and unversioned, so every name it hands you is a
hypothesis to open in the real source.

## Query it in this order

| # | Call | What it buys |
|---|---|---|
| 1 | `structure <owner>/<repo>` | the repo's own page tree — a reading list, plus the vocabulary the project uses for its own concepts |
| 2 | `ask <owner>/<repo> "<the user's own question>"` | one call, phrased as asked; the value is the file and class names it cites |
| — | never the whole-wiki dump | one response, no size preview: `vllm-project/vllm` is 1.4 MB ≈ 344k tokens, so by the time you see how big it is, it is already in context. One `ask` returns the part you would have read |

One question, not five. Each extra call costs seconds and lands more unverified
prose in context.

## Run it

```bash
python3 <base>/scripts/deepwiki.py structure pytorch/pytorch
python3 <base>/scripts/deepwiki.py ask vllm-project/vllm "how does prefix caching work?"
```

`<base>` is this skill's directory. It needs `python3` and network, and nothing else
— the endpoint is public, key-less and stateless. The script prints the answer as
text and exits non-zero with a readable reason when the repo is not indexed. Run it
through `python3`, never by bare path.

## Naming the repo

`<owner>/<repo>` is the **project**, not your checkout: `git -C <checkout> remote -v`
and prefer `upstream` — a fork's `origin` is not what DeepWiki indexed
(`/root/pytorch`: origin `KarhouTam/pytorch`, upstream `pytorch/pytorch`). With no
checkout, take the canonical `<owner>/<repo>` from the package metadata or docs.

## What it may be trusted for

| Use it for | Never treat it as |
|---|---|
| a reading list, and where a subsystem lives | provenance for a claim |
| candidate `file::symbol` names | a revision — it is unversioned, so it cannot become `verified_against` |
| the repo's vocabulary for a concept | evidence a symbol still exists, or that code still works that way |

- **Open every name it gives you** in the local source, or in the file at the
  pinned ref. When source and wiki disagree, source wins — and say so out loud,
  because the divergence is usually the interesting part (stale wiki, or a recent
  move).
- A claim only the wiki supports is **`unverified`**. It never raises confidence in
  a note, a review, or a doc.
- **A failed lookup is one line, not a blocker**: repo not indexed, private, offline
  or rate-limited — say which, then carry on with the source. Never invent wiki
  content, never retry in a loop.
- It is a pointer, so cite it as one: `deepwiki:<owner>/<repo>`, never as the ref a
  claim was checked against.

## Common mistakes

| Mistake | Correction |
|---|---|
| Paraphrasing the wiki as the answer | It is a map: the answer comes from source you opened, the wiki decides where to look |
| Asking for the whole wiki instead of one answer | No size preview exists — the 344k-token response is in context before you learn its size. Take the page tree, then one question |
| Retrying a failing lookup, or blocking on it | One attempt; one line of explanation; continue |
| Quoting the wiki's own line numbers as fact | Wiki prose drifts; line numbers come from the file you opened |
| Asking it about a repo it does not index | Private, fork-only and non-GitHub sources are out of scope — say so |

## Red flags

- A name from the wiki is in your answer and was never opened in source.
- `verified_against` / a citation says DeepWiki.
- The wiki and the source disagree, and the answer quietly follows the wiki.
- More than two lookups for one question, or a request for the whole wiki.
