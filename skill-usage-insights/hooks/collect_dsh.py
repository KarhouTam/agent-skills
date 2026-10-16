"""dsh session-log collector for skill-usage-insights.

DeepSeek Harness records a session as a zstd JSONL log, so unlike Claude Code
there is nothing to install: this turns those logs into the same records
`analyze.py` already reads, and appends them to `<data>/sessions/<session>.jsonl`.

    python3 collect_dsh.py                # collect every log, skip what is already in
    python3 collect_dsh.py --status       # what is collectible, write nothing
    python3 collect_dsh.py --since 30d    # only logs touched since then (an epoch is accepted too)
    python3 collect_dsh.py --json         # machine-readable result of a real collection

`--since` filters by the log's modification time, so a window narrower than a long
session can leave a hole in it. Leave it at the default to collect everything.

A log is re-emitted only past its recorded `seq` cursor, so a session that is
still running is picked up incrementally and a re-run appends nothing. A log that
cannot be decoded is reported and skipped: one bad session must not lose the rest.

The mapping from dsh events to records is documented in reference/data-model.md.
"""

import argparse
import json
import os
import re
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import dsh_logs  # noqa: E402
import usage_lib as lib  # noqa: E402

# dsh tool names are lower case (`read`, `write`, `bash`, `skill`, `subagent`).
FILE_TOOLS = {"read": "read", "write": "edit", "edit": "edit"}
# Both spawn a child session whose log names this one as its parent.
AGENT_TOOLS = ("subagent", "subagent_fork")
# dsh's own slash gesture: /(^|\s)\/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)/g
SKILL_GESTURE = re.compile(r"(?:^|\s)/([a-z0-9]+(?:-[a-z0-9]+)*)(?=\s|$)")
PROMPT_SOURCES = ("user", "agent-message", "goal", "plugin")
ERROR_LOG_LIMIT = 1_000_000
WINDOW = {"h": 3600, "d": 86400, "w": 604800}


def ms_to_iso(ms):
    try:
        seconds = float(ms) / 1000.0
    except (TypeError, ValueError):
        return lib.now_iso()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(seconds)) + f".{int(seconds % 1 * 1000):03d}Z"


def preview(text):
    text = " ".join(str(text or "").split())
    return text[: lib.PROMPT_CHARS] if lib.PROMPT_CHARS > 0 else ""


def text_of(content):
    if not isinstance(content, list):
        return ""
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


def usage_of(event):
    """A dsh `assistant/message` usage vector, in the field names analyze.py sums.

    dsh counts a step's fresh input (`inputTokens`) separately from the context it
    re-read (`cacheReadTokens`); Claude Code's transcript splits the same thing into
    `input_tokens` and `cache_read_input_tokens`, and has no separate reasoning count
    (dsh includes it in `outputTokens`). `cache_creation` stays zero: dsh does not
    report cache writes apart from the read.
    """
    usage = event.get("data", {}).get("usage") or {}
    return {"input": int(usage.get("inputTokens") or 0), "cache_creation": 0,
            "cache_read": int(usage.get("cacheReadTokens") or 0),
            "output": int(usage.get("outputTokens") or 0)}


def context_of(usage):
    """What the model had to read for that step: fresh input plus cached context."""
    return usage["input"] + usage["cache_read"]


class Cursors:
    """`<data>/state/dsh.json`: the last `seq` emitted for each session log."""

    def __init__(self, base):
        self.path = base / "state" / "dsh.json"

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def save(self, cursors):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cursors, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)


