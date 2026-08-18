"""Deterministic PR review queue logic for the review-queue sidecar.

Handles the mechanical parts of the daily batch review: loading the pending
PR list, classifying PRs via `gh` (open/merged/closed, test-file changes),
rendering the daily issue comment, posting it, archiving processed PRs, and
rewriting the pending list. No AI logic here — reviews are driven by
agent/prompts/reviewer.md (Claude sub-agent) or
agent/prompts/reviewer_batch.md (Codex inline executor).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from state import PrReviewItem, PrReviewResult
from utils import (
    PR_QUEUE_FILE,
    PR_REVIEW_ISSUE_NUMBER,
    PR_REVIEW_ISSUE_REPO,
    PR_REVIEW_TEST_PREFIXES,
    PR_REVIEW_WORKSPACE_ROOT,
    PR_REVIEWED_ARCHIVE_FILE,
)

_PR_URL_RE = re.compile(r"pull[s]?/(\d+)")

_REASON_CN = {
    "merged/closed": "已 merged/closed",
    "no_test_changes": "未改动 test/** 或 torch/testing/**",
}


@dataclass
class SelectResult:
    """Partition of one selection pass over the pending list."""

    review_queue: list[PrReviewItem] = field(default_factory=list)
    not_applicable: list[PrReviewItem] = field(default_factory=list)
    failed: list[PrReviewItem] = field(default_factory=list)


# ── gh helpers ──────────────────────────────────────────────────────


def _run_gh(*args: str) -> str:
    """Run a gh CLI command and return stdout (raises on non-zero exit)."""
    result = subprocess.run(
        ["gh", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def gh_pr_info(pr_number: int) -> dict | None:
    """Fetch PR metadata via `gh pr view`. Returns None on any failure."""
    try:
        out = _run_gh(
            "pr",
            "view",
            str(pr_number),
            "--repo",
            "pytorch/pytorch",
            "--json",
            "number,title,author,state,url,files",
        )
        data = json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None
    author = (data.get("author") or {}) or {}
    files = data.get("files") or []
    return {
        "number": data.get("number", pr_number),
        "title": data.get("title", ""),
        "author": author.get("login", ""),
        "state": data.get("state", ""),
        "url": data.get("url", f"https://github.com/pytorch/pytorch/pull/{pr_number}"),
        "paths": [f.get("path", "") for f in files],
    }


def has_test_changes(paths: list[str]) -> bool:
    """True if any changed path is a test file (test/** or torch/testing/**)."""
    return any(p.startswith(PR_REVIEW_TEST_PREFIXES) for p in paths)


# ── pending list ────────────────────────────────────────────────────


def parse_pr_url(url: str) -> int | None:
    """Extract the PR number from a GitHub PR URL, or None."""
    m = _PR_URL_RE.search(url)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def load_pending(path: Path | str | None = None) -> list[str]:
    """Load the pending PR URLs, deduped and in file order."""
    path = Path(path) if path else PR_QUEUE_FILE
    if not path.exists():
        return []
    urls: list[str] = []
    seen: set[str] = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        url = raw.strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


def save_pending(urls: list[str], path: Path | str | None = None) -> None:
    """Rewrite the pending list file (newline-terminated, deduped)."""
    path = Path(path) if path else PR_QUEUE_FILE
    seen: set[str] = set()
    lines: list[str] = []
    for url in urls:
        url = url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        lines.append(url)
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def select_pending(
    limit: int = 10,
    pending_path: Path | str | None = None,
) -> SelectResult:
    """Classify pending PRs FIFO and select up to `limit` reviewable ones.

    - OPEN PRs with test-file changes → review_queue (up to `limit`).
    - merged/closed PRs or OPEN PRs without test-file changes →
      not_applicable (archived this run, mentioned in the comment).
    - PRs whose metadata could not be fetched → failed (stays pending,
      never mentioned in the comment).
    Scanning stops once `limit` reviewable PRs are found; everything after
    stays pending untouched.
    """
    result = SelectResult()
    pending = load_pending(pending_path)
    for url in pending:
        if len(result.review_queue) >= limit:
            break
        pr_number = parse_pr_url(url)
        if not pr_number:
            result.failed.append(
                PrReviewItem(url=url, pr_number=0, status="failed", reason="bad_url")
            )
            continue
        info = gh_pr_info(pr_number)
        if info is None:
            result.failed.append(
                PrReviewItem(
                    url=url, pr_number=pr_number, status="failed", reason="fetch_failed"
                )
            )
            continue
        item = PrReviewItem(
            url=info["url"],
            pr_number=pr_number,
            title=info["title"],
            author=info["author"],
            state=info["state"],
            has_test_changes=has_test_changes(info["paths"]),
        )
        if item.state != "OPEN":
            item.status = "na"
            item.reason = "merged/closed"
            result.not_applicable.append(item)
        elif not item.has_test_changes:
            item.status = "na"
            item.reason = "no_test_changes"
            result.not_applicable.append(item)
        else:
            result.review_queue.append(item)
    return result


# ── archive ─────────────────────────────────────────────────────────


def load_archive(path: Path | str | None = None) -> dict:
    """Load the processed-PR archive. Returns {'records': [], 'last_run': {}}."""
    path = Path(path) if path else PR_REVIEW_WORKSPACE_ROOT / PR_REVIEWED_ARCHIVE_FILE
    if not path.exists():
        return {"records": [], "last_run": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"records": [], "last_run": {}}
    if not isinstance(data, dict):
        return {"records": [], "last_run": {}}
    data.setdefault("records", [])
    data.setdefault("last_run", {})
    return data


def save_archive(archive: dict, path: Path | str | None = None) -> None:
    """Persist the processed-PR archive."""
    path = Path(path) if path else PR_REVIEW_WORKSPACE_ROOT / PR_REVIEWED_ARCHIVE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(archive, indent=2, ensure_ascii=False), encoding="utf-8")


# ── comment rendering ───────────────────────────────────────────────


def _verdict(result: PrReviewResult) -> str:
    if result.all_clear:
        return "通过"
    counts: dict[str, int] = {}
    for f in result.findings:
        counts[f.severity] = counts.get(f.severity, 0) + 1
    parts = [f"{counts[k]} {k}" for k in ("Blocker", "Major", "Minor") if counts.get(k)]
    return " · ".join(parts) if parts else "有发现（未分类）"


def _render_reviewed(result: PrReviewResult) -> str:
    files = ", ".join(result.reviewed_files) if result.reviewed_files else "（无）"
    if result.findings:
        items = "\n".join(
            f"  - [{f.severity}] `{f.file}:{f.line_number}`：{f.description}"
            + (f"（修复：{f.fix}）" if f.fix else "")
            for f in result.findings
        )
    else:
        items = "  - 未发现问题"
    summary = result.summary or "—"
    return (
        "<details>\n"
        f"<summary>pytorch/pytorch#{result.pr_number} · {result.title} · "
        f"@{result.author} · 结论：{_verdict(result)}</summary>\n\n"
        f"- **URL**: https://github.com/pytorch/pytorch/pull/{result.pr_number}\n"
        f"- **State**: {result.state}\n"
        f"- **Review mode**: diff-based（review-test-refactoring）\n"
        f"- **Reviewed files**: {files}\n"
        f"- **Summary**: {summary}\n"
        f"- **Findings**:\n{items}\n"
        "</details>"
    )


def _render_na(item: PrReviewItem) -> str:
    reason_cn = _REASON_CN.get(item.reason, item.reason)
    return (
        "<details>\n"
        f"<summary>pytorch/pytorch#{item.pr_number} · {item.title} · "
        f"不适用（{reason_cn}）</summary>\n\n"
        f"- **URL**: {item.url}\n"
        f"- **State**: {item.state}\n"
        f"- 该 PR {reason_cn}，跳过解耦检查。\n"
        "</details>"
    )


def render_comment(
    date_str: str,
    reviewed: list[PrReviewResult],
    not_applicable: list[PrReviewItem],
) -> str:
    """Render the daily comment: one collapsed <details> block per PR."""
    blocks = [_render_reviewed(r) for r in reviewed]
    blocks.extend(_render_na(item) for item in not_applicable)
    footer = (
        f"_本批 {len(reviewed) + len(not_applicable)} 个："
        f"reviewed {len(reviewed)} · 不适用 {len(not_applicable)}_"
    )
    return (
        f"## Daily Review — {date_str}\n\n"
        + ("\n\n".join(blocks) + "\n\n" if blocks else "")
        + footer
        + "\n"
    )


# ── publishing ──────────────────────────────────────────────────────


def post_comment(comment_path: Path | str) -> str:
    """Post a comment body file to the tracking issue. Returns the comment URL."""
    out = _run_gh(
        "issue",
        "comment",
        str(PR_REVIEW_ISSUE_NUMBER),
        "--repo",
        PR_REVIEW_ISSUE_REPO,
        "--body-file",
        str(comment_path),
    )
    return out.strip()


def publish_batch(
    workspace: Path,
    reviewed: list[PrReviewResult],
    not_applicable: list[PrReviewItem],
    date_str: str | None = None,
) -> dict:
    """Post the daily comment, archive processed PRs, rewrite the pending list.

    Order matters for crash safety: the comment is posted first; only after a
    successful post are the archive and pending list mutated, so a failed post
    never removes PRs from the queue.
    """
    date_str = date_str or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    workspace.mkdir(parents=True, exist_ok=True)
    comment_path = workspace / f"comment_{date_str}.md"
    comment_path.write_text(
        render_comment(date_str, reviewed, not_applicable), encoding="utf-8"
    )
    comment_url = post_comment(comment_path)

    records: list[dict] = []
    for r in reviewed:
        records.append(
            {
                "url": f"https://github.com/pytorch/pytorch/pull/{r.pr_number}",
                "pr_number": r.pr_number,
                "title": r.title,
                "author": r.author,
                "state": r.state,
                "date": date_str,
                "comment_url": comment_url,
                "status": "reviewed",
                "reason": "",
                "findings_count": len(r.findings),
                "all_clear": r.all_clear,
                "summary": r.summary,
            }
        )
    for item in not_applicable:
        records.append(
            {
                "url": item.url,
                "pr_number": item.pr_number,
                "title": item.title,
                "author": item.author,
                "state": item.state,
                "date": date_str,
                "comment_url": comment_url,
                "status": "na",
                "reason": item.reason,
                "findings_count": 0,
                "all_clear": None,
                "summary": "",
            }
        )

    archive_path = workspace / PR_REVIEWED_ARCHIVE_FILE
    archive = load_archive(archive_path)
    archive["records"].extend(records)
    archive["last_run"] = {"date": date_str, "comment_url": comment_url}
    save_archive(archive, archive_path)

    remove = {r.pr_number for r in reviewed} | {i.pr_number for i in not_applicable}
    pending = [u for u in load_pending() if parse_pr_url(u) not in remove]
    save_pending(pending)

    return {
        "comment_url": comment_url,
        "comment_path": str(comment_path),
        "records": records,
    }
