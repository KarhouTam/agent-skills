"""Claude Code hook entry point for skill-usage-insights.

One script serves every hook event: Claude Code passes the event as JSON on
stdin, and `hook_event_name` selects the handler. Each handler appends compact
records to <data>/sessions/<session_id>.jsonl; nothing here ever reads another
session's data, blocks a tool, or prints to the conversation.

A collector must never break the session it observes: every failure is logged
to <data>/errors.log and the script still exits 0.

    python3 collect.py < payload.json        # what the hook runs
    SKILL_USAGE_DISABLE=1                    # turn collection off without uninstalling
"""

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import usage_lib as lib  # noqa: E402

ERROR_LOG_LIMIT = 1_000_000


def base_record(payload, kind):
    record = {"kind": kind, "ts": lib.now_iso(), "session_id": payload.get("session_id")}
    for field in ("prompt_id", "agent_id", "agent_type"):
        if payload.get(field):
            record[field] = payload[field]
    return record


def preview(text):
    text = " ".join(str(text or "").split())
    return text[: lib.PROMPT_CHARS] if lib.PROMPT_CHARS > 0 else ""


def under_root(path):
    try:
        Path(os.path.realpath(path)).relative_to(lib.ROOT)
        return True
    except (ValueError, OSError, TypeError):
        return False


def skill_fields(catalog, skill):
    fields = {"skill": skill, "tier": catalog.tier(skill)}
    if skill in catalog.skills:
        fields["hash"] = catalog.skills[skill]["hash"]
    return fields


# --------------------------------------------------------------------------- handlers


def on_session_start(payload, catalog, base):
    if payload.get("source") in ("resume", "fork"):
        mark_history(payload, base)
    record = base_record(payload, "session_start")
    cwd = payload.get("cwd") or os.getcwd()
    record.update({
        "source": payload.get("source"),
        "model": payload.get("model"),
        "cwd": cwd,
        "project": Path(os.environ.get("CLAUDE_PROJECT_DIR") or cwd).name,
        "authoring": under_root(cwd),
    })
    return [record]


def on_prompt(payload, catalog, base):
    prompt = payload.get("prompt") or ""
    record = base_record(payload, "prompt")
    record.update({"preview": preview(prompt), "chars": len(prompt)})
    stripped = prompt.lstrip()
    if stripped.startswith("/"):
        record["slash"] = stripped[1:].split(None, 1)[0] if len(stripped) > 1 else ""
    return [record]


def on_expansion(payload, catalog, base):
    if payload.get("expansion_type") not in (None, "slash_command"):
        return []
    name = payload.get("command_name") or ""
    skill = catalog.resolve(name) or name.lstrip("/")
    record = base_record(payload, "activation")
    record.update(skill_fields(catalog, skill))
    record.update({"via": "user", "source": payload.get("command_source"),
                   "args": preview(payload.get("command_args"))})
    return [record]


def on_pre_tool(payload, catalog, base):
    if payload.get("tool_name") != "Skill":
        return []
    tool_input = payload.get("tool_input") or {}
    name = tool_input.get("skill") or tool_input.get("command") or ""
    skill = catalog.resolve(name) or str(name).lstrip("/")
    record = base_record(payload, "activation")
    record.update(skill_fields(catalog, skill))
    record.update({"via": "model", "tool_use_id": payload.get("tool_use_id"), "args": preview(tool_input.get("args"))})
    return [record]


def on_post_tool(payload, catalog, base, ok):
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input") or {}
    error = None if ok else str(payload.get("error") or "")[:300]
    records = []

    if tool == "Skill":
        record = base_record(payload, "activation_result")
        record.update({"tool_use_id": payload.get("tool_use_id"), "ok": ok, "error": error,
                       "is_interrupt": payload.get("is_interrupt")})
        return [record]

    if tool in lib.AGENT_TOOLS:
        response = payload.get("tool_response") if isinstance(payload.get("tool_response"), dict) else {}
        record = base_record(payload, "agent_link")
        record.update({"tool_use_id": payload.get("tool_use_id"), "ok": ok,
                       "child_agent_id": response.get("agentId"), "status": response.get("status"),
                       "subagent_type": tool_input.get("subagent_type"),
                       "total_tool_uses": response.get("totalToolUseCount"),
                       "duration_ms": response.get("totalDurationMs")})
        return [record]

    if tool in lib.FILE_TOOLS:
        paths, op = [tool_input.get("file_path") or tool_input.get("notebook_path")], lib.FILE_TOOLS[tool]
    elif tool == "Bash":
        paths, op = lib.paths_in_command(str(tool_input.get("command") or ""), payload.get("cwd")), "exec"
    else:
        return []

    # Most Read/Bash calls touch nothing in the skills repo: decide that before loading the catalog.
    for path in [p for p in paths if p and under_root(p)]:
        skill, rel = catalog.skill_for_path(path)
        if not skill:
            continue
        record = base_record(payload, "resource")
        record.update({"skill": skill, "file": rel, "op": op, "ok": ok, "error": error,
                       "tool_use_id": payload.get("tool_use_id"), "duration_ms": payload.get("duration_ms")})
        records.append(record)
        if op == "read" and ok and (rel == "SKILL.md"):
            activation = base_record(payload, "activation")
            activation.update(skill_fields(catalog, skill))
            activation.update({"via": "read", "tool_use_id": payload.get("tool_use_id")})
            records.append(activation)
    return records


