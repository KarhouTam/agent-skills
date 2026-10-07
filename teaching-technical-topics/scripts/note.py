#!/usr/bin/env python3
"""Keep the teaching notes consistent: one command instead of four hand edits.

A note is `<notes>/<project>/<topic>.md` with flat YAML frontmatter. Its id is
`project/topic` (bare slugs collide across projects).

    python3 note.py add <notes>/<project>/<topic>.md [--related vllm/scheduler]...
    python3 note.py promote <notes>/.drafts/<project>/<topic>.md [--related ...]...
    python3 note.py check <notes>

A lesson is taught into a **draft** under `<notes>/.drafts/` and only becomes
reference material once the user confirms it was any good and that the lesson is
over. `promote` is that archiving step: it moves the draft into place and runs the
full `add` pass. Drafts are invisible to `check`'s link graph and to readers, so an
unconfirmed or later-revised explanation can never poison a future lesson.

`add` is idempotent and does all of the bookkeeping in one pass:
  - normalises frontmatter (date defaults to today)
  - regenerates INDEX.md from every note's frontmatter
  - creates the project's _project.md if missing
  - makes every `related` link bidirectional, both ways in frontmatter and as a
    `- → [...]` bullet in the body's `## 已建立的连接` section

`check` exits non-zero on: missing/invalid frontmatter, a dangling `related` id, a
one-way link, a broken relative link in any note body, or an INDEX that has drifted.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

REQUIRED = ("project", "topic", "date", "nutshell", "related", "verified_against", "confidence")
CONFIDENCE = ("verified", "background")
DRAFT_DIR = ".drafts"
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        raise ValueError("no frontmatter block")
    end = text.find("\n---", 4)
    if end == -1:
        raise ValueError("frontmatter block is not closed")
    fields: dict[str, object] = {}
    for line in text[4:end].split("\n"):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"frontmatter line is not `key: value`: {line!r}")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fields[key.strip()] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        else:
            fields[key.strip()] = value
    return fields, text[end + 4 :].lstrip("\n")


def render_frontmatter(fields: dict) -> str:
    lines = []
    for key in REQUIRED:
        value = fields.get(key, [] if key == "related" else "")
        lines.append(f"{key}: [{', '.join(value)}]" if isinstance(value, list) else f"{key}: {value}")
    for key, value in fields.items():
        if key not in REQUIRED:
            lines.append(f"{key}: {value}")
    return "---\n" + "\n".join(lines) + "\n---\n"


def note_id(path: str, root: str) -> str:
    rel = os.path.relpath(path, root)
    return rel[: -len(".md")].replace(os.sep, "/")


def project_of(nid: str) -> str:
    return nid.split("/")[0]


def note_path(root: str, nid: str) -> str:
    return os.path.join(root, *nid.split("/")) + ".md"


def normalize_id(nid: str, project: str) -> str:
    """A bare topic means 'same project'; canonical ids are always project/topic."""
    return nid if "/" in nid else f"{project}/{nid}"


def load_notes(root: str) -> dict[str, tuple[dict, str]]:
    """Map every *active* topic id -> (fields, body). Drafts are not reference."""
    notes: dict[str, tuple[dict, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != DRAFT_DIR]
        for name in sorted(filenames):
            if not name.endswith(".md") or name in ("INDEX.md", "_project.md"):
                continue
            path = os.path.join(dirpath, name)
            nid = note_id(path, root)
            try:
                notes[nid] = parse_frontmatter(open(path, encoding="utf-8").read())
            except ValueError as exc:
                raise SystemExit(f"error: {path}: {exc}")
    return notes


def draft_path(root: str, nid: str) -> str:
    return os.path.join(root, DRAFT_DIR, *nid.split("/")) + ".md"


def find_drafts(root: str) -> dict[str, str]:
    """Map topic id -> draft path. Drafts await the user's confirmation."""
    base = os.path.join(root, DRAFT_DIR)
    drafts: dict[str, str] = {}
    for dirpath, _, filenames in os.walk(base):
        for name in sorted(filenames):
            if name.endswith(".md") and name != "_project.md":
                path = os.path.join(dirpath, name)
                drafts[note_id(path, base)] = path
    return drafts


def missing_note_error(root: str, nid: str) -> str:
    hint = ""
    if os.path.exists(draft_path(root, nid)):
        hint = (
            f" — it is still a draft at {draft_path(root, nid)}. Ask the user to confirm"
            " that lesson too, then `note.py promote` it."
        )
    return f"error: related id {nid!r} has no active note at {note_path(root, nid)}{hint}"


def write(path: str, fields: dict, body: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_frontmatter(fields) + "\n" + body.strip("\n") + "\n")


def rel_link(from_nid: str, to_nid: str) -> str:
    """Relative path from one note to another; same project stays inside the folder."""
    if project_of(from_nid) == project_of(to_nid):
        return f"./{to_nid.split('/', 1)[1]}.md"
    return f"../{to_nid}.md"


