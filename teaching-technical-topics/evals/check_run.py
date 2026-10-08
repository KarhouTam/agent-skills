#!/usr/bin/env python3
"""Assert the mechanical half of a teaching run.

The other half — is the explanation actually any good? — needs a human. This
script pins down everything that is objectively checkable, so a SKILL.md edit can
be told apart from a regression:

    python3 evals/check_run.py --answer run.md --notes <notes-root> --mode standard
    python3 evals/check_run.py --answer run.md --notes <notes-root> --mode quick \
        --notes-before <copy-of-notes-taken-before-the-run>

With `--expect-deepwiki` the run was made with DeepWiki reachable, so the answer
must show it was used as a map and not as a source.

Exit codes: 0 = every check passed, 1 = at least one failed, 2 = bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

DEFAULT_CHECKER = os.path.expanduser("~/.agents/skills/mermaid-diagrams/scripts/check-mermaid.mjs")
FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.S)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
SENTENCE_RE = re.compile(r"[。！？]|[.!?](?:\s|$)")
results: list[tuple[bool, str]] = []


def record(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail and not ok else ""))


def strip_code(text: str) -> str:
    return FENCE_RE.sub("", text)


def mermaid_blocks(text: str) -> list[str]:
    return [body for lang, body in FENCE_RE.findall(text) if lang.strip() == "mermaid"]


def section(text: str, heading: str) -> str | None:
    m = re.search(rf"^{re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else None


def drop_sections(text: str, headings: list[str]) -> str:
    for heading in headings:
        m = re.search(rf"^{re.escape(heading)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
        if m:
            text = text[: m.start()] + text[m.end() :]
    return text


def check_answer(path: str, mode: str, checker: str, expect_deepwiki: bool = False) -> None:
    text = open(path, encoding="utf-8").read()
    # Prose only: fenced code, inline code and the 来源 identifier list are Latin by design.
    prose = re.sub(r"`[^`]*`", "", drop_sections(strip_code(text), ["## 来源"]))

    headings = re.findall(r"^## .+$", text, re.M)
    record(bool(headings) and headings[0].strip() == "## In a nutshell",
           "answer opens with `## In a nutshell`",
           f"first heading is {headings[0]!r}" if headings else "no `## ` headings at all")

    nutshell = section(text, "## In a nutshell")
    if nutshell is None:
        record(False, "nutshell section is present and one sentence")
    else:
        sentences = len(SENTENCE_RE.findall(strip_code(nutshell)))
        record(sentences == 1, "nutshell section is present and one sentence",
               f"found {sentences} sentence terminators")

    cjk = len(CJK_RE.findall(prose))
    latin = len(re.findall(r"[A-Za-z]", prose))
    ratio = cjk / max(1, cjk + latin)
    record(ratio >= 0.30, "正文 is predominantly Chinese whatever the question's language",
           f"CJK ratio {ratio:.2f} of prose")

    blocks = mermaid_blocks(text)
    limit = 2
    record(len(blocks) <= limit, f"at most {limit} mermaid blocks", f"found {len(blocks)}")
    if blocks and os.path.exists(checker):
        tmp = path + ".mmd"
        open(tmp, "w", encoding="utf-8").write("\n\n".join(f"```mermaid\n{b}```" for b in blocks))
        try:
            proc = subprocess.run(["node", checker, tmp], capture_output=True, text=True, timeout=300)
            record(proc.returncode == 0, "every mermaid block parses", proc.stdout.strip()[-300:])
        finally:
            os.remove(tmp)

    if mode == "quick":
        words = len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z]+", prose))
        record(words <= 300, "Quick mode stays short", f"{words} words")
        record(section(text, "## 来源") is None, "Quick mode carries no 来源 section")
    else:
        source = section(text, "## 来源")
        record(source is not None, "answer has a `## 来源` section")
        if source:
            has_provenance = bool(re.search(r"\w+::\w+", source)) or "unverified" in source
            record(has_provenance, "来源 gives `file::symbol` or says `unverified`")

    if mode != "quick":
        record(section(text, "## 延伸") is not None, "answer has a `## 延伸` section")
    if section(text, "## 延伸"):
        items = re.findall(r"^\s*(?:\d+\.|[-*])\s+\S", section(text, "## 延伸"), re.M)
        record(len(items) <= 4, "延伸 has at most 4 items", f"found {len(items)}")

    if expect_deepwiki:
        source = section(text, "## 来源") or ""
        record("deepwiki:" in source.lower(), "来源 points at the DeepWiki map this run used")
        record(bool(re.search(r"\w+::\w+", source)),
               "a DeepWiki-assisted answer still carries a local `file::symbol`")


def check_lifecycle(root: str, topic: str | None, stage: str) -> None:
    """A lesson stays a draft until the user confirms it; only then is it reference."""
    if not topic:
        return
    active = os.path.join(root, *topic.split("/")) + ".md"
    draft = os.path.join(root, ".drafts", *topic.split("/")) + ".md"
    index_path = os.path.join(root, "INDEX.md")
    index = open(index_path, encoding="utf-8").read() if os.path.exists(index_path) else ""
    topic_slug = topic.split("/")[-1]

    if stage == "draft":
        record(os.path.exists(draft), f"{topic} is kept as a draft before confirmation")
        record(not os.path.exists(active), f"{topic} was NOT promoted without confirmation")
        record(topic_slug not in index, f"{topic} is absent from INDEX.md before confirmation")
    elif stage == "promoted":
        record(os.path.exists(active), f"{topic} is an active note after confirmation")
        record(topic_slug in index, f"{topic} appears in INDEX.md after confirmation")
        record(not os.path.exists(draft), f"{topic} draft was consumed by the promotion")


def check_notes(root: str, base: str, before: str | None) -> None:
    note_py = os.path.join(base, "scripts", "note.py")
    proc = subprocess.run([sys.executable, note_py, "check", root], capture_output=True, text=True)
    record(proc.returncode == 0, "`note.py check` passes", (proc.stdout + proc.stderr).strip()[-400:])

    # A generated wiki is a map, not provenance: it can never be what a note was
    # verified against, however plausible its prose looked.
    wiki_claims = []
    for dp, _, fs in os.walk(root):
        for f in fs:
            path = os.path.join(dp, f)
            if f.endswith(".md") and re.search(
                r"^verified_against:.*deepwiki", open(path, encoding="utf-8").read(), re.M | re.I
            ):
                wiki_claims.append(os.path.relpath(path, root))
    record(not wiki_claims, "no note names DeepWiki in its `verified_against`", f"claimed by {wiki_claims}")

    notebooks = [
        os.path.join(dp, f)
        for dp, _, fs in os.walk(root)
        for f in fs
        if f.endswith(".ipynb")
    ]
    for nb_path in notebooks:
        try:
            nb = json.load(open(nb_path))
        except json.JSONDecodeError as exc:
            record(False, f"notebook {os.path.relpath(nb_path, root)} is valid JSON", str(exc))
            continue
        code = [c for c in nb.get("cells", []) if c.get("cell_type") == "code"]
        executed = [c for c in code if c.get("execution_count") is not None]
        record(bool(code) and len(executed) == len(code),
               f"notebook {os.path.relpath(nb_path, root)} ran (every code cell has outputs)",
               f"{len(executed)}/{len(code)} executed")

    if before is not None:
        def md5s(top: str) -> dict[str, str]:
            import hashlib
            out = {}
            for dp, _, fs in os.walk(top):
                for f in fs:
                    p = os.path.join(dp, f)
                    out[os.path.relpath(p, top)] = hashlib.md5(open(p, "rb").read()).hexdigest()
            return out
        changed = {k for k, v in md5s(root).items() if md5s(before).get(k) != v}
        record(not changed, "Quick mode wrote nothing to the notes root", f"changed: {sorted(changed)[:5]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--answer", required=True, help="the teaching answer, saved verbatim as markdown")
    ap.add_argument("--notes", required=True, help="notes root produced by the run")
    ap.add_argument("--mode", default="standard", choices=("quick", "standard", "deep"))
    ap.add_argument("--notes-before", help="copy of the notes root taken before the run (for Quick mode)")
    ap.add_argument("--topic", help="the topic the run taught, as project/topic")
    ap.add_argument("--stage", default="none", choices=("none", "draft", "promoted"),
                    help="assert the lesson is still a draft, or was promoted after confirmation")
    ap.add_argument("--mermaid-check", default=DEFAULT_CHECKER)
    ap.add_argument("--expect-deepwiki", action="store_true",
                    help="the run had DeepWiki reachable: 来源 must point at it, and a local file::symbol must remain")
    args = ap.parse_args()

    if not os.path.exists(args.answer):
        print(f"error: {args.answer} not found", file=sys.stderr)
        return 2
    if not os.path.isdir(args.notes):
        print(f"error: notes root {args.notes} not found", file=sys.stderr)
        return 2

    check_answer(args.answer, args.mode, args.mermaid_check, args.expect_deepwiki)
    check_notes(args.notes, os.path.dirname(os.path.dirname(os.path.abspath(__file__))), args.notes_before)
    check_lifecycle(args.notes, args.topic, args.stage)

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    for label in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