class SessionState:
    """The per-session parser state in <data>/state/<session>.json, held under its lock."""

    def __init__(self, payload, base):
        self.path = base / "state" / f"{lib.safe_name(payload.get('session_id'))}.json"
        self.lock = lib.locked(str(self.path) + ".lock")

    def __enter__(self):
        self.lock.__enter__()
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        self.state.setdefault("cursors", {})
        return self.state

    def __exit__(self, *exc):
        try:
            if exc[0] is None:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.state), encoding="utf-8")
                tmp.replace(self.path)
        finally:
            self.lock.__exit__(*exc)


def mark_history(payload, base):
    """A resumed or forked session may start on a transcript whose history was counted elsewhere."""
    with SessionState(payload, base) as state:
        if "main" not in state["cursors"]:
            state.setdefault("since", lib.now_iso())


def flush(payload, catalog, base, path, scope, prompt_id=None):
    """Parse whatever the transcript gained since the last flush, under the session lock."""
    if not path or not os.path.isfile(os.path.expanduser(path)):
        return []
    with SessionState(payload, base) as state:
        cursors = state["cursors"]
        records, cursors[scope] = lib.parse_transcript(
            os.path.expanduser(path), cursors.get(scope), catalog, scope=scope, prompt_id=prompt_id,
            min_ts=state.get("since") if scope == "main" else None)
        for record in records:
            record["session_id"] = payload.get("session_id")
            if scope != "main":
                record.setdefault("parent_prompt_id", payload.get("prompt_id"))
        lib.append(base, payload.get("session_id"), records)
    return []


def on_stop(payload, catalog, base, kind):
    flush(payload, catalog, base, payload.get("transcript_path"), "main")
    record = base_record(payload, kind)
    if kind == "turn_failed":
        record["error"] = payload.get("error")
    return [record]


def on_subagent_start(payload, catalog, base):
    return [base_record(payload, "subagent_start")]


def on_subagent_stop(payload, catalog, base):
    agent_id = payload.get("agent_id") or "unknown"
    flush(payload, catalog, base, payload.get("agent_transcript_path"), agent_id, prompt_id=agent_id)
    return [base_record(payload, "subagent_stop")]


def on_session_end(payload, catalog, base):
    flush(payload, catalog, base, payload.get("transcript_path"), "main")
    record = base_record(payload, "session_end")
    record["reason"] = payload.get("reason")
    return [record]


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_prompt,
    "UserPromptExpansion": on_expansion,
    "PreToolUse": on_pre_tool,
    "PostToolUse": lambda p, c, b: on_post_tool(p, c, b, True),
    "PostToolUseFailure": lambda p, c, b: on_post_tool(p, c, b, False),
    "SubagentStart": on_subagent_start,
    "SubagentStop": on_subagent_stop,
    "Stop": lambda p, c, b: on_stop(p, c, b, "turn_end"),
    "StopFailure": lambda p, c, b: on_stop(p, c, b, "turn_failed"),
    "SessionEnd": on_session_end,
}


class LazyCatalog:
    """Builds the catalog on first use: prompt and session hooks never need it."""

    def __init__(self):
        self._catalog = None

    def __getattr__(self, name):
        if self._catalog is None:
            self._catalog = lib.Catalog()
        return getattr(self._catalog, name)


def log_error(base, payload):
    if base is None:
        return
    path = base / "errors.log"
    try:
        if path.exists() and path.stat().st_size > ERROR_LOG_LIMIT:
            path.write_text("", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as handle:
            event = payload.get("hook_event_name") if isinstance(payload, dict) else None
            handle.write(f"--- {lib.now_iso()} {event}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def main():
    if os.environ.get("SKILL_USAGE_DISABLE"):
        return 0
    payload, base = {}, None
    try:
        payload = json.load(sys.stdin)
        handler = HANDLERS.get(payload.get("hook_event_name"))
        if handler is None:
            return 0
        base = lib.data_dir()
        if base is None:
            return 0
        records = handler(payload, LazyCatalog(), base)
        lib.append(base, payload.get("session_id"), records)
    except Exception:  # noqa: BLE001 - a collector must never fail the session it observes
        log_error(base or lib.data_dir(), payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
