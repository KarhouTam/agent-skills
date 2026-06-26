You are debugging CI failures on a PyTorch test refactoring PR.

## Context Files

All paths are relative to the workspace: `{workspace}`

| File | Content |
|------|---------|
| `ci_failures.json` | Failed check runs with log excerpts and bot labels |
| `bot_comment.json` | Raw pytorch-bot PR comment + best-effort parsed hints |
| `analyst_report.json` | Original analysis and strategy assignments |
| `final_summary.md` | Refactoring summary from Phase 7 |

## Task

1. **Read** `ci_failures.json` and `bot_comment.json` from the workspace
2. **For each failure**, classify:
   - `CAUSED_BY_US` — The refactoring introduced this failure. Fix the code.
   - `UNRELATED` — Pre-existing, flaky, infrastructure, or not our fault. Skip.
3. **Read** `analyst_report.json` to understand the original strategy assignments. Common fix patterns:
   - XPU/MPS can't run a test moved to S2 → revert to S3
   - Wrong decorator applied → fix decorator
   - Import breakage from removed TEST_CUDA/TEST_MPS → restore needed imports
   - Class rename broke DecorateInfo or dynamo references → update references
4. **Apply fixes** to `{file_path}`
5. **Commit and push**:
   ```
   git add {file_path}
   git commit -m "fix: address CI failures from refactoring"
   git push
   ```

## Report Format

Output a structured report with `fixes_applied` and `unrelated` sections.

For each fix: check_name, failure description, verdict (caused_by_us), rationale, code change.
For each unrelated: check_name, failure description, verdict (unrelated), rationale.
