"""Shared pieces of skill-usage-insights.

Three concerns live here because both the hook collector and the analyzer need
them, and they must agree byte for byte:

- where usage data lives (`data_dir`, `append`, `locked`);
- what the skill catalog of this repo looks like (`Catalog`);
- how a Claude Code transcript turns into token usage attributed to skills
  (`parse_transcript`).

Stdlib only, like the rest of the repo: the collector runs on every hook event
and must start fast on any machine.
"""

import contextlib
import fcntl
import hashlib
import json
import os
import re
import shlex
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
SKILL_ID = SKILL_DIR.name
ROOT = Path(os.environ.get("SKILL_USAGE_ROOT") or SKILL_DIR.parent).resolve()

PROMPT_CHARS = int(os.environ.get("SKILL_USAGE_PROMPT_CHARS", "200"))
SKIP_DIRS = {".git", ".system", "node_modules", "__pycache__", "data", "memory", "insights"}
FILE_TOOLS = {"Read": "read", "Edit": "edit", "Write": "edit", "MultiEdit": "edit", "NotebookEdit": "edit"}
AGENT_TOOLS = {"Agent", "Task"}
SKILL_BASE = re.compile(r"Base directory for this skill:\s*(\S+)")
COMMAND_NAME = re.compile(r"<command-name>/?([^<\s]+)</command-name>")
SUB_SKILL = re.compile(r"REQUIRED SUB-SKILL", re.I)


# --------------------------------------------------------------------------- storage


def data_dir(create=True):
    """First writable of $SKILL_USAGE_DATA_DIR, <skill>/data, $XDG_DATA_HOME/skill-usage-insights."""
    candidates = [
        os.environ.get("SKILL_USAGE_DATA_DIR"),
        str(SKILL_DIR / "data"),
        os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), SKILL_ID),
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser()
        if not create:
            if path.is_dir():
                return path
            continue
        try:
            (path / "sessions").mkdir(parents=True, exist_ok=True)
            (path / "state").mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(path, os.W_OK):
            return path
    return None


def now_iso():
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"


def safe_name(value):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", str(value or "unknown"))[:120]