class LogToRecords:
    """One decoded dsh session log -> the records analyze.py joins.

    `children` maps a child session id to (the parent's `subagent` call id, the parent
    session id, the parent turn). A subagent's tokens are attributed to the parent
    turn through that link; without it analyze.py charges them to every main-thread
    skill of the turn instead.
    """

    def __init__(self, events, catalog, children=None):
        self.events = [e for e in events if isinstance(e, dict)]
        self.catalog = catalog
        self.children = children or {}
        self.session = str(next((e.get("id") for e in self.events if e.get("type") == "session"), "unknown"))
        link = self.children.get(self.session)
        self.parent_prompt = link[2] if link else None
        self.records = []
        self.activations = []   # (ts_ms, tool_use_id, skill)
        self.pending = []       # (skill, tool_use_id, tool calls in the issuing step)
        self.by_call = {}       # callId -> record index of a resource it produced
        self.calls = {}         # callId -> (name, arguments)
        self.steps = {}         # step -> its usage vector
        self.step_calls = defaultdict(int)
        self.turn_ts = {}       # turn -> epoch ms of its first user-role message
        self.turn_segments = {}  # turn -> its `usage` records, split at each activation
        self.prompted = set()
        self.goal_prompts = set()  # (goal id, objective) already recorded as a prompt
        self.header = {}
        self.scope = "main"     # a child reports usage under its own id, which analyze.py joins to the parent
        self.cwd = ""
        self.stamp = 0
        self.activated = False
        self.last_activation = None

    # -- plumbing ----------------------------------------------------------------

    def add(self, kind, ms, **fields):
        record = {"kind": kind, "ts": ms_to_iso(ms), "session_id": self.session}
        record.update(fields)
        self.records.append(record)
        return record

    def resolve(self, name):
        """A dsh skill name -> one of this repo's catalog ids, else None."""
        skill = self.catalog.resolve(name)
        return skill if skill in self.catalog.skills else None

    def turn_id(self, turn):
        """The turn a record belongs to. A subagent's records belong to its parent's turn."""
        return self.parent_prompt or f"t{turn}"

    def activate(self, skill, via, ms, turn, tool_use_id=None):
        """Record a load of one of this repo's skills. Returns whether it was one."""
        if not skill:
            return False
        info = self.catalog.skills.get(skill, {})
        self.add("activation", ms, prompt_id=self.turn_id(turn), skill=skill, via=via,
                 tool_use_id=tool_use_id, hash=info.get("hash"))
        self.activations.append((ms, tool_use_id, skill))
        self.activated, self.last_activation = True, ms
        return True

    def stack_before(self, ms):
        """Every distinct skill loaded by then, in load order.

        A skill can be loaded twice in one turn — a `/name` gesture and then the model
        calling the tool — and that is one skill on the stack, not two.
        """
        stack = []
        for ts, _, skill in self.activations:
            if ts <= ms and skill not in stack:
                stack.append(skill)
        return stack

    def segments(self, turn):
        """The `usage` records of one turn, opened as skills load.

        dsh reports tokens per step; a skill that loads mid-turn has to be charged
        from the step after the load, so a turn is split at every activation and
        each part carries the stack that was live for it. A turn with no skill
        keeps the single record its start opened.
        """
        return self.turn_segments.setdefault(turn, [
            self.segment(turn, self.turn_ts.get(turn), 0, None)])

    def segment(self, turn, ms, index, model):
        record = {"kind": "usage", "ts": ms_to_iso(ms), "session_id": self.record_session, "scope": self.scope,
                  "prompt_id": self.turn_id(turn), "turn_ts": ms_to_iso(ms), "skills": self.stack_before(ms),
                  "model": model, "calls": 0, "input": 0, "cache_creation": 0,
                  "cache_read": 0, "output": 0, "_turn": turn, "_index": index}
        if self.parent_prompt:
            # What analyze.py joins a subagent's usage on: the parent turn it belongs to.
            record["parent_prompt_id"] = self.parent_prompt
        return record

    def open_segment(self, turn, ms):
        live = self.segments(turn)
        stack = self.stack_before(ms)
        if stack == live[-1]["skills"]:
            return
        live.append(self.segment(turn, ms, len(live), live[-1]["model"]))

    @property
    def record_session(self):
        """The session a `usage` record belongs to.

        A subagent's tokens are the parent turn's cost and analyze.py joins them by
        looking the child up under the parent session, so a child files its usage
        there and names itself as the scope.
        """
        link = self.children.get(self.session)
        return link[1] if link else self.session

    # -- event handlers ----------------------------------------------------------

    def on_prompt(self, event, turn):
        source = event.get("data", {}).get("source") or {}
        kind = str(source.get("kind") or "")
        if kind == "skill-catalog":
            self.on_catalog(event)
            return
        if kind not in PROMPT_SOURCES or turn in self.prompted:
            return
        self.prompted.add(turn)
        self.turn_ts.setdefault(turn, event.get("time"))
        if self.parent_prompt:
            # A subagent session gets the dispatcher's text as its first user-role message.
            # That is not a prompt and not a `/skill` the user typed: the parent's turn
            # already carries the real one, so copying it here would count the same gesture
            # twice and fill the trigger review with other agents' dispatches.
            return
        text = text_of(event.get("data", {}).get("content"))
        if kind == "goal":
            # Every goal round restates the objective the user typed, so the objective is
            # recorded once (later rounds are continuations of the same prompt) — but each
            # round is still its own turn, which is where its tokens go.
            match = re.search(r"Objective:\s*(.+?)\s*(?:\n|$)", text)
            text = match.group(1).strip().strip('"').strip() if match else ""
            if (source.get("goalId"), text) in self.goal_prompts:
                return
            self.goal_prompts.add((source.get("goalId"), text))
        stripped = text.lstrip()
        slash = stripped[1:].split(None, 1)[0] if stripped.startswith("/") and len(stripped) > 1 else None
        self.add("prompt", event.get("time"), prompt_id=f"t{turn}", preview=preview(text),
                 chars=len(text), slash=slash)
        for name in SKILL_GESTURE.findall(text):
            self.activate(self.resolve(name), "user", event.get("time"), turn)

    def on_message(self, event, turn):
        if turn not in self.prompted:
            # A continuation turn whose user-role message carried no text still needs
            # a row: an empty preview is honest, a missing row silently loses usage.
            self.prompted.add(turn)
            self.turn_ts.setdefault(turn, event.get("time"))
            if not self.parent_prompt:
                self.add("prompt", event.get("time"), prompt_id=f"t{turn}", preview="", chars=0, slash=None)
        data = event.get("data", {})
        step, usage = int(data.get("step") or 0), usage_of(event)
        self._settle_loads(step, usage, turn)
        self.steps[step] = usage
        part = self.segments(turn)[-1]
        for field in ("input", "cache_creation", "cache_read", "output"):
            part[field] += usage[field]
        part["calls"] += 1
        part["model"] = part["model"] or (data.get("message") or {}).get("model")

    def _settle_loads(self, step, usage, turn):
        """Context growth across an activation: this step's context minus what the last
        step ended with.

        The issuing step's own output is subtracted, because the next step reads the
        conversation including it; what is left is what the activation itself added.
        """
        if not self.pending:
            return
        before = self.steps.get(step - 1)
        for skill, tool_use_id, issuing_calls in self.pending:
            if before is None:
                continue
            self.add("load", self.stamp, prompt_id=self.turn_id(turn), skill=skill, tool_use_id=tool_use_id,
                     tokens=max(0, context_of(usage) - context_of(before) - before["output"]),
                     clean=issuing_calls <= 1)
        self.pending = []

    def on_tool_call(self, event, turn):
        data = event.get("data", {})
        name, ms = data.get("name"), event.get("time")
        self.stamp = ms
        step = int(data.get("step") or 0)
        self.step_calls[step] += 1
        try:
            arguments = json.loads(data.get("arguments") or "{}")
        except ValueError:
            arguments = {}
        arguments = arguments if isinstance(arguments, dict) else {}
        call_id = data.get("callId")
        self.calls[call_id] = (name, arguments)

        if name == "skill":
            skill = self.resolve(str(arguments.get("name") or ""))
            if self.activate(skill, "model", ms, turn, call_id):
                self.pending.append((skill, call_id, self.step_calls[step]))
        elif name in FILE_TOOLS:
            path = arguments.get("file_path") or arguments.get("notebook_path")
            skill, rel = self.catalog.skill_for_path(path) if path else (None, None)
            if not skill:
                return
            self.add("resource", ms, prompt_id=self.turn_id(turn), skill=skill, file=rel,
                     op=FILE_TOOLS[name], ok=None, tool_use_id=call_id)
            self.by_call[call_id] = len(self.records) - 1
            if FILE_TOOLS[name] == "read" and rel == "SKILL.md":
                self.activate(skill, "read", ms, turn, call_id)
        elif name == "bash":
            for path in lib.paths_in_command(str(arguments.get("command") or ""), self.cwd):
                skill, rel = self.catalog.skill_for_path(path)
                if skill:
                    self.add("resource", ms, prompt_id=self.turn_id(turn), skill=skill, file=rel,
                             op="exec", ok=None, tool_use_id=call_id)
                    self.by_call[call_id] = len(self.records) - 1
        elif name in AGENT_TOOLS:
            self.add("spawn", ms, prompt_id=self.turn_id(turn), tool_use_id=call_id,
                     skills=self.stack_before(ms), subagent_type=arguments.get("description"))

    def on_tool_result(self, event, turn):
        data = event.get("data", {})
        call_id = (data.get("message", {}).get("source") or {}).get("callId")
        name = self.calls.get(call_id, (None, {}))[0]
        error = data.get("error")
        ms = event.get("time")
        text = json.dumps(error)[:300] if error else ""
        if name == "skill":
            self.add("activation_result", ms, prompt_id=self.turn_id(turn), tool_use_id=call_id,
                     ok=not error, error=text or None)
            return
        index = self.by_call.get(call_id)
        if index is not None:
            self.records[index]["ok"] = not error
            if error:
                self.records[index]["error"] = text

    def on_catalog(self, event):
        """dsh re-publishes the model's skill catalog in the conversation.

        That message is the listing cost a session pays for the catalog, so it is
        recorded as it was sent rather than estimated from this repo's descriptions.
        """
        source = (event.get("data") or {}).get("source") or {}
        entries = source.get("entries")
        if not isinstance(entries, list) or not entries:
            return
        rendered = json.dumps(entries, ensure_ascii=False)
        names = {n for n in (self.resolve(str(e.get("name") or "")) for e in entries if isinstance(e, dict)) if n}
        self.add("catalog", event.get("time"), skills=sorted(names), entries=len(entries),
                 tokens=lib.estimate_tokens(rendered))

    def on_command(self, event, turn):
        """A `/name` dsh command: the user asking for a skill by name."""
        data = event.get("data", {})
        self.activate(self.resolve(str(data.get("name") or "")), "user", event.get("time"), turn)

    # -- assembly ----------------------------------------------------------------

    def build(self):
        header = next((e for e in self.events if e.get("type") == "session"), {})
        self.header = header
        self.session = str(header.get("id") or "unknown")
        self.cwd = str(header.get("cwd") or "")
        if header.get("origin") == "subagent" or header.get("delegationDepth"):
            self.scope = self.session
        authoring = False
        try:
            Path(os.path.realpath(self.cwd)).relative_to(lib.ROOT)
            authoring = True
        except (ValueError, OSError):
            authoring = False
        self.add("session_start", header.get("createdAt"), source="dsh", model=header.get("model"),
                 project=Path(self.cwd).name or "?", authoring=authoring,
                 agent_preset=header.get("agentPreset"), origin=header.get("origin") or "main")
        link = self.children.get(self.session)
        if link:
            # Filed under the parent session, because that is where analyze.py looks a
            # child up when it joins the child's usage to the parent turn.
            record_session, self.session = self.session, link[1]
            self.add("agent_link", header.get("createdAt"), tool_use_id=link[0], child_agent_id=record_session)
            self.session = record_session

        turn = 0
        for event in sorted(self.events, key=lambda e: e.get("seq") if e.get("seq") is not None else -1):
            kind, data = event.get("type"), event.get("data") or {}
            if kind == "turn/start":
                turn = int(data.get("turn") or turn)
            elif kind == "user/message":
                self.on_prompt(event, turn)
            elif kind == "assistant/message":
                self.on_message(event, turn)
            elif kind == "tool/call":
                self.on_tool_call(event, turn)
            elif kind == "tool/result":
                self.on_tool_result(event, turn)
            elif kind == "turn/end":
                failed = (data.get("reason") or {}).get("kind") in ("error", "failed")
                self.add("turn_failed" if failed else "turn_end", event.get("time"),
                         prompt_id=f"t{data.get('turn')}")
            elif kind == "command/run":
                self.on_command(event, turn)
            if self.activated:
                self.activated = False
                self.open_segment(turn, self.last_activation or self.turn_ts.get(turn) or 0)
        end_ms = max((e.get("time") or 0 for e in self.events), default=0) + 1
        self._flush_usage(end_ms)
        return self.records

    def _flush_usage(self, end_ms):
        """Every segment that saw an assistant message becomes a `usage` record.

        The last segment of every turn takes the stack as it stood when the turn
        ended, so a skill loaded mid-turn is also charged the turn's tail — but the
        earlier segments keep the stack they were opened with, or every part of one
        turn would claim the whole turn.
        """
        for turn in sorted(self.turn_segments):
            parts = [p for p in self.turn_segments[turn] if p["calls"]]
            if parts:
                parts[-1]["skills"] = self.stack_before(end_ms)
            for part in parts:
                self.records.append({k: v for k, v in part.items() if not k.startswith("_")})


