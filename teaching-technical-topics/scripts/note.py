#!/usr/bin/env python3
"""Keep the teaching notes consistent: one command instead of hand edits.

A note is `<notes>/<project>/<topic>/note.md` with flat YAML frontmatter. Its
materials (notebooks, figures, ...) live beside it in the same topic directory;
its id stays `project/topic`.

    python3 note.py add <notes>/<project>/<topic>/note.md [--related vllm/scheduler]...
    python3 note.py promote <notes>/.drafts/<project>/<topic>/note.md [--related ...]...
    python3 note.py index <notes>
    python3 note.py check <notes>
    python3 note.py migrate <notes>

A lesson is taught into a **draft** under `<notes>/.drafts/` and only becomes
reference material once the user confirms it was any good and that the lesson is
over. `promote` is that archiving step: it moves the draft note into the topic
directory and runs the full `add` pass. Drafts are invisible to `check`'s link
graph and to readers, so an unconfirmed explanation can never poison a lesson.

`add` is idempotent and does all of the bookkeeping in one pass:
  - normalises frontmatter (date defaults to today)
  - regenerates INDEX.md, the local overview / landscape, from frontmatter and
    every note's `## 待深入` threads
  - creates the project's _project.md if missing
  - makes every `related` link bidirectional, both ways in frontmatter and as a
    `- → [...]` bullet in the body's `## 已建立的连接` section

`index` only regenerates INDEX.md; `check` compares it byte for byte.
`migrate` converts the old flat layout (`<project>/<topic>.md`,
`<project>/notebooks/<topic>.*`, `.drafts/<project>/<topic>.md`) into topic
directories. Both are idempotent.

`check` exits non-zero on: missing/invalid frontmatter, a dangling `related` id, a
one-way link, a broken relative link in any note body, or a stale INDEX.
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
SYNC_DIR = ".sync"
NOTE_FILE = "note.md"
INDEX_FILE = "INDEX.md"
PROJECT_FILE = "_project.md"
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
MAX_THREAD_CHARS = 220


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
    """`<root>/<project>/<topic>/note.md` -> `project/topic`."""
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root)).replace(os.sep, "/")
    if not rel.endswith("/" + NOTE_FILE):
        raise SystemExit(f"error: {path} is not a topic note (expected <project>/<topic>/note.md)")
    nid = rel[: -(len(NOTE_FILE) + 1)]
    if nid.count("/") != 1:
        raise SystemExit(f"error: {path} is not at <project>/<topic>/note.md depth")
    return nid


def project_of(nid: str) -> str:
    return nid.split("/")[0]


def topic_of(nid: str) -> str:
    return nid.split("/", 1)[1]


def note_path(root: str, nid: str) -> str:
    return os.path.join(root, *nid.split("/"), NOTE_FILE)


def draft_path(root: str, nid: str) -> str:
    return os.path.join(root, DRAFT_DIR, *nid.split("/"), NOTE_FILE)


def normalize_id(nid: str, project: str) -> str:
    """A bare topic means 'same project'; canonical ids are always project/topic."""
    return nid if "/" in nid else f"{project}/{nid}"


def load_notes(root: str) -> dict[str, tuple[dict, str]]:
    """Map every *active* topic id -> (fields, body). Drafts are not reference."""
    notes: dict[str, tuple[dict, str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in (DRAFT_DIR, SYNC_DIR))
        if NOTE_FILE not in filenames:
            continue
        path = os.path.join(dirpath, NOTE_FILE)
        nid = note_id(path, root)
        try:
            notes[nid] = parse_frontmatter(open(path, encoding="utf-8").read())
        except ValueError as exc:
            raise SystemExit(f"error: {path}: {exc}")
    return notes


def find_drafts(root: str) -> dict[str, str]:
    """Map topic id -> draft path. Drafts await the user's confirmation."""
    base = os.path.join(root, DRAFT_DIR)
    drafts: dict[str, str] = {}
    for dirpath, _, filenames in os.walk(base):
        if NOTE_FILE not in filenames:
            continue
        path = os.path.join(dirpath, NOTE_FILE)
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
    """Relative path from one topic's note to another's."""
    if project_of(from_nid) == project_of(to_nid):
        return f"../{topic_of(to_nid)}/{NOTE_FILE}"
    return f"../../{to_nid}/{NOTE_FILE}"


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
    path = os.path.join(root, project, PROJECT_FILE)
    if os.path.exists(path):
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(
            f"---\nproject: {project}\n---\n\n# {project} 学习地图\n\n"
            "## 已讲\n\n## 依赖位置\n\n## Learner model\n"
            "- 已具备的基础：\n- 明确困惑过 / 答错过：\n- 偏好深度：\n\n## 待续的线头\n"
        )


