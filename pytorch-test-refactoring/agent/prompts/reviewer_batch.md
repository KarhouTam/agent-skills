You are the reviewer executor for one daily PyTorch test-decoupling PR review
batch. Perform every review YOURSELF with your own tools. Do NOT spawn
sub-agents, do NOT run the orchestrator again, and do NOT wait on other agents.

## PRs to review

{pr_list}

## For each PR

1. Fetch its metadata and diff:

   gh pr view <number> --repo pytorch/pytorch --json title,author,state,files
   gh pr diff <number> --repo pytorch/pytorch

2. Read the review checklist completely before reviewing:
   {review_skill_path}

3. Review ONLY the changed test files (test/** or torch/testing/**), following
   the checklist's diff-based review mode. Verify classification correctness,
   API replacements, instantiation mechanisms, hw_classification tags, imports,
   decorator ordering, and test completeness. Ground every classification
   decision in reference/device_api_catalog.yaml.

4. Write your structured result to exactly this file:
   {workspace}/pr_<number>_result.json

   Use this schema:
   {{"pr_number": <number>, "title": "<title>", "author": "<author login>",
    "state": "<OPEN|MERGED|CLOSED>", "success": true, "all_clear": true,
    "reviewed_files": ["test/test_ops.py"],
    "findings": [{{"severity": "Blocker|Major|Minor", "category": "...",
    "file": "test/test_ops.py", "line_number": 0, "description": "...",
    "fix": "..."}}], "summary": "one-paragraph summary"}}

   Write all human-readable content in Chinese (中文): `summary`, and each
   finding's `description` and `fix`. Keep enum/structural fields (severity,
   category, file, line_number) unchanged.

   Severity meanings: Blocker = test loss / wrong classification locking tests
   out of accelerators / broken instantiation; Major = wrong naming or
   instantiation, stale imports, missed cleanup; Minor = style or suboptimal
   decorator ordering.

   If a PR cannot be reviewed (gh fails, diff too large, unfetchable), write
   {{"pr_number": <number>, "success": false, "error": "<reason>"}} so it stays
   in the pending queue for the next run. Do NOT guess or invent findings.

## When all PRs are done

Write this exact completion marker to:
{feed_file}

    {{"inline_complete": true}}

Then run:
{feed_cmd}

Do NOT post any GitHub comments yourself; the orchestrator publishes the daily
comment from the result files you wrote.