def spawn_calls(events):
    """callId -> (the turn it was made in, its timestamp), for the `subagent` calls of one log.

    A child session reports its usage under the *parent's* turn, because that is the
    turn whose skill stack the child inherits; the child's own turn numbers start at
    one and would collide with the parent's.
    """
    calls, turn = {}, 0
    for event in sorted(events, key=lambda e: e.get("seq") if e.get("seq") is not None else -1):
        if event.get("type") == "turn/start":
            turn = int((event.get("data") or {}).get("turn") or turn)
        elif event.get("type") == "tool/call" and (event.get("data") or {}).get("name") in AGENT_TOOLS:
            call_id = (event.get("data") or {}).get("callId")
            calls[call_id] = (f"t{turn}", event.get("time") or 0)
    return calls


def collect(base, root=None, since=0.0, catalog=None, force=False):
    """Append every new dsh record to `<data>/sessions/`, and report what was collected."""
    catalog = catalog or lib.Catalog()
    paths = dsh_logs.session_paths(root, since)
    result = {"root": str(root or dsh_logs.sessions_root()), "logs": len(paths), "collected": 0,
              "records": 0, "skipped": 0, "errors": [], "sessions": []}
    if not paths:
        result["errors"].append(f"no session logs under {result['root']}")
        return result
    if not dsh_logs.decode_available():
        # One actionable error beats one per log.
        result["errors"].append(f"cannot decode session logs: {dsh_logs.NODE_HINT} is required")
        return result

    decoded, spawn_index, headers = {}, {}, {}
    for path in paths:
        try:
            events = dsh_logs.decode_events(path)
        except dsh_logs.DecodeError as exc:
            result["errors"].append(str(exc))
            continue
        decoded[path] = events
        header = dsh_logs.session_header(events)
        headers[str(header.get("id") or path.parent.name)] = header
        spawn_index[str(header.get("id") or path.parent.name)] = spawn_calls(events)

    links = {}
    for sid, header in headers.items():
        parent = header.get("parentSession")
        if not parent or parent not in spawn_index:
            continue
        created = header.get("createdAt") or 0
        candidates = [(ts, call_id, pid) for call_id, (pid, ts) in spawn_index[parent].items()]
        if not candidates:
            continue
        closest = min(candidates, key=lambda item: abs(item[0] - created))
        if abs(closest[0] - created) <= 300_000:
            links[sid] = (closest[1], str(parent), closest[2])

    cursors = Cursors(base)
    seen = cursors.load()
    for path, events in decoded.items():
        key = str(path)
        session = str(dsh_logs.session_header(events).get("id") or path.parent.name)
        cursor = -1 if force else int(seen.get(key, -1))
        if not [e for e in events if (e.get("seq") if e.get("seq") is not None else -1) > cursor]:
            result["skipped"] += 1
            continue
        try:
            records = LogToRecords(events, catalog, links).build()
        except Exception:  # noqa: BLE001 - one odd log must not stop the rest
            result["errors"].append(f"{path}: {traceback.format_exc().strip().splitlines()[-1][:200]}")
            continue
        lib.append(base, session, records)
        seen[key] = max((e.get("seq") for e in events if e.get("seq") is not None), default=cursor)
        result["collected"] += 1
        result["records"] += len(records)
        result["sessions"].append({"session": session, "records": len(records),
                                   "project": Path(str(headers.get(session, {}).get("cwd") or "")).name,
                                   "origin": headers.get(session, {}).get("origin") or "main"})
    cursors.save(seen)
    return result