@contextlib.contextmanager
def locked(path):
    """Exclusive advisory lock: async hooks of one session can run concurrently."""
    with open(path, "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def append(base, session_id, records):
    """Append records to the session's JSONL file under its lock."""
    if not records:
        return
    path = base / "sessions" / f"{safe_name(session_id)}.jsonl"
    lines = "".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records)
    with locked(str(path) + ".lock"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(lines)


def read_records(base, since_epoch=0.0):
    """Yield every record of every session file touched since since_epoch."""
    sessions = base / "sessions"
    if not sessions.is_dir():
        return
    for path in sorted(sessions.glob("*.jsonl")):
        if path.stat().st_mtime < since_epoch:
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


# --------------------------------------------------------------------------- catalog


def read_frontmatter(path):
    """Minimal YAML-frontmatter reader: flat `key: value` plus folded `>` / `|` blocks."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta, key = {}, None
    for line in text[3:end].splitlines():
        if key and (line.startswith("  ") or not line.strip()):
            meta[key] = (meta[key] + " " + line.strip()).strip()
            continue
        key = None
        match = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if value in (">", "|", ">-", "|-"):
            meta[name], key = "", name
        else:
            meta[name] = value.strip("'\"")
    return meta, text[end + 4:].lstrip("\n")


def truthy(value):
    return str(value).strip().lower() in {"true", "yes", "on", "1"}


def estimate_tokens(text):
    """Rough tokenizer-free estimate: ~4 ASCII chars per token, ~1 token per CJK char."""
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    return ascii_chars // 4 + (len(text) - ascii_chars)


def sha12(path):
    try:
        return hashlib.sha1(Path(path).read_bytes()).hexdigest()[:12]
    except OSError:
        return None


class Catalog:
    """The skills that live in ROOT: top-level `<id>/SKILL.md` plus nested ones.

    A nested SKILL.md (e.g. `pytorch-test-refactoring/agent/skills/ci-automation/`)
    is a sub-skill of the top-level skill that contains it, with id `<top>:<dir>`.
    """

    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.self_developed = self._whitelist()
        self.public = self._manifest()
        self.skills = {}
        self.names = {}
        for skill_md in sorted(self.root.glob("*/SKILL.md")):
            self._add(skill_md, skill_md.parent.name, None)
        for top in list(self.skills):
            for skill_md in self._nested(self.root / top):
                self._add(skill_md, f"{top}:{skill_md.parent.name}", top)
        for name in self.public:
            self.names.setdefault(name, name)

    def _whitelist(self):
        path = self.root / ".gitignore"
        if not path.is_file():
            return set()
        names = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^!/([^/*]+)/$", line.strip())
            if match:
                names.add(match.group(1))
        return names

    def _manifest(self):
        path = self.root / "skills.json"
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {
            name: source.get("repo", "")
            for source in manifest.get("sources", [])
            for name in source.get("skills", {})
        }

    def _nested(self, top):
        for dirpath, dirnames, filenames in os.walk(top):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            if "SKILL.md" in filenames and Path(dirpath) != top:
                yield Path(dirpath) / "SKILL.md"

    def _add(self, skill_md, skill_id, parent):
        meta, body = read_frontmatter(skill_md)
        description = (meta.get("description", "") + " " + meta.get("when_to_use", "")).strip()
        if parent:
            tier = "nested"
        elif skill_id in self.self_developed:
            tier = "self"
        elif skill_id in self.public:
            tier = "public"
        else:
            tier = "untracked"
        self.skills[skill_id] = {
            "id": skill_id,
            "name": meta.get("name") or skill_md.parent.name,
            "dir": skill_md.parent,
            "parent": parent,
            "tier": tier,
            "model_invocable": not truthy(meta.get("disable-model-invocation", "false")),
            "description": description,
            "listing_tokens": estimate_tokens(description[:1536]),
            "body_tokens": estimate_tokens(body),
            "hash": sha12(skill_md),
            "body": body,
        }
        if not parent:
            self.names.setdefault(skill_id, skill_id)
            self.names.setdefault(meta.get("name") or skill_id, skill_id)

    def resolve(self, name):
        """Map a Skill-tool / slash-command name to a catalog id, or None when foreign."""
        if not name:
            return None
        name = str(name).strip().lstrip("/")
        if name in self.skills:
            return name
        # A colon-scoped name (`plugin:skill`, `apps/web:deploy`) is someone else's copy.
        return self.names.get(name)

    def tier(self, skill_id):
        if skill_id in self.skills:
            return self.skills[skill_id]["tier"]
        if skill_id in self.public:
            return "public"
        return "external"

    def skill_for_path(self, path):
        """(skill_id, path relative to that skill's dir) for a file inside ROOT, else (None, None)."""
        try:
            real = Path(os.path.realpath(os.path.expanduser(str(path))))
            rel = real.relative_to(self.root)
        except (ValueError, OSError):
            return None, None
        if not rel.parts or rel.parts[0] not in self.skills:
            return None, None
        top = rel.parts[0]
        # Innermost directory holding a SKILL.md wins, so nested sub-skills get their own id.
        for depth in range(len(rel.parts) - 1, 1, -1):
            candidate = f"{top}:{rel.parts[depth - 1]}"
            if candidate in self.skills and self.skills[candidate]["dir"] == self.root.joinpath(*rel.parts[:depth]):
                return candidate, "/".join(rel.parts[depth:])
        return top, "/".join(rel.parts[1:])

    def sub_skill_edges(self):
        """Declared parent -> child edges: `REQUIRED SUB-SKILL` lines and nested SKILL.md files."""
        edges = []
        tops = [s for s in self.skills.values() if not s["parent"]]
        names = sorted({n for n in self.names}, key=len, reverse=True)
        for skill in tops:
            lines = skill["body"].splitlines()
            heading = ""
            for index, line in enumerate(lines):
                if line.startswith("#"):
                    heading = line.lstrip("#").strip()
                if not SUB_SKILL.search(line):
                    continue
                for name in names:
                    child = self.names[name]
                    if child == skill["id"] or any(e["parent"] == skill["id"] and e["child"] == child for e in edges):
                        continue
                    if re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", line):
                        edges.append({
                            "parent": skill["id"],
                            "child": child,
                            "kind": "required-sub-skill",
                            "section": heading,
                            "condition": _paragraph(lines, index),
                        })
        for skill in self.skills.values():
            if skill["parent"]:
                edges.append({
                    "parent": skill["parent"],
                    "child": skill["id"],
                    "kind": "nested",
                    "section": "",
                    "condition": skill["description"][:300],
                })
        return edges

    def scripts(self, skill_id):
        """Every non-markdown file a skill ships (candidates for `exec` / `read` resource use)."""
        skill = self.skills.get(skill_id)
        if not skill:
            return []
        files = []
        for dirpath, dirnames, filenames in os.walk(skill["dir"]):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for filename in filenames:
                path = Path(dirpath) / filename
                if self.skill_for_path(path)[0] == skill_id:
                    files.append(str(path.relative_to(skill["dir"])))
        return sorted(files)


def _paragraph(lines, index):
    """The paragraph around lines[index], plus the one before it: the condition a sub-skill is gated on."""
    start = index
    while start > 0 and lines[start - 1].strip():
        start -= 1
    if start > 1 and not lines[start - 1].strip():
        prev = start - 1
        while prev > 0 and lines[prev - 1].strip() and not lines[prev - 1].startswith("#"):
            prev -= 1
        start = prev
    end = index
    while end + 1 < len(lines) and lines[end + 1].strip():
        end += 1
    text = " ".join(line.strip() for line in lines[start:end + 1] if line.strip())
    return text[:600]


def paths_in_command(command, cwd):
    """Absolute paths of existing files that a shell command names."""
    try:
        tokens = shlex.split(command, comments=False, posix=True)
    except ValueError:
        tokens = command.split()
    found = []
    for token in tokens:
        for piece in token.split("="):
            piece = piece.strip(";&|()<>`'\"")
            if "/" not in piece and not piece.endswith((".py", ".sh", ".mjs", ".js", ".ts")):
                continue
            path = os.path.expanduser(os.path.expandvars(piece))
            if not os.path.isabs(path):
                path = os.path.join(cwd or ".", path)
            if os.path.isfile(path) and path not in found:
                found.append(path)
    return found


# --------------------------------------------------------------------------- transcripts


def _usage_vector(usage):
    return [
        int(usage.get("input_tokens") or 0),
        int(usage.get("cache_creation_input_tokens") or 0),
        int(usage.get("cache_read_input_tokens") or 0),
        int(usage.get("output_tokens") or 0),
    ]


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _is_prompt(entry):
    if entry.get("isMeta") or entry.get("isCompactSummary"):
        return False
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return True
    return isinstance(content, list) and not any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
    )


def new_cursor():
    return {"offset": 0, "prompt_id": None, "turn_ts": None, "stack": [], "msgs": {}, "pending": []}


def parse_transcript(path, cursor, catalog, scope="main", prompt_id=None, min_ts=None):
    """Consume the complete lines appended to a transcript since `cursor`.

    Returns (records, cursor). Token usage is attributed to the *stack* of skills
    activated so far in the current turn: a skill's lifecycle runs from the API
    call after its activation to the end of that turn. The API call that decides
    to invoke a skill is not part of the skill's cost.

    The main transcript skips sidechain lines; subagents are parsed from their
    own transcript (scope = agent id) on SubagentStop, and joined to the parent
    stack later through the Agent tool_use id. Lines older than `min_ts` are
    history another session already accounted for (a resumed or forked copy).
    """
    cursor = cursor or new_cursor()
    if prompt_id and not cursor["prompt_id"]:
        cursor["prompt_id"] = prompt_id
    try:
        size = os.path.getsize(path)
    except OSError:
        return [], cursor
    if size < cursor["offset"]:
        cursor.update(new_cursor())
    with open(path, "rb") as handle:
        handle.seek(cursor["offset"])
        blob = handle.read()
    end = blob.rfind(b"\n")
    if end == -1:
        return [], cursor
    cursor["offset"] += end + 1

    chunks, records = {}, []
    msgs = cursor["msgs"]

    def chunk(prompt, turn_ts, stack, model, ts):
        key = (prompt, tuple(stack), model)
        if key not in chunks:
            chunks[key] = {"kind": "usage", "scope": scope, "prompt_id": prompt, "turn_ts": turn_ts,
                           "skills": list(stack), "model": model, "ts": ts, "calls": 0,
                           "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0}
        return chunks[key]

    def activate(skill, via, tool_use_id, ts, mid):
        if skill in cursor["stack"]:
            return
        cursor["stack"].append(skill)
        records.append({"kind": "t_activation", "scope": scope, "prompt_id": cursor["prompt_id"],
                        "turn_ts": cursor["turn_ts"], "skill": skill, "via": via, "tool_use_id": tool_use_id,
                        "ts": ts})
        cursor["pending"].append([skill, tool_use_id, mid])

    for raw in blob[:end].split(b"\n"):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        if scope == "main" and entry.get("isSidechain"):
            continue
        kind, ts = entry.get("type"), entry.get("timestamp")
        if min_ts and ts and ts < min_ts:
            continue
        message = entry.get("message") if isinstance(entry.get("message"), dict) else {}

        if kind == "user":
            pid = entry.get("promptId")
            if scope == "main" and ((pid and pid != cursor["prompt_id"]) or (not pid and _is_prompt(entry))):
                cursor["prompt_id"], cursor["turn_ts"] = pid or entry.get("uuid"), ts
                cursor["stack"], cursor["pending"] = [], []
            text = _text_of(message.get("content"))
            for name in COMMAND_NAME.findall(text):
                skill = catalog.resolve(name)
                if skill:
                    activate(skill, "user", None, ts, None)
            # The injected skill body names its directory: the fallback for a `/skill` whose name
            # did not resolve (plugin or other-dir skills). A Skill tool call was already counted.
            for base in [] if entry.get("sourceToolUseID") else SKILL_BASE.findall(text):
                skill = catalog.skill_for_path(Path(base) / "SKILL.md")[0] or Path(base).name
                activate(skill, "user", None, ts, None)
            continue

        if kind != "assistant" or not isinstance(message.get("usage"), dict):
            continue
        mid = message.get("id") or entry.get("requestId") or entry.get("uuid")
        vector = _usage_vector(message["usage"])
        if mid in msgs:
            prev = msgs[mid]
            delta = [max(0, a - b) for a, b in zip(vector, prev["u"])]
            if any(delta):
                target = chunk(prev["p"], prev.get("t"), prev["s"], prev["m"], ts)
                for field, value in zip(("input", "cache_creation", "cache_read", "output"), delta):
                    target[field] += value
                prev["u"] = [max(a, b) for a, b in zip(vector, prev["u"])]
        else:
            # A new API call: settle the context growth caused by skills the previous call activated.
            # Growth = this call's context - (previous context + previous output); it is "clean"
            # when the activating call made no other tool call whose result would inflate it.
            for skill, tool_use_id, issuer in cursor["pending"]:
                if issuer in msgs:
                    before = sum(msgs[issuer]["u"])
                    records.append({"kind": "load", "scope": scope, "skill": skill, "tool_use_id": tool_use_id,
                                    "tokens": max(0, sum(vector[:3]) - before),
                                    "clean": msgs[issuer].get("tools", 0) <= 1})
            cursor["pending"] = []
            model = message.get("model")
            msgs[mid] = {"u": vector, "p": cursor["prompt_id"], "t": cursor["turn_ts"], "s": list(cursor["stack"]),
                         "m": model, "tools": 0}
            target = chunk(cursor["prompt_id"], cursor["turn_ts"], cursor["stack"], model, ts)
            target["calls"] += 1
            for field, value in zip(("input", "cache_creation", "cache_read", "output"), vector):
                target[field] += value

        blocks = [b for b in (message.get("content") or []) if isinstance(b, dict) and b.get("type") == "tool_use"]
        msgs[mid]["tools"] = msgs[mid].get("tools", 0) + len(blocks)
        for block in blocks:
            name, tool_input = block.get("name"), block.get("input") or {}
            if name == "Skill":
                raw_name = tool_input.get("skill") or tool_input.get("command") or ""
                activate(catalog.resolve(raw_name) or str(raw_name).lstrip("/"), "model", block.get("id"), ts, mid)
            elif name == "Read" and str(tool_input.get("file_path", "")).endswith("SKILL.md"):
                skill, rel = catalog.skill_for_path(tool_input["file_path"])
                if skill and rel == "SKILL.md":
                    activate(skill, "read", block.get("id"), ts, mid)
            elif name in AGENT_TOOLS:
                records.append({"kind": "spawn", "scope": scope, "prompt_id": cursor["prompt_id"],
                                "turn_ts": cursor["turn_ts"],
                                "tool_use_id": block.get("id"), "skills": list(cursor["stack"]),
                                "subagent_type": tool_input.get("subagent_type"), "ts": ts})

    # Keep the dedupe window small: split lines of one message arrive together.
    if len(msgs) > 64:
        for key in list(msgs)[:-64]:
            del msgs[key]
    return records + list(chunks.values()), cursor