def body_link_bullet(from_nid: str, to_nid: str, fields: dict, target: dict) -> str:
    return f"- → [{to_nid}]({rel_link(from_nid, to_nid)}): {fields.get('nutshell') or target.get('nutshell')}"


def upsert_connection(body: str, bullet: str, target_nid: str) -> str:
    """Add/replace one `- → [...]` bullet under `## 已建立的连接`."""
    heading = "## 已建立的连接"
    lines = body.split("\n")
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return body.rstrip("\n") + f"\n\n{heading}\n{bullet}\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    kept = [
        line
        for line in lines[start + 1 : end]
        if not (line.startswith("- → ") and f"[{target_nid}]" in line)
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    return "\n".join(lines[: start + 1] + kept + [bullet] + lines[end:])


def ensure_project_file(root: str, project: str) -> None:
    """Create a stub learning map on first use; never overwrite an existing one."""
    path = os.path.join(root, project, "_project.md")
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nproject: {project}\n---\n\n# {project} 学习地图\n\n"
            "## 已讲\n\n## 依赖位置\n\n## Learner model\n"
            "- 已具备的基础：\n- 明确困惑过 / 答错过：\n- 偏好深度：\n\n## 待续的线头\n"
        )


def regenerate_index(root: str, notes: dict[str, tuple[dict, str]]) -> None:
    rows = [
        "| project | topic | note | nutshell | verified_against | confidence | date |",
        "|---|---|---|---|---|---|---|",
    ]
    for nid in sorted(notes):
        f = notes[nid][0]
        topic = f.get("topic") or nid.split("/", 1)[1]
        rows.append(
            f"| {f.get('project')} | {topic} | [{nid.split('/', 1)[1]}.md](./{nid}.md) "
            f"| {f.get('nutshell')} | {f.get('verified_against')} | {f.get('confidence')} | {f.get('date')} |"
        )
    with open(os.path.join(root, "INDEX.md"), "w", encoding="utf-8") as fh:
        fh.write(
            "# 教学记忆索引\n\n"
            "由 `scripts/note.py` 从各 note 的 frontmatter 生成，不要手改。\n\n"
            + "\n".join(rows)
            + "\n"
        )


def command_add(args: argparse.Namespace) -> int:
    root = args.notes_root
    path = os.path.abspath(args.note)
    if not os.path.exists(path):
        raise SystemExit(f"error: {path} does not exist — write the note first")
    nid = note_id(path, os.path.abspath(root))
    fields, body = parse_frontmatter(open(path, encoding="utf-8").read())
    fields["project"] = fields.get("project") or project_of(nid)
    fields["topic"] = fields.get("topic") or nid.split("/", 1)[1]
    fields["date"] = fields.get("date") or datetime.date.today().isoformat()
    fields["confidence"] = fields.get("confidence") or "background"
    fields["verified_against"] = fields.get("verified_against") or "unverified"
    merged = [
        normalize_id(r, project_of(nid))
        for r in list(fields.get("related") or []) + list(args.related or [])
    ]
    related = list(dict.fromkeys(merged))
    fields["related"] = related

    for target in related:
        if not os.path.exists(note_path(root, target)):
            raise SystemExit(missing_note_error(root, target))

    write(path, fields, body)

    # Bidirectional: this note's bullets, and the reverse edge in every target.
    notes = load_notes(root)
    for target in related:
        fields, body = notes[nid]
        body = upsert_connection(body, body_link_bullet(nid, target, fields, notes[target][0]), target)
        write(path, fields, body)
        notes[nid] = (fields, body)

        tpath = note_path(root, target)
        tfields, tbody = notes[target]
        if nid not in (tfields.get("related") or []):
            tfields["related"] = list(tfields.get("related") or []) + [nid]
        tbody = upsert_connection(tbody, body_link_bullet(target, nid, tfields, fields), nid)
        write(tpath, tfields, tbody)
        notes[target] = (tfields, tbody)

    ensure_project_file(root, project_of(nid))
    regenerate_index(root, load_notes(root))
    print(f"registered {nid} (related: {', '.join(related) or 'none'})")
    return 0


