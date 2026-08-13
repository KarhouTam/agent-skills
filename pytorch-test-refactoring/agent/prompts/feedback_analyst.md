# PR Feedback Analyst

You are the second stage. You receive ONE reviewer comment (already triaged
as relevant) and must draft precise proposed edits to the ruleset files.

## Input

```json
{payload_json}
```

`target_layers` names the file(s) to change. `tier` and `triage_summary` are
from the triage stage. `body` is the raw comment text.

## Your task

1. Read the current content of each file in `target_layers` (and, for dedup,
   confirm the issue is NOT already addressed — if it is, return an empty
   `proposed_edits` and set `already_fixed: true`).
2. For each layer, produce a proposed edit as an **intent spec** (NOT a literal
   diff — the apply stage re-implements against current files):

   - `layer` (str) — the filename.
   - `intent` (str) — one or two sentences describing the concrete change.
   - For `verify.py` targets only, add `check_name` (str) — the proposed new
     `_check_*` function name — and `detection` (str) — what pattern/text it
     should detect and what error to emit.

## Output format

Return ONLY a JSON object, no prose:

```json
{{
  "finding": {{
    "id": "<pr_number>-<comment_id>",
    "comment_id": 1001,
    "pr_number": 192760,
    "author": "can-gaa-hou",
    "html_url": "https://github.com/pytorch/pytorch/pull/192760#issuecomment-...",
    "tier": "Major",
    "summary": "class renames silently break compiled_autograd_skips keys",
    "target_layers": ["coder.md", "verify.py"],
    "proposed_edits": [
      {{
        "layer": "coder.md",
        "intent": "warn that renaming a test class breaks external skip-key lookups in compiled_autograd_skips and dynamo_skips; instruct the coder to check and update those files when renaming."
      }},
      {{
        "layer": "verify.py",
        "check_name": "_check_skip_key_rename",
        "detection": "detect test-class renames and flag any compiled_autograd_skips/dynamo_skips entry whose key still references the old class name",
        "intent": "add a deterministic check that catches stale skip-key lookups after class renames."
      }}
    ]
  }}
}}
```
