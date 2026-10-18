"""Codex hook entry point for skill-usage-insights.

One script serves every hook event: Codex passes the event as JSON on stdin and
`hook_event_name` selects the handler. Each handler appends compact records to
<data>/sessions/<session_id>.jsonl.

Codex has no Skill tool, so an implicit activation is a command that reads a
`SKILL.md`; that call is visible in the rollout log, not in the hook payload, so
the hooks record prompts and file/script use and the rollout supplies activations,
tokens and turn outcomes (see `scripts/codex_transcript.py`).

Codex turns a hook's stdout into model context on several events, so this script
never prints anything. A collector must never break the session it observes:
every failure is logged to <data>/errors.log and the script still exits 0.

    python3 collect_codex.py < payload.json    # what the hook runs
    SKILL_USAGE_DISABLE=1                      # turn collection off without uninstalling
"""

import json
import os
import re
import sys
import traceback
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import codex_transcript as codex  # noqa: E402
import usage_lib as lib  # noqa: E402

ERROR_LOG_LIMIT = 1_000_000
SHELL_TOOLS = {"Bash", "exec_command", "unified_exec", "local_shell", "shell"}
MENTION = re.compile(r"(?<![\w$-])\$([A-Za-z][\w-]*)")
EXIT_CODE = re.compile(r"(?:Process exited with code|Exit code:)\s*(-?\d+)")


def base_record(payload, kind):
    record = {"kind": kind, "ts": lib.now_iso(), "session_id": payload.get("session_id")}
    for field in ("turn_id", "agent_id", "agent_type"):
        if payload.get(field):
            record[field] = payload[field]
    if payload.get("turn_id"):
        record["prompt_id"] = payload["turn_id"]
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


def ok_of(payload):
    """Codex runs PostToolUse for a failing shell command too; None when it does not say."""
    response = payload.get("tool_response")
    if isinstance(response, dict):
        metadata = response.get("metadata") if isinstance(response.get("metadata"), dict) else {}
        for source in (metadata, response):
            for key in ("exit_code", "exitCode"):
                if isinstance(source.get(key), int):
                    return source[key] == 0
    match = EXIT_CODE.search(json.dumps(response) if response is not None else "")
    return None if match is None else int(match.group(1)) == 0


# --------------------------------------------------------------------------- handlers


def on_session_start(payload, catalog, base):
    if payload.get("source") in ("resume", "fork"):
        with SessionState(payload, base) as state:
            state.setdefault("since", lib.now_iso())
    record = base_record(payload, "session_start")
    cwd = payload.get("cwd") or os.getcwd()
    record.update({
        "source": payload.get("source"),
        "model": payload.get("model"),
        "cwd": cwd,
        "project": Path(cwd).name,
        "authoring": under_root(cwd),
    })
    flush(payload, catalog, base, payload.get("transcript_path"), "main")
    return [record]


def on_prompt(payload, catalog, base):
    prompt = payload.get("prompt") or ""
    record = base_record(payload, "prompt")
    record.update({"preview": preview(prompt), "chars": len(prompt)})
    stripped = prompt.lstrip()
    if stripped.startswith("/"):
        record["slash"] = stripped[1:].split(None, 1)[0] if len(stripped) > 1 else ""
    records = [record]
    for name in MENTION.findall(prompt):
        skill = catalog.resolve(name)
        if skill and skill not in [r.get("skill") for r in records]:
            activation = base_record(payload, "activation")
            activation.update({"skill": skill, "tier": catalog.tier(skill), "via": "user"})
            if skill in catalog.skills:
                activation["hash"] = catalog.skills[skill]["hash"]
            records.append(activation)
    return records


def on_post_tool(payload, catalog, base):
    tool, tool_input = payload.get("tool_name"), payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return []
    if tool in SHELL_TOOLS:
        command, op = str(tool_input.get("cmd") or tool_input.get("command") or ""), "exec"
    elif tool == "apply_patch":
        command, op = str(tool_input.get("command") or tool_input.get("patch") or ""), "edit"
    else:
        return []
    ok = ok_of(payload)
    records = []
    for path in lib.paths_in_command(command, payload.get("cwd")):
        skill, rel = catalog.skill_for_path(path)
        if not skill:
            continue
        record = base_record(payload, "resource")
        record.update({"skill": skill, "file": rel, "op": op, "ok": ok,
                       "tool_use_id": payload.get("tool_use_id")})
        records.append(record)
    return records


class SessionState:
    """The per-session parser cursor in <data>/state/codex-<session>.json, under its lock."""

    def __init__(self, payload, base, scope="main"):
        name = lib.safe_name(payload.get("session_id"))
        self.path = base / "state" / f"codex-{name}-{lib.safe_name(scope)}.json"
        self.lock = lib.locked(str(self.path) + ".lock")
        self.scope = scope

    def __enter__(self):
        self.lock.__enter__()
        try:
            self.state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}
        return self.state

    def __exit__(self, *exc):
        try:
            if exc[0] is None:
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.state), encoding="utf-8")
                tmp.replace(self.path)
        finally:
            self.lock.__exit__(*exc)


def flush(payload, catalog, base, path, scope, prompt_id=None):
    """Parse whatever the rollout gained since the last flush, under the session lock."""
    if not path or not os.path.isfile(os.path.expanduser(path)):
        return
    with SessionState(payload, base, scope) as state:
        cursor = state.get("cursor") or codex.new_cursor()
        records, state["cursor"] = codex.parse_transcript(
            os.path.expanduser(path), cursor, catalog, scope=scope, prompt_id=prompt_id,
            min_ts=state.get("since") if scope == "main" else None)
        for record in records:
            record["session_id"] = payload.get("session_id")
            if scope != "main":
                record.setdefault("parent_prompt_id", payload.get("turn_id"))
        lib.append(base, payload.get("session_id"), records)


def on_stop(payload, catalog, base):
    flush(payload, catalog, base, payload.get("transcript_path"), "main")
    return [base_record(payload, "turn_end")]


def on_session_end(payload, catalog, base):
    flush(payload, catalog, base, payload.get("transcript_path"), "main")
    record = base_record(payload, "session_end")
    record["reason"] = payload.get("reason")
    return [record]


def on_subagent_start(payload, catalog, base):
    return [base_record(payload, "subagent_start")]


def on_subagent_stop(payload, catalog, base):
    agent_id = payload.get("agent_id") or "unknown"
    flush(payload, catalog, base, payload.get("agent_transcript_path"), agent_id, prompt_id=agent_id)
    return [base_record(payload, "subagent_stop")]


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_prompt,
    "PostToolUse": on_post_tool,
    "SubagentStart": on_subagent_start,
    "SubagentStop": on_subagent_stop,
    "Stop": on_stop,
    "SessionEnd": on_session_end,
}


class LazyCatalog:
    """Builds the catalog on first use: prompt and session hooks often never need it."""

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