def section_text(body: str, heading: str) -> str:
    lines = body.split("\n")
    start = next((i for i, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip("\n")


def bullets_under(body: str, heading: str) -> list[str]:
    items: list[str] = []
    for line in section_text(body, heading).split("\n"):
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif items and line.startswith(("  ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
    return [item for item in items if item]


def materials_for(root: str, nid: str) -> list[str]:
    """Every regular, non-note file sitting in the topic directory."""
    topic_dir = os.path.dirname(note_path(root, nid))
    if not os.path.isdir(topic_dir):
        return []
    return sorted(
        name
        for name in os.listdir(topic_dir)
        if name != NOTE_FILE
        and not name.startswith(".")
        and os.path.isfile(os.path.join(topic_dir, name))
    )


def md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _thread_line(nid: str, text: str) -> str:
    text = " ".join(text.split())
    if len(text) > MAX_THREAD_CHARS:
        text = text[: MAX_THREAD_CHARS - 3].rstrip() + "..."
    return f"- [{nid}](./{nid}/{NOTE_FILE}): {text}"


def render_index(root: str, notes: dict[str, tuple[dict, str]]) -> str:
    """The generated overview / landscape: global stats, projects, edges, threads."""
    ids = sorted(notes)
    projects: dict[str, list[str]] = {}
    for nid in ids:
        projects.setdefault(project_of(nid), []).append(nid)

    rows: list[str] = [
        "# 教学记忆总览",
        "",
        "由 `scripts/note.py` 从各 note 的 frontmatter 与 `## 待深入` 生成，不要手改。",
        "只列已归档主题；`.drafts/` 与云端草稿分支不进入本文件。",
        "",
        "## 全局",
        "",
    ]
    verified = sum(1 for nid in ids if notes[nid][0].get("confidence") == "verified")
    material_count = sum(1 for nid in ids if materials_for(root, nid))
    latest = max((str(notes[nid][0].get("date") or "") for nid in ids), default="")
    rows.append(
        "- 项目：" + (", ".join(f"{p} ({len(projects[p])})" for p in sorted(projects)) or "（暂无）")
    )
    rows.append(f"- 已归档：{len(ids)} 篇；verified {verified} / background {len(ids) - verified}")
    rows.append(f"- 材料：{material_count} 个 topic 带材料")
    rows.append(f"- 最近更新：{latest or '（暂无）'}")

    edges: set[tuple[str, str]] = set()
    for nid in ids:
        for related in notes[nid][0].get("related") or []:
            target = normalize_id(str(related).strip(), project_of(nid))
            if target in notes and target != nid:
                edges.add(tuple(sorted((nid, target))))
    rows.append(f"- 关联：{len(edges)} 条")
    rows += ["", "## 按项目", ""]
    if not projects:
        rows += ["（暂无归档主题）", ""]
    for project in sorted(projects):
        rows += [
            f"### {project}",
            "",
            "| topic | nutshell | verified_against | confidence | date | 材料 |",
            "|---|---|---|---|---|---|",
        ]
        for nid in sorted(projects[project]):
            fields = notes[nid][0]
            materials = " / ".join(
                f"[{name}](./{nid}/{name})" for name in materials_for(root, nid)
            ) or "—"
            rows.append(
                f"| [{topic_of(nid)}](./{nid}/{NOTE_FILE}) "
                f"| {md_cell(fields.get('nutshell'))} "
                f"| {md_cell(fields.get('verified_against'))} "
                f"| {md_cell(fields.get('confidence'))} "
                f"| {md_cell(fields.get('date'))} "
                f"| {materials} |"
            )
        rows.append("")

    rows += ["## 关联", ""]
    if edges:
        rows += [f"- {left} — {right}" for left, right in sorted(edges)]
    else:
        rows.append("（暂无）")
    rows.append("")

    rows += ["## 待续的线头", ""]
    thread_rows: list[str] = []
    for project in sorted(projects):
        for nid in sorted(projects[project]):
            thread_rows += [_thread_line(nid, thread) for thread in bullets_under(notes[nid][1], "## 待深入")]
    rows += thread_rows or ["（暂无）"]
    rows.append("")

    rows += [
        "## 复核顺序",
        "",
        "由旧到新；`verified_against` 与当前环境不一致时需要重验。",
        "",
    ]
    for nid in sorted(ids, key=lambda nid: (str(notes[nid][0].get("date") or ""), nid)):
        fields = notes[nid][0]
        rows.append(
            f"- {fields.get('date') or '（无日期）'} [{nid}](./{nid}/{NOTE_FILE})"
            f" — {fields.get('verified_against') or 'unverified'}"
        )
    return "\n".join(rows).rstrip() + "\n"


def regenerate_index(root: str, notes: dict[str, tuple[dict, str]] | None = None) -> bool:
    """Write INDEX.md only when its content actually changed. Returns True if written."""
    notes = load_notes(root) if notes is None else notes
    content = render_index(root, notes)
    path = os.path.join(root, INDEX_FILE)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            if fh.read() == content:
                return False
    os.makedirs(root, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return True


def command_add(args: argparse.Namespace) -> int:
    root = args.notes_root
    path = os.path.abspath(args.note)
    if not os.path.exists(path):
        raise SystemExit(f"error: {path} does not exist — write the note first")
    nid = note_id(path, os.path.abspath(root))
    fields, body = parse_frontmatter(open(path, encoding="utf-8").read())
    fields["project"] = fields.get("project") or project_of(nid)
    fields["topic"] = fields.get("topic") or topic_of(nid)
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


def command_index(args: argparse.Namespace) -> int:
    changed = regenerate_index(args.notes_root)
    print(f"{'wrote' if changed else 'unchanged'} {os.path.join(args.notes_root, INDEX_FILE)}")
    return 0


def command_promote(args: argparse.Namespace) -> int:
    """Move a confirmed draft into the topic tree and run the full add pass.

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
    fields["topic"] = topic_of(nid)
    fields["related"] = related
    os.makedirs(os.path.dirname(target), exist_ok=True)
    write(target, fields, body)
    os.remove(draft)
    _prune_empty_dirs(os.path.dirname(draft), os.path.join(root, DRAFT_DIR))
    print(f"promoted {nid}")
    return command_add(argparse.Namespace(note=target, notes_root=root, related=args.related))


def _prune_empty_dirs(path: str, stop: str) -> None:
    stop = os.path.abspath(stop)
    path = os.path.abspath(path)
    while path.startswith(stop + os.sep):
        try:
            os.rmdir(path)
        except OSError:
            return
        path = os.path.dirname(path)


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
        if fields.get("topic") and fields["topic"] != topic_of(nid):
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

    index = os.path.join(root, INDEX_FILE)
    if not os.path.exists(index):
        if notes:
            problems.append("INDEX.md is missing — run `note.py index <notes>`")
    else:
        actual = open(index, encoding="utf-8").read()
        if actual != render_index(root, notes):
            problems.append("INDEX.md is stale — run `note.py index <notes>`")

    for problem in problems:
        print(f"FAIL {problem}")
    drafts = find_drafts(root)
    print(f"{len(notes)} note(s), {len(problems)} problem(s)")
    if drafts:
        print(f"{len(drafts)} draft(s) awaiting the user's confirmation: {', '.join(sorted(drafts))}")
    return 1 if problems else 0


def _legacy_moves(root: str) -> list[tuple[str, str]]:
    """All flat-layout -> topic-directory moves, without touching the disk."""
    moves: list[tuple[str, str]] = []
    for project in sorted(os.listdir(root)):
        pdir = os.path.join(root, project)
        if project.startswith(".") or project in (DRAFT_DIR, SYNC_DIR) or not os.path.isdir(pdir):
            continue
        for name in sorted(os.listdir(pdir)):
            path = os.path.join(pdir, name)
            if os.path.isfile(path) and name.endswith(".md") and name not in (PROJECT_FILE, NOTE_FILE):
                topic = name[: -len(".md")]
                moves.append((path, os.path.join(pdir, topic, NOTE_FILE)))
        notebooks = os.path.join(pdir, "notebooks")
        if os.path.isdir(notebooks):
            for name in sorted(os.listdir(notebooks)):
                stem, ext = os.path.splitext(name)
                if ext in (".py", ".ipynb") and os.path.isfile(os.path.join(notebooks, name)):
                    moves.append(
                        (os.path.join(notebooks, name), os.path.join(pdir, stem, name))
                    )
    drafts_base = os.path.join(root, DRAFT_DIR)
    if os.path.isdir(drafts_base):
        for project in sorted(os.listdir(drafts_base)):
            pdir = os.path.join(drafts_base, project)
            if not os.path.isdir(pdir):
                continue
            for name in sorted(os.listdir(pdir)):
                path = os.path.join(pdir, name)
                if os.path.isfile(path) and name.endswith(".md") and name != NOTE_FILE:
                    topic = name[: -len(".md")]
                    moves.append((path, os.path.join(pdir, topic, NOTE_FILE)))
    return moves


def command_migrate(args: argparse.Namespace) -> int:
    root = os.path.abspath(args.notes_root)
    moves = _legacy_moves(root)
    if not moves:
        print("nothing to migrate")
        return 0
    clashes = [(src, dst) for src, dst in moves if os.path.exists(dst)]
    for src, dst in clashes:
        print(f"error: refusing to overwrite {dst} (migrating {src})", file=sys.stderr)
    if clashes:
        return 1
    for src, dst in moves:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.replace(src, dst)
        print(f"moved {os.path.relpath(src, root)} -> {os.path.relpath(dst, root)}")
    for project in sorted(os.listdir(root)):
        pdir = os.path.join(root, project)
        notebooks = os.path.join(pdir, "notebooks")
        if os.path.isdir(notebooks):
            try:
                os.rmdir(notebooks)
            except OSError:
                pass
    drafts_base = os.path.join(root, DRAFT_DIR)
    if os.path.isdir(drafts_base):
        for project in sorted(os.listdir(drafts_base)):
            pdir = os.path.join(drafts_base, project)
            if os.path.isdir(pdir):
                try:
                    os.rmdir(pdir)
                except OSError:
                    pass
    notes = load_notes(root)
    for nid in sorted(notes):
        command_add(argparse.Namespace(note=note_path(root, nid), notes_root=root, related=[]))
    regenerate_index(root, load_notes(root))
    print(f"migrated {len(moves)} path(s); {len(notes)} note(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="register a note: frontmatter, INDEX, project map, linkbacks")
    add.add_argument("note", help="path to <notes>/<project>/<topic>/note.md")
    add.add_argument("--notes-root", default=None, help="notes root (default: three levels above the note)")
    add.add_argument("--related", action="append", default=[], help="related note id, e.g. vllm/scheduler")
    add.set_defaults(func=command_add)

    chk = sub.add_parser("check", help="verify frontmatter, links and INDEX")
    chk.add_argument("notes_root")
    chk.set_defaults(func=command_check)

    idx = sub.add_parser("index", help="regenerate INDEX.md (the local overview)")
    idx.add_argument("notes_root")
    idx.set_defaults(func=command_index)

    pro = sub.add_parser("promote", help="archive a confirmed draft as a reference note")
    pro.add_argument("draft", help="path to <notes>/.drafts/<project>/<topic>/note.md")
    pro.add_argument("--related", action="append", default=[], help="related note id, e.g. vllm/scheduler")
    pro.set_defaults(func=command_promote)

    mig = sub.add_parser("migrate", help="convert the old flat layout into topic directories")
    mig.add_argument("notes_root")
    mig.set_defaults(func=command_migrate)

    args = ap.parse_args()
    if args.command == "add" and not args.notes_root:
        args.notes_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(args.note))))
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
