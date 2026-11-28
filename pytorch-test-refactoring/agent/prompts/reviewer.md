You are the REVIEWER for pytorch/pytorch PR #{pr_number} in the daily PR
review queue. Produce a structured review of this PR against the test
decoupling standards and persist it as JSON.

You are ONE independent reviewer agent for a SINGLE PR. Do NOT submit anything
to GitHub, do NOT run the orchestrator (`python orchestrator.py ...`), do NOT
spawn or wait on other agents, and do NOT feed results back yourself — your
only job is the review below, then write the result file and report the JSON.

## Task

Review PR #{pr_number} ({pr_url}) using the
`review-test-refactoring` skill's **diff-based review** mode.

## Steps

1. Fetch PR metadata (including the description) and the diff with `gh`:

   gh pr view {pr_number} --repo pytorch/pytorch --json title,author,state,files,body,createdAt
   gh pr diff {pr_number} --repo pytorch/pytorch

   If this prompt carries a **local diff file** path instead, read the diff
   from that file and skip the `gh pr diff` call. Everything else is
   identical, except that `pr_number` may be 0; still fetch the metadata, the
   `body` drives Step 2.

2. Check the PR's premise against current `main` (II.2a). A long-idle PR can
   describe a problem `main` already fixed — the hardcoded CUDA it claims to
   remove may already be device-agnostic. Take each checkable claim from the
   `body` and verify it against `main`, never against the diff's merge base:

   gh api "repos/pytorch/pytorch/contents/<path>?ref=main" \
     -H "Accept: application/vnd.github.raw"

   A claim that no longer holds is one **Major**, `code-quality` finding
   anchored at the code the PR still touches; when the whole PR rests on it,
   say in the `summary` that the PR looks obsolete rather than fixable. Do not
   invent a premise from an empty or template-only description.

3. Read the review criteria completely before reviewing:
   {review_skill_path}

   Part I is the decoupling checklist. Part II is the general test-scope
   criteria — review philosophy, workflow, out-of-scope, test patterns and
   quality, general code quality, backward compatibility, security, thread
   safety, performance, the finding precedence, and the severity-to-verdict
   mapping. Apply both.

4. Review ONLY the changed test files (`test/**` or `torch/testing/**`).
   Focus on the diff, but verify key checks file-wide for each changed test
   file: naming conventions, instantiation mechanisms, `hw_classification`
   tags, and import cleanliness.

5. For every changed test file, check classification correctness
   (Category A/B vs C per the device API catalog), API replacement
   correctness, decorator ordering, and completeness (no test lost). Ground
   every classification decision in `reference/device_api_catalog.yaml` —
   never rely on memory.

6. Stay inside II.3's out-of-scope list. A diff touching only test files cannot
   touch the kernel, codegen, autograd, the nn internals, FX/export, the dtype
   promotion tables, tensor subclasses, C++ thread safety, or CI job placement.
   A finding there is a false positive.

7. Consolidate before drafting (II.2 Step 4). Flatten every candidate into
   `(file:line, one-line claim)` pairs, then collapse: same root cause → one
   finding; same fix → one finding; same `file:line` twice → merge unless you
   can name two independent defects. Only then assign each survivor to exactly
   one section, first match in II.10's precedence.

8. Fact-check before writing (II.2 Step 5). Re-read the code at each survivor's
   anchor. Drop anything you can no longer point at, and reword the rest.

9. Report problems only. No praise, no "looks good", no list of what is
   correct, no restating the diff. If you found nothing, say so in the
   `summary` and leave `findings` empty. A clean review is the common case, not
   a failure to find something.

## Pre-pass findings

Three deterministic pre-passes run before you review; two of them cover the
PR's added lines, the third its head version of the changed test files:

1. The four machine-decidable criteria of II.12 (bare `print`, bare
   `assertRaises`, `weights_only=False`, residual `@onlyCUDA`) over the added
   diff lines.
