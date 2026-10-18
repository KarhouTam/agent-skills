"""Turn a Codex rollout log into token usage attributed to skills.

Codex hooks see prompts and tool calls but not tokens; the rollout log it writes
anyway (`~/.codex/sessions/<Y>/<M>/<D>/rollout-*.jsonl`) carries the per-call
`token_count`. This module reads that file past a byte cursor and returns the
same `usage` / `load` / `t_activation` records `usage_lib.parse_transcript`
returns for Claude Code, so `analyze.py` needs no Codex branch.

Attribution mirrors the Claude Code parser: a turn is one `turn_id`, the stack is
the skills read so far in that turn, and a call is charged to the stack as it
stood *before* that call's own tool calls. Codex has no Skill tool, so a skill is
activated by a command that reads its `SKILL.md`.
"""

import json
import os
import re

import usage_lib as lib

CALL_TOOLS = {"exec_command", "shell", "local_shell", "unified_exec", "apply_patch", "write_stdin"}
SKILLS_HEADING = "### Available skills"
SKILL_ROOT = re.compile(r"^- `(r\d+)` = `(.+)`$", re.M)


def new_cursor():
    return {"offset": 0, "turn": None, "turn_ts": None, "cwd": None, "model": None,
            "stack": [], "prev": None, "tools": 0, "current": [], "pending": [],
            "catalog_sig": None}


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b.get("text") or "") for b in content if isinstance(b, dict))
    return ""


def _command(name, arguments):
    if not isinstance(arguments, dict):
        return ""
    if name == "apply_patch":
        return str(arguments.get("command") or arguments.get("patch") or "")
    return str(arguments.get("cmd") or arguments.get("command") or "")


def _vector(usage):
    """Codex reports total input (cache included) and output; split the cache out."""
    total_in = int(usage.get("input_tokens") or 0)
    cached = int(usage.get("cached_input_tokens") or 0)
    return [max(0, total_in - cached), int(usage.get("cache_write_input_tokens") or 0),
            cached, int(usage.get("output_tokens") or 0)]


def _catalog_record(catalog, text, ts, scope):
    """The `<skills_instructions>` block is Codex's catalog listing.

    Entry paths are written against short roots (`r1/mermaid-diagrams/SKILL.md`)
    that the block itself defines, so resolve those before asking the catalog.
    """
    start = text.find("<skills_instructions>")
    end = text.find("</skills_instructions>")
    block = text[start:end] if end != -1 else text[start:]
    heading = block.find(SKILLS_HEADING)
    if heading == -1:
        return None
    roots = {m.group(1): m.group(2) for m in SKILL_ROOT.finditer(block)}
    entries, names = 0, []
    for line in block[heading:].splitlines():
        if line.startswith("#") and not line.startswith(SKILLS_HEADING):
            break  # the next section's bullets are not skill entries
        if not line.startswith("- "):
            continue
        entries += 1
        marker = line.rfind("(file: ")
        if marker == -1:
            continue
        path = line[marker + len("(file: "):].rstrip(")")
        root = re.match(r"^(r\d+)/(.+)$", path)
        if root and root.group(1) in roots:
            path = os.path.join(roots[root.group(1)], root.group(2))
        skill = catalog.skill_for_path(path)[0]
        if skill:
            names.append(skill)
    if not entries:
        return None
    return {"kind": "catalog", "scope": scope, "ts": ts, "entries": entries,
            "tokens": lib.estimate_tokens(block), "skills": sorted(set(names))}