def command_promote(args: argparse.Namespace) -> int:
    """Move a confirmed draft into the notes proper and run the full add pass.

    Everything is validated before the draft is touched, so a failure here leaves
    the draft where it was instead of half-promoting it.
    """
    draft = os.path.abspath(args.draft)
    parts = draft.split(os.sep)
    if DRAFT_DIR not in parts or not os.path.exists(draft):
        raise SystemExit(f"error: {draft} is not an existing draft inside a {DRAFT_DIR}/ directory")
    root = os.sep.join(parts[: parts.index(DRAFT_DIR)]) or os.sep
    nid = note_id(draft, os.path.join(root, DRAFT_DIR))
    target = note_path(root, nid)
    project = project_of(nid)

    fields, body = parse_frontmatter(open(draft, encoding="utf-8").read())
    merged = [
        normalize_id(r, project)
        for r in list(fields.get("related") or []) + list(args.related or [])
    ]
    related = list(dict.fromkeys(merged))
    for target_id in related:
        if not os.path.exists(note_path(root, target_id)):
            raise SystemExit(missing_note_error(root, target_id))
    if os.path.exists(target):
        raise SystemExit(f"error: {target} already exists — refusing to overwrite an active note")

    fields["project"] = project
    fields["topic"] = nid.split("/", 1)[1]
    fields["related"] = related
    os.makedirs(os.path.dirname(target), exist_ok=True)
    write(target, fields, body)
    os.remove(draft)
    draft_project_dir = os.path.dirname(draft)
    if not os.listdir(draft_project_dir):
        os.rmdir(draft_project_dir)
    print(f"promoted {nid}")
    return command_add(argparse.Namespace(note=target, notes_root=root, related=args.related))


def command_check(args: argparse.Namespace) -> int:
    root = args.notes_root
    problems: list[str] = []
    try:
        notes = load_notes(root)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    if not os.path.isdir(root):
        problems.append(f"{root}: notes root does not exist")
    elif not notes and not find_drafts(root):
        print(f"note: {root} is empty — nothing to check yet")
        return 0

    for nid, (fields, body) in sorted(notes.items()):
        for key in REQUIRED:
            if key not in fields or fields[key] in ("", None):
                problems.append(f"{nid}: frontmatter missing `{key}`")
        if fields.get("project") and fields["project"] != project_of(nid):
            problems.append(f"{nid}: frontmatter project={fields['project']!r} disagrees with path")
        if fields.get("topic") and fields["topic"] != nid.split("/", 1)[1]:
            problems.append(f"{nid}: frontmatter topic={fields['topic']!r} disagrees with path")
        if fields.get("confidence") not in CONFIDENCE:
            problems.append(f"{nid}: confidence={fields.get('confidence')!r} not in {CONFIDENCE}")
        related = [normalize_id(r, project_of(nid)) for r in fields.get("related") or []]
        for dup in sorted({r for r in related if related.count(r) > 1}):
            problems.append(f"{nid}: related lists {dup!r} more than once")
        for target in related:
            if target not in notes:
                problems.append(
                    f"{nid}: related id {target!r} has no active note"
                    + (" (still a draft)" if os.path.exists(draft_path(root, target)) else " (dangling)")
                )
            elif nid not in (notes[target][0].get("related") or []):
                problems.append(f"{nid}: related {target!r} is one-way (missing the reverse edge)")
        for _, target in LINK_RE.findall(body):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            resolved = os.path.normpath(os.path.join(os.path.dirname(note_path(root, nid)), target))
            if not os.path.exists(resolved):
                problems.append(f"{nid}: body link {target!r} does not resolve")

    expected_rows = {
        f"| {f.get('project')} | {f.get('topic')} | " for f in (v[0] for v in notes.values())
    }
    index = os.path.join(root, "INDEX.md")
    if not os.path.exists(index):
        if notes:
            problems.append("INDEX.md is missing — run `note.py add` on a note")
    else:
        text = open(index, encoding="utf-8").read()
        for row in sorted(expected_rows):
            if row not in text:
                problems.append(f"INDEX.md is missing the row for {row.strip('| ')}")

    for problem in problems:
        print(f"FAIL {problem}")
    drafts = find_drafts(root)
    print(f"{len(notes)} note(s), {len(problems)} problem(s)")
    if drafts:
        print(f"{len(drafts)} draft(s) awaiting the user's confirmation: {', '.join(sorted(drafts))}")
    return 1 if problems else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="register a note: frontmatter, INDEX, project map, linkbacks")
    add.add_argument("note", help="path to <notes>/<project>/<topic>.md")
    add.add_argument("--notes-root", default=None, help="notes root (default: two levels above the note)")
    add.add_argument("--related", action="append", default=[], help="related note id, e.g. vllm/scheduler")
    add.set_defaults(func=command_add)

    chk = sub.add_parser("check", help="verify frontmatter, links and INDEX")
    chk.add_argument("notes_root")
    chk.set_defaults(func=command_check)

    pro = sub.add_parser("promote", help="archive a confirmed draft as a reference note")
    pro.add_argument("draft", help="path to <notes>/.drafts/<project>/<topic>.md")
    pro.add_argument("--related", action="append", default=[], help="related note id, e.g. vllm/scheduler")
    pro.set_defaults(func=command_promote)

    args = ap.parse_args()
    if args.command == "add" and not args.notes_root:
        args.notes_root = os.path.dirname(os.path.dirname(os.path.abspath(args.note)))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