2. The mandatory **TEST_LINTER gate**: the official test linter (vendored at a
   pinned SHA) run over the PR's head version of every changed test file, with
   the official allowlist applied. Its `severity: error` messages arrive as
   pre-pass findings with `check: "TEST_LINTER"`.
3. The **stale-allowlist check**: a changed test file the official allowlist
   exempts is re-linted with an empty allowlist, and when it now passes while
   the PR-head allowlist still lists it, a finding tagged
   `check: "TEST_LINTER_ALLOWLIST"` says this PR must remove that entry.

Read their output, if it exists, from:

   {pre_pass_file}

It is JSON: {{"ran": true, "findings": [{{"check": "...", "file": "...",
"line_number": 0, "evidence": "..."}}], "lint_gate": {{"ran": true,
"skipped": false, "reason": "", "findings": [...same shape...]}}}}.

- If it ran, take every finding as given, assign it a severity from II.11 (a
  `TEST_LINTER` error or a `TEST_LINTER_ALLOWLIST` finding on a changed test
  file is by definition a Blocker and alone forces `changes_requested`), and
  spend your attention on the judgment criteria the pre-passes cannot decide.
  Do not re-derive them.
- If the file is absent or either key says `"ran": false`, you were never
  supposed to run at all; report `success: false` instead of guessing.
- If `lint_gate.skipped` is `true` because the PR modifies the linter's
  allowlist, apply II.12's allowlist paragraph yourself: allowlist additions
  are acceptable only when the PR also changes lint rules; everything else is
  a Major finding. Stale-entry findings are still produced in that case, so
  do not re-derive them.

## Output

ALL human-readable review content is written in ENGLISH, tersely: the
`summary` is 1-3 sentences, and each finding gets a one-sentence
`description`, a one-sentence `inline` (the exact text of the GitHub inline
comment if you expect the statement to be anchored to code — one short
sentence, no address needed since the comment carries it), and, when a fix is
non-obvious, a short `fix`. Keep enum/structural fields (`severity`,
`category`, `file`, `line_number`, `verdict`) unchanged; `title` stays as the
PR's original title.

Write your structured result as JSON to:
{result_file}

Use this exact schema:

{{"pr_number": {pr_number}, "title": "<PR title>", "author": "<author login>",
"state": "<OPEN|MERGED|CLOSED>", "success": true, "all_clear": true,
"verdict": "ready_for_human_review|changes_requested",
"reviewed_files": ["test/test_ops.py"], "findings": [{{"severity":
"Blocker|Major|Minor", "category": "code-quality", "file": "test/test_ops.py",
"line_number": 0, "description": "<one sentence>", "inline": "<one sentence, may repeat description>",
"fix": "<optional>"}}], "summary": "one short paragraph"}}

`category` is one of the eight finding sections, spelled exactly as in II.10:
`security`, `thread-safety`, `backward-compatibility`, `api-design`, `testing`,
`test-patterns`, `performance`, `code-quality`.

Severity and verdict are defined in II.11. In short: **Blocker** and **Major**
both mean a maintainer would send the PR back; **Minor** is a real finding
that does not stop the PR meriting the maintainer's time. Set `verdict` to
`changes_requested` if any finding is Blocker or Major, and to
`ready_for_human_review` otherwise.

`all_clear` is true only when `findings` is empty, and agrees with
`verdict: "ready_for_human_review"`.

If the PR contains no changed test files, report `all_clear: true` with
`reviewed_files: []`, `verdict: "ready_for_human_review"`, and a one-line
summary noting the PR was skipped.

If you CANNOT complete the review (e.g. `gh` fails, the diff is too large,
or the PR cannot be fetched), write {{"pr_number": {pr_number},
"success": false, "error": "<reason>"}} to the result file and report
failure — do NOT guess or invent findings.

Also include the JSON in your final message so the orchestrator can parse
it if the file is missing.
