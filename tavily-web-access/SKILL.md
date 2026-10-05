---
name: tavily-web-access
description: Use when a task needs live web information — current facts, docs, releases, pricing, news — or the full text of a URL as clean markdown, especially when web_fetch is blocked, fails to resolve the hostname, or returns raw HTML, or when the session has no built-in web search.
---

# Tavily Web Access

## Overview

Tavily returns ranked results with extracted page text instead of raw HTML —
the difference between reading an article and regex-stripping a 600 KB
JavaScript bundle. Run every command through the wrapper below: it supplies the
API key and rotates to the next key when one is rejected.

## Run through the wrapper (REQUIRED)

```bash
python3 scripts/tvly.py search "query" --max-results 5
python3 scripts/tvly.py extract "https://example.com/docs" -o /tmp/page.json
python3 scripts/tvly.py crawl "https://docs.example.com" --output-dir /tmp/docs
```

Resolve `scripts/tvly.py` against this skill's base directory. It calls the
[Tavily Python SDK](https://docs.tavily.com/sdk/python/reference) directly, so
output is always JSON and any SDK parameter passes through as a flag, with
dashes turned into underscores:

| Flag | Applies to |
|---|---|
| `--max-results 5`, `--search-depth advanced`, `--topic news`, `--time-range week`, `--include-raw-content markdown` | search |
| `--include-domains a.com,b.com`, `--exclude-domains ...` | search, map, crawl |
| `--query "..."`, `--chunks-per-source 3`, `--extract-depth advanced` | extract |
| `--instructions "..."`, `--limit N`, `--max-depth N`, `--select-paths '/docs/.*'` | map, crawl |
| `-o FILE` writes the response to disk and prints a one-line summary | all |

The wrapper reads keys from `~/.tavily/keys.json` (mode 0600, outside this
repo). On its first run it fetches the key list from your personal gist and
writes that file; when the API rejects a key it advances to the next one.
`--refresh-keys` re-fetches the gist (it needs write access to `~/.tavily/`,
which a sandboxed session may refuse); `--check-keys` reports how many keys are
stored without calling the API.

It needs `tavily-python` (`pip install tavily-python`), not the `tvly` CLI —
that CLI can stay installed for interactive use, but this script does not run it.

## Escalate one step at a time

| Need | Command | Notes |
|---|---|---|
| Find pages, no URL yet | `search` | Start here |
| Content of known URLs | `extract` | Up to 20 URLs per call; renders JS pages |
| Locate a page on a large site | `map` | URLs only, cheap |
| Bulk content from a site section | `crawl` | `--output-dir` writes one markdown file per page |
| Multi-source synthesis | `research` | 30-120 s; `--no-wait` returns a request_id for `research-status` when that would outlast your command timeout |

## Spend credits deliberately

Each key has 1,000 free credits per month. `basic` depth costs 1 credit and is
the default; `advanced` costs 2. Keep `--max-results` at 5 unless you need more.
`search --include-raw-content markdown` returns full page text and often removes
the need for a separate `extract`. Do not reach for `research` when two searches
answer the question.

## Keep payloads out of context

- Save large responses with `-o /tmp/tavily-<task>.json`, then filter the file
  with `python3` and print only the evidence the answer needs — roughly 150-600
  tokens per source. Raw API JSON in context is waste, not evidence.
- After `extract`, inspect `failed_results`: exit code 0 does not mean every
  URL was extracted. Retry a failed URL with `--extract-depth advanced`; if it
  still fails, `curl` that URL directly — some hosts (docs.tavily.com among
  them) refuse extraction but serve plain HTTP fine.
- Cite the URL beside every fact you take from a page.
- Narrow with `--include-domains` when a claim must come from an official
  source, then confirm the hostname of each result you actually cite.
- Documentation sites built on Mintlify and similar (including Tavily's own
  docs) serve clean markdown when `.md` is appended to the URL — try that
  before wrestling with a heavy HTML page.

## Common mistakes

| Mistake | Correction |
|---|---|
| Running `tvly` from the shell, then hitting "No Tavily API key found" | The CLI has no stored key; `scripts/tvly.py` is the front door |
| Hand-rolling an HTTP request to api.tavily.com | The wrapper handles auth, rotation, and the IPv6 connect stall Python clients hit on some networks |
| Writing a key into the skill directory or any repo file | Keys belong only in `~/.tavily/keys.json`; never copy one into a file you author |
| `curl` plus a regex HTML stripper | `extract` returns markdown sized for a context window |
| Dumping the whole JSON response into the conversation | Save with `-o`, filter on disk, print the relevant lines |
| `web_fetch` failed with "resolves to a non-public IP address" and the task stalled | That is what `extract` is for |
| Treating a successful `extract` as complete | Read `failed_results` |
| Rotating keys by hand after a rejection | The wrapper advances the key itself, on the SDK's typed auth/limit errors |

## Red flags — stop

- A string starting with `tvly-` appears anywhere inside this repo, in a diff,
  or in a file you are about to write. Delete it and use `~/.tavily/keys.json`.
- More than a few KB of raw HTML or API JSON is in your context.
- You are about to reach for `curl` plus a regex on a page you have not tried
  `extract` on.
- Every key was rejected: report the last error and say that the monthly
  credits are likely exhausted, rather than falling back to scraping silently.
  A `RequestsDependencyWarning` on stderr is unrelated environment noise.