def parse_transcript(path, cursor, catalog, scope="main", prompt_id=None, min_ts=None):
    """Consume the complete lines appended to a rollout since `cursor`.

    Returns `(records, cursor)`. `load` is the next call's context minus what the
    activating call already held, and is `clean` when that call ran no other tool.
    Lines older than `min_ts` belong to history another session accounted for (a
    resumed or forked rollout).
    """
    cursor = cursor or new_cursor()
    for key, value in new_cursor().items():
        cursor.setdefault(key, value)
    if prompt_id and not cursor["turn"]:
        cursor["turn"] = prompt_id
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

    def chunk(model, ts):
        key = (cursor["turn"], tuple(cursor["stack"]), model)
        if key not in chunks:
            chunks[key] = {"kind": "usage", "scope": scope, "prompt_id": cursor["turn"],
                           "turn_ts": cursor["turn_ts"], "skills": list(cursor["stack"]),
                           "model": model, "ts": ts, "calls": 0,
                           "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0}
        return chunks[key]

    def start_turn(turn, ts):
        cursor.update({"turn": turn or cursor["turn"], "turn_ts": ts, "stack": [],
                       "prev": None, "tools": 0, "current": [], "pending": []})

    def charge(usage, ts):
        vector = _vector(usage)
        context = vector[0] + vector[2]
        pending, cursor["pending"] = cursor["pending"], []
        if cursor["prev"] is not None:
            before, prev_out, prev_tools = cursor["prev"]
            for skill in pending:
                records.append({"kind": "load", "scope": scope, "skill": skill, "tool_use_id": None,
                                "tokens": max(0, context - before - prev_out), "clean": prev_tools <= 1})
        target = chunk(cursor["model"], ts)
        target["calls"] += 1
        for field, value in zip(("input", "cache_creation", "cache_read", "output"), vector):
            target[field] += value
        cursor["prev"] = (context, vector[3], cursor["tools"])
        cursor["tools"] = 0
        # The call just charged is the one that decided to load these: apply them now,
        # so its own cost is not attributed to the skill it activated.
        for skill, tool_use_id in cursor["current"]:
            if skill not in cursor["stack"]:
                cursor["stack"].append(skill)
                records.append({"kind": "t_activation", "scope": scope, "prompt_id": cursor["turn"],
                                "turn_ts": cursor["turn_ts"], "skill": skill, "via": "read",
                                "tool_use_id": tool_use_id, "ts": ts})
                cursor["pending"].append(skill)
        cursor["current"] = []

    for raw in blob[:end].split(b"\n"):
        try:
            entry = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        kind, ts = entry.get("type"), entry.get("timestamp")
        payload = entry.get("payload") if isinstance(entry.get("payload"), dict) else {}
        if min_ts and ts and ts < min_ts:
            continue

        if kind == "turn_context":
            cursor["model"] = payload.get("model") or cursor["model"]
            cursor["cwd"] = payload.get("cwd") or cursor["cwd"]
            if payload.get("turn_id") and not cursor["turn"]:
                start_turn(payload["turn_id"], ts)
            continue

        if kind == "event_msg":
            event = payload.get("type")
            if event == "task_started":
                start_turn(payload.get("turn_id"), ts)
            elif event == "item_completed":
                item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
                if item.get("type") == "UserMessage" and cursor["turn_ts"] is None:
                    cursor["turn_ts"] = ts
            elif event == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                usage = info.get("last_token_usage") or info.get("total_token_usage")
                if isinstance(usage, dict):
                    if cursor["turn"] is None:
                        cursor["turn"], cursor["turn_ts"] = ts, ts
                    charge(usage, ts)
            elif event == "turn_aborted":
                records.append({"kind": "turn_failed", "scope": scope, "ts": ts,
                                "prompt_id": cursor["turn"],
                                "error": payload.get("reason") or "aborted"})
            continue

        if kind != "response_item":
            continue
        item = payload.get("type")
        if item == "message":
            text = _text_of(payload.get("content"))
            if payload.get("role") == "developer" and "<skills_instructions>" in text:
                record = _catalog_record(catalog, text[text.index("<skills_instructions>"):], ts, scope)
                signature = record and (record["tokens"], record["entries"], len(record["skills"]))
                if record and signature != cursor["catalog_sig"]:
                    cursor["catalog_sig"] = signature
                    records.append(record)
            continue
        if item != "function_call" or payload.get("name") not in CALL_TOOLS:
            continue
        cursor["tools"] += 1
        try:
            arguments = json.loads(payload.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        command = _command(payload.get("name"), arguments if isinstance(arguments, dict) else {})
        for path in lib.paths_in_command(command, cursor["cwd"]):
            skill, rel = catalog.skill_for_path(path)
            if skill and rel == "SKILL.md" and skill not in cursor["stack"]:
                cursor["current"].append((skill, payload.get("call_id") or payload.get("id")))

    records.extend(c for c in chunks.values() if c["calls"])
    return records, cursor
