This file provides guidance to agent when working with code in this repository.

## What this repo is

A personal collection of agent skills.

## The two-tier model

The repo deliberately mixes two kinds of content, and this split drives how it is maintained:

- **Self-developed skills** are version-controlled and live in git.
- **Public skills** are fetched from upstream repos at pinned commits. They sit on disk but
  are **deliberately untracked** — `git status` will not report them.

`skills.json` is the manifest for the public tier; `sync_skills.py` materialises it.

## `.gitignore` is a whitelist — read before adding a skill

`.gitignore` ignores `/*` and re-includes only what should be tracked:

```gitignore
/*
!/.gitignore
!/README.md
!/CLAUDE.md
!/skills.json
!/sync_skills.py
!/<self-developed-skill>/
```

A new self-developed skill stays **silently untracked** until its directory is added here;
`git status` gives no hint, since the whole path is ignored. Adding a skill to the public
tier requires no `.gitignore` change at all — that is the point of the whitelist.

## Syncing public skills

```bash
python3 sync_skills.py --list           # manifest, with present/missing per skill
python3 sync_skills.py                  # install anything missing, skip what exists
python3 sync_skills.py --force          # refresh every skill to its pinned revision
python3 sync_skills.py --only handoff   # one skill (repeatable)
```

`--force` overwrites the destination directories. Public skill directories are pure upstream
copies — never edit them in place, since the next sync replaces them. To diverge from
upstream, add a local overlay (below); to take the skill over entirely, promote it to the
self-developed tier and drop it from the manifest.

Stdlib only: no `git`, `jq`, `gh`, or PyYAML required. Downloads carry a 30s timeout and
retry, because codeload connections here have been observed to stall.

## Local overlays on public skills

A public skill that needs local adaptation keeps the adaptation in `overlays/<skill>/`,
which is version-controlled (`.gitignore` whitelists `!/overlays/`) and mirrors the skill's
own layout. `sync_skills.py` copies it over the fetched skill on every install or refresh,
so `--force` restores the adaptation instead of discarding it. `using-superpowers` is the
only one today: a DeepSeek Harness reference plus its line in the Platform Adaptation list.

Edits to upstream *text* cannot be expressed by a mirror copy, so `overlays/<skill>/overlay.json`
carries declarative rules:

```json
{ "insertAfter": [
  { "file": "SKILL.md", "after": "<an exact upstream line>", "text": "<line to insert after it>" } ] }
```

Rules are idempotent — one whose `text` is already present is skipped, which is also how an
upstream that adopts the same line avoids a duplicate — and they fail loudly when the anchor
disappears, so bumping a `ref` surfaces a reshaped file instead of silently dropping the
adaptation. An overlay is applied only when the script writes the skill; a plain sync of an
already-present skill reports `SKIPPED` and touches nothing.

## `skills.json`

Each source pins one exact commit SHA, so syncs are reproducible; bumping a `ref` is the only
way to update that source. The inner map is destination directory → upstream path, plus an
optional `interface` block:

```json
{ "repo": "owner/name", "ref": "<sha>",
  "skills": { "<dest>": { "path": "<path in repo>", "interface": { "display_name": "...", "short_description": "..." } } } }
```

## `agents/openai.yaml`

Codex-style skill metadata. Sources that ship their own (`mattpocock/skills`, `openai/skills`)
keep theirs verbatim, including any `policy.allow_implicit_invocation: false`. For sources
that ship none (`obra/superpowers`), `sync_skills.py` generates the file from the manifest's
`interface` block, **but only when absent** — so it is rewritten on every sync and must not be
hand-edited; change `skills.json` instead.

In `SKILL.md` frontmatter, `disable-model-invocation: true` is the counterpart of
`policy.allow_implicit_invocation: false`; keep the two in sync.

## Subproject: `pytorch-test-refactoring/`

Self-contained and far larger than anything else here — a Python state machine with its own
prompts, deterministic scripts, and pytest suite. It has its own `CLAUDE.md` (with `AGENTS.md`
symlinked to it). Read that before touching anything inside rather than relying on this file.

```bash
cd pytorch-test-refactoring
python3 -m pytest tests/ -q                                                   # whole suite
python3 -m pytest tests/test_workflow.py::test_full_flow_replay_to_finalize -q  # single test
```

`.system/` is pre-provisioned by the Codex harness, ignored by the whitelist, and not managed
by `sync_skills.py` — leave it alone.