def status(base, root=None, since=0.0):
    """What is collectible right now. Writes nothing."""
    paths = dsh_logs.session_paths(root, since)
    report = {"root": str(root or dsh_logs.sessions_root()), "decoder": dsh_logs.decode_available(),
              "sessions_on_disk": len(paths), "uncollected": 0, "unreadable": [],
              "collected_files": len(list((base / "sessions").glob("*.jsonl"))) if (base / "sessions").is_dir() else 0}
    if not report["decoder"]:
        report["hint"] = f"decoding dsh session logs needs {dsh_logs.NODE_HINT}"
    if not report["decoder"] or not paths:
        return report  # nothing to read: say so instead of failing once per log
    cursors = Cursors(base).load()
    for path in paths:
        try:
            events = dsh_logs.decode_events(path)
        except dsh_logs.DecodeError as exc:
            report["unreadable"].append(str(exc))
            continue
        top = max((e.get("seq") for e in events if e.get("seq") is not None), default=-1)
        if top > int(cursors.get(str(path), -1)):
            report["uncollected"] += 1
    return report


def log_error(base, message):
    if base is None:
        return
    path = base / "errors.log"
    try:
        if path.exists() and path.stat().st_size > ERROR_LOG_LIMIT:
            path.write_text("", encoding="utf-8")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"--- {lib.now_iso()} dsh\n{message}\n")
    except OSError:
        pass


