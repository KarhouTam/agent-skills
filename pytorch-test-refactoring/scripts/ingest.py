"""Deterministic PR-feedback harvesting for the ingest sidecar module.

Fetches reviewer comments from KarhouTam's merged [Test] PRs via the
GitHub REST API (through the `gh` CLI), filters to replied inline
threads and claude[bot] summaries, and returns structured comment
objects. No AI logic here — analysis lives in the agent prompts.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from state import FeedbackComment, IngestState
from utils import get_ingest_workspace

CLAUDE_BOT_LOGIN = "claude[bot]"  # mirror of utils.CLAUDE_BOT_LOGIN


def _run_gh(*args: str) -> str:
    """Run a gh CLI command and return stdout."""
    result = subprocess.run(
        ["gh", *args], check=True, capture_output=True, text=True
    )
    return result.stdout


def _gh_json(endpoint: str, *extra_args: str) -> list[dict]:
    """Call `gh api <endpoint>` and return parsed JSON as a list."""
    try:
        out = _run_gh("api", endpoint, *extra_args)
        return json.loads(out) if out.strip() else []
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return []


def discover_merged_prs(author: str = "KarhouTam", title_prefix: str = "[Test]") -> list[dict]:
    """Return merged PRs by `author` whose title starts with `title_prefix`.

    Uses the `Merged` label (PyTorch's merge discriminator), not state=merged.
    Uses `gh search prs --json` — the search REST endpoint rejects
    `gh api search/issues -f q=...` with HTTP 404, so we go through the
    higher-level `gh search` CLI instead.
    """
    try:
        out = _run_gh(
            "search", "prs",
            "--repo", "pytorch/pytorch",
            "--author", author,
            "--label", "Merged",
            "--limit", "100",
            "--json", "number,title,closedAt",
        )
    except subprocess.CalledProcessError:
        return []

    try:
        items = json.loads(out)
    except json.JSONDecodeError:
        return []

    prs: list[dict] = []
    for item in items:
        title = item.get("title", "")
        if title.startswith(title_prefix):
            prs.append(
                {
                    "number": item.get("number"),
                    "title": title,
                    "closed_at": item.get("closedAt", ""),
                }
            )
    return prs


def fetch_inline_review_comments(pr_number: int) -> list[dict]:
    """Fetch inline review comments for a PR (Pull Request Review Comments API)."""
    return _gh_json(f"/repos/pytorch/pytorch/pulls/{pr_number}/comments")


def fetch_claude_summaries(pr_number: int) -> list[dict]:
    """Fetch claude[bot] issue-comment summaries for a PR.

    Returns ALL issue comments; the caller filters by author, or we
    filter here for clarity. We filter here.
    """
    comments = _gh_json(f"/repos/pytorch/pytorch/issues/{pr_number}/comments")
    return [c for c in comments if (c.get("user") or {}).get("login") == CLAUDE_BOT_LOGIN]


def _is_replied_thread(comment: dict, all_inline: list[dict]) -> bool:
    """True if `comment` is part of a replied thread.

    A thread is 'replied' if any comment has `in_reply_to_id` pointing at
    another comment in the set, OR is itself a reply.
    """
    ids = {c.get("id") for c in all_inline}
    if comment.get("in_reply_to_id") in ids:
        return True
    # has a reply: another comment's in_reply_to_id == this comment's id
    cid = comment.get("id")
    return any(c.get("in_reply_to_id") == cid for c in all_inline)


def to_feedback_comments(
    comments: list[dict],
    pr_number: int,
    source: str = "inline_review",
) -> list[FeedbackComment]:
    """Convert raw API comment dicts into FeedbackComment models.

    For inline_review source, drops comments that are not in a replied
    thread. For claude_summary source, the caller has already filtered
    by author.
    """
    result: list[FeedbackComment] = []
    for c in comments:
        if source == "inline_review" and not _is_replied_thread(c, comments):
            continue
        user = c.get("user") or {}
        result.append(
            FeedbackComment(
                comment_id=c.get("id", 0),
                pr_number=pr_number,
                author=user.get("login", ""),
                body=c.get("body", ""),
                html_url=c.get("html_url", ""),
                created_at=c.get("created_at", ""),
                source=source,
                is_reply=bool(c.get("in_reply_to_id")),
                in_reply_to_id=c.get("in_reply_to_id") or 0,
            )
        )
    return result


def load_state(workspace: Path) -> IngestState:
    """Load IngestState from workspace/state.json, or a fresh state."""
    path = workspace / "state.json"
    if not path.exists():
        return IngestState()
    try:
        return IngestState.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:
        return IngestState()


def save_state(state: IngestState, workspace: Path) -> None:
    """Persist IngestState to workspace/state.json."""
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "state.json").write_text(
        state.model_dump_json(indent=2), encoding="utf-8"
    )


def _parse_iso(ts: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp, tolerating the 'Z' suffix."""
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def filter_new(comments: list[FeedbackComment], state: IngestState) -> list[FeedbackComment]:
    """Return comments not yet processed and newer than the per-PR cursor."""
    fresh: list[FeedbackComment] = []
    for c in comments:
        if c.comment_id in state.processed_comment_ids:
            continue
        cursor = _parse_iso(state.pr_timestamps.get(str(c.pr_number), ""))
        if _parse_iso(c.created_at) <= cursor:
            continue
        fresh.append(c)
    return fresh


def harvest(
    author: str = "KarhouTam", title_prefix: str = "[Test]"
) -> tuple[list[FeedbackComment], IngestState]:
    """Discover merged [Test] PRs, fetch new replied comments, return them.

    Pure fetch + filter: this does NOT mutate or persist state. Marking
    comments as processed is deferred to `finalize_harvest` so the
    triage -> draft pipeline can resume across process invocations.
    """
    workspace = get_ingest_workspace()
    state = load_state(workspace)
    collected: list[FeedbackComment] = []

    prs = discover_merged_prs(author, title_prefix)
    for pr in prs:
        pr_number = int(pr.get("number", 0))
        if not pr_number:
            continue
        inline = to_feedback_comments(fetch_inline_review_comments(pr_number), pr_number, "inline_review")
        summaries = to_feedback_comments(fetch_claude_summaries(pr_number), pr_number, "claude_summary")
        collected.extend(inline + summaries)

    fresh = filter_new(collected, state)
    return fresh, state


def finalize_harvest(comments: list[FeedbackComment]) -> IngestState:
    """Mark harvested comments processed and bump per-PR timestamps.

    Called once the ingest pipeline reaches DONE. Bumps the cursor even
    when a comment yielded no finding, so the next run does not rescan
    the backlog.
    """
    workspace = get_ingest_workspace()
    state = load_state(workspace)
    now = datetime.now(timezone.utc).isoformat()
    for c in comments:
        state.processed_comment_ids.add(c.comment_id)
        state.pr_timestamps[str(c.pr_number)] = now
    state.last_run_at = now
    save_state(state, workspace)
    return state


# ── findings.md writer + CHANGELOG appender ─────────────────────────


_FINDINGS_TEMPLATE = """# PR Feedback Findings — PR #{pr_number}

Source PR: {pr_url}
Generated by the feedback-ingest sidecar module. Review each finding,
change the checkboxes to mark status, then run:
  python orchestrator.py --apply-ingest {findings_path}

{findings_body}
"""

_FINDING_TEMPLATE = """### Finding {fid} ({tier})

- **Source:** [{author}]({html_url})
- **Summary:** {summary}
- **Target layers:** {target_layers}
- **Proposed edits:**
{edits}

- [ ] Approved
- [ ] Rejected
- [ ] Modified (edit the intent above, then approve)

"""


def write_findings_md(findings: list[FeedbackFinding], workspace: Path) -> Path:
    """Write findings to workspace/findings/PR-<pr>.md. Returns the file path."""
    findings_dir = workspace / "findings"
    findings_dir.mkdir(parents=True, exist_ok=True)
    if not findings:
        return findings_dir / "empty.md"
    pr_number = findings[0].pr_number
    body_parts = []
    for f in findings:
        edits = "\n".join(
            f"  - `{e.get('layer', '?')}`: {e.get('intent', '')}" for e in f.proposed_edits
        )
        body_parts.append(
            _FINDING_TEMPLATE.format(
                fid=f.id, tier=f.tier, author=f.author, html_url=f.html_url,
                summary=f.summary,
                target_layers=", ".join(f.target_layers),
                edits=edits,
            )
        )
    path = findings_dir / f"PR-{pr_number}.md"
    path.write_text(
        _FINDINGS_TEMPLATE.format(
            pr_number=pr_number,
            pr_url=f"https://github.com/pytorch/pytorch/pull/{pr_number}",
            findings_path=path,
            findings_body="\n".join(body_parts),
        ),
        encoding="utf-8",
    )
    return path


def append_changelog(findings: list[FeedbackFinding], changelog_path: Path) -> None:
    """Prepend a dated CHANGELOG entry summarizing applied findings."""
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"## {today} — Reviewer feedback ingest (applied)", ""]
    for f in findings:
        lines.append(
            f"- **{f.tier}** [{f.id}]({f.html_url}) — {f.summary} "
            f"(target: {', '.join(f.target_layers) or 'n/a'})"
        )
    lines.append("")
    entry = "\n".join(lines)
    existing = changelog_path.read_text(encoding="utf-8") if changelog_path.exists() else "# Changelog\n"
    # insert after the leading "# Changelog" heading
    if existing.startswith("# Changelog"):
        existing = existing[len("# Changelog"):].lstrip("\n")
        changelog_path.write_text("# Changelog\n\n" + entry + "\n" + existing, encoding="utf-8")
    else:
        changelog_path.write_text(entry + "\n" + existing, encoding="utf-8")
