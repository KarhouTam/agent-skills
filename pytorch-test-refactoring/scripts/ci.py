"""Deterministic CI operations for Phase 8 (CI automation).

Handles check-run fetching and pytorch-bot comment fetch/parse. Called by
ci_ops.py — no AI logic in this module. Creating and pushing the PR (and
marking it ready) is user-driven, so no helpers for that live here.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from state import CICheckRun, CIBotHints, CIFailure
from utils import run_gh as _run_gh


# ── CI check-run fetching ───────────────────────────────────────────


def get_check_runs(ref: str) -> list[CICheckRun]:
    """Fetch all check runs for a given git ref (commit SHA).

    Uses `gh api` to call the GitHub Checks API.
    """
    try:
        output = _run_gh(
            "api",
            f"/repos/pytorch/pytorch/commits/{ref}/check-runs",
            "--paginate",
            "-q",
            ".check_runs[] | {name: .name, status: .status, conclusion: .conclusion, html_url: .html_url}",
        )
        if not output.strip():
            return []
        runs = []
        for line in output.strip().split("\n"):
            try:
                data = json.loads(line)
                runs.append(
                    CICheckRun(
                        name=data.get("name", ""),
                        status=data.get("status", ""),
                        conclusion=data.get("conclusion", "") or "",
                        html_url=data.get("html_url", ""),
                    )
                )
            except json.JSONDecodeError:
                continue
        return runs
    except subprocess.CalledProcessError as e:
        import sys

        print(
            f"ci_ops: get_check_runs failed for ref={ref[:12]}: {e}",
            file=sys.stderr,
        )
        return []


# ── pytorch-bot comment ─────────────────────────────────────────────


def get_bot_comment(pr_number: int) -> str:
    """Fetch the latest comment from pytorch-bot on the PR.

    Returns the raw markdown body, or empty string if none found.
    """
    # Fetch every page: the API returns comments oldest-first, so reading
    # only the first page would return a stale bot comment (or none).
    try:
        output = _run_gh(
            "api",
            f"/repos/pytorch/pytorch/issues/{pr_number}/comments",
            "--paginate",
            "--slurp",
        )
    except subprocess.CalledProcessError:
        return ""
    if not output.strip():
        return ""
    try:
        pages = json.loads(output)
    except json.JSONDecodeError:
        return ""
    if not isinstance(pages, list):
        return ""
    if pages and not all(isinstance(page, list) for page in pages):
        pages = [pages]
    comments = [c for page in pages for c in page if isinstance(c, dict)]

    for bot_name in ("pytorch-bot", "pytorchmergebot"):
        bodies = [
            (c.get("body") or "").strip()
            for c in comments
            if (c.get("user") or {}).get("login") == bot_name
        ]
        if bodies:
            return bodies[-1]
    return ""


def parse_bot_comment(raw_comment: str) -> CIBotHints:
    """Best-effort parse of the pytorch-bot comment.

    Extracts hints about flaky checks, unrelated failures, and
    trunk-broken checks. This is NON-BLOCKING: if parsing fails,
    the raw comment is preserved in the hints object.
    """
    hints = CIBotHints(raw_comment=raw_comment)
    if not raw_comment:
        return hints

    # Pattern 1: "flaky" mentions
    flaky_patterns = [
        r'(?:check|workflow|test)\s+"?([^"]+?)"?\s+(?:is|appears|seems)\s+flaky',
        r'(?:flaky|unstable):\s*"?([^"\n]+)"?',
    ]
    for pattern in flaky_patterns:
        for match in re.finditer(pattern, raw_comment, re.IGNORECASE):
            name = match.group(1).strip()
            if name and name not in hints.flaky_checks:
                hints.flaky_checks.append(name)

    # Pattern 2: "unrelated" / "not caused by this PR"
    unrelated_patterns = [
        r'(?:failure|check|workflow)\s+"?([^"]+?)"?\s+(?:is|appears)\s+(?:unrelated|not caused)',
        r'(?:unrelated|not caused by this PR):\s*"?([^"\n]+)"?',
    ]
    for pattern in unrelated_patterns:
        for match in re.finditer(pattern, raw_comment, re.IGNORECASE):
            name = match.group(1).strip()
            if name and name not in hints.unrelated_failures:
                hints.unrelated_failures.append(name)

    # Pattern 3: "broken on trunk"
    trunk_patterns = [
        r'(?:check|workflow|test)\s+"?([^"\n]+?)"?\s+(?:is|appears|was)\s+(?:broken|failing)\s+on\s+trunk',
        r'(?:broken on trunk|trunk failure):\s*"?([^"\n]+)"?',
    ]
    for pattern in trunk_patterns:
        for match in re.finditer(pattern, raw_comment, re.IGNORECASE):
            name = match.group(1).strip()
            if name and name not in hints.trunk_broken:
                hints.trunk_broken.append(name)

    return hints


# ── CI state classification ─────────────────────────────────────────


def classify_ci_state(
    check_runs: list[CICheckRun],
    bot_hints: CIBotHints,
) -> tuple[str, list[CIFailure]]:
    """Classify the overall CI state.

    Returns (verdict, failures) where verdict is one of:
      - "all_pass": all checks green
      - "all_excused": all failures are excused by bot hints
      - "failures": some failures are not excused, need debugger
      - "pending": some checks still running, OR no check runs fetched
    """
    if not check_runs:
        # Empty check runs list means the API call likely failed (network
        # error, rate limit, etc.).  Assume pending rather than falsely
        # declaring all_pass — a false "done" signal would kill the
        # cron job and abandon CI monitoring.
        return "pending", []

    all_pass = True
    has_pending = False
    failures: list[CIFailure] = []

    for run in check_runs:
        if run.status in ("queued", "in_progress"):
            has_pending = True
            all_pass = False
            continue

        if run.conclusion in ("success", "neutral", "skipped"):
            continue

        # Every other completed conclusion is a failure. Treating an unknown
        # one (startup_failure, action_required, stale, "") as green would
        # report a broken workflow as all_pass and stop CI monitoring.
        all_pass = False
        bot_label = ""
        if run.name in bot_hints.flaky_checks:
            bot_label = "flaky"
        elif run.name in bot_hints.unrelated_failures:
            bot_label = "unrelated"
        elif run.name in bot_hints.trunk_broken:
            bot_label = "trunk_broken"

        failures.append(
            CIFailure(
                check_name=run.name,
                bot_label=bot_label,
            )
        )

    if has_pending:
        return "pending", failures
    if all_pass:
        return "all_pass", []
    if failures and all(f.bot_label for f in failures):
        return "all_excused", failures
    return "failures", failures


# -- Intermediate JSON files for debugger agent -----------------------


def write_ci_failures_json(
    failures: list[CIFailure],
    workspace: Path,
) -> str:
    """Write CI failures to a JSON file for the debugger to read.

    Returns the file path relative to workspace.
    """
    from datetime import datetime, timezone

    path = workspace / "ci_failures.json"
    data = {
        "total_failed": len(failures),
        "failures": [
            {
                "check_name": f.check_name,
                "log_excerpt": f.log_excerpt,
                "bot_label": f.bot_label,
            }
            for f in failures
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def write_bot_comment_json(
    bot_hints: CIBotHints,
    workspace: Path,
) -> str:
    """Write the bot comment and parsed hints to a JSON file.

    Returns the file path relative to workspace.
    """
    from datetime import datetime, timezone

    path = workspace / "bot_comment.json"
    data = {
        "raw_comment": bot_hints.raw_comment,
        "flaky_checks": bot_hints.flaky_checks,
        "unrelated_failures": bot_hints.unrelated_failures,
        "trunk_broken": bot_hints.trunk_broken,
        "parse_warning": (
            "This parse is automated and may be incomplete. "
            "Always verify against raw_comment."
        ),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)