def parse_since(value, now=None):
    now = now or time.time()
    text = str(value).strip()
    if text in ("0", "all"):
        return 0.0
    if re.fullmatch(r"\d{9,}", text):  # an epoch, as analyze.py passes it
        return float(text)
    match = re.fullmatch(r"(\d+)([hdw])", text)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return now - amount * WINDOW[unit]
    try:
        return time.mktime(time.strptime(text, "%Y-%m-%d"))
    except ValueError:
        raise SystemExit(f"--since wants 30d, 12h, 2w, YYYY-MM-DD or an epoch, not {value!r}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="0", help="only logs touched since: 30d, 12h, 2w or YYYY-MM-DD")
    parser.add_argument("--root", type=Path, default=None, help="sessions root (default: $DSH_HOME/sessions)")
    parser.add_argument("--status", action="store_true", help="report what is collectible; write nothing")
    parser.add_argument("--json", action="store_true", help="print the result as JSON")
    args = parser.parse_args(argv)

    if os.environ.get("SKILL_USAGE_DISABLE"):
        print(json.dumps({"disabled": True}) if args.json else "collection disabled (SKILL_USAGE_DISABLE)")
        return 0
    base = lib.data_dir()
    if base is None:
        print("no writable data directory", file=sys.stderr)
        return 1
    since = parse_since(args.since)
    if args.status:
        report = status(base, args.root, since)
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"dsh sessions root  {report['root']}")
            print(f"node decoder       {'available' if report['decoder'] else 'MISSING (need ' + dsh_logs.NODE_HINT + ')'}")
            print(f"session logs       {report['sessions_on_disk']}")
            print(f"not yet collected  {report['uncollected']}")
            for message in report["unreadable"][:3]:
                print(f"  unreadable: {message}", file=sys.stderr)
        # No decoder, no logs, or logs that cannot be read: collection is off either way.
        return 0 if report["decoder"] and report["sessions_on_disk"] and not report["unreadable"] else 1

    result = collect(base, args.root, since)
    for message in result["errors"]:
        log_error(base, message)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    print(f"{result['collected']}/{result['logs']} log(s) collected, {result['records']} record(s) appended, "
          f"{result['skipped']} already up to date")
    for message in result["errors"][:5]:
        print(f"  error: {message}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
