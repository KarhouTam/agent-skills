"""Summarise collected skill usage into an insight markdown file.

Reads the records the hooks wrote (see reference/data-model.md), joins them into
skill *runs* — one skill activated in one turn — and writes
insights/<date>.md with deterministic tables plus an empty `## Decisions`
section for the agent to fill in. A machine-readable snapshot goes to
<data>/snapshots/<date>.json so the next insight can show trends.

    python3 analyze.py                     # last 30 days -> insights/<today>.md
    python3 analyze.py --since 7d          # or 2w, or 2026-09-01
    python3 analyze.py --stdout            # print the markdown, write nothing
    python3 analyze.py --json              # print the summary as JSON, write nothing
"""

import argparse
import calendar
import hashlib
import json
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import usage_lib as lib  # noqa: E402

INSIGHTS = lib.SKILL_DIR / "insights"
DAY = 86400.0
MIN_COVERAGE_DAYS = 14  # never call a skill unused on less evidence than this


# --------------------------------------------------------------------------- helpers


def parse_ts(value):
    if not value:
        return None
    match = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(\.\d+)?", str(value))
    if not match:
        return None
    base = calendar.timegm(time.strptime(f"{match.group(1)}T{match.group(2)}", "%Y-%m-%dT%H:%M:%S"))
    return base + float(match.group(3) or 0)


def parse_since(value, now):
    match = re.fullmatch(r"(\d+)([dwh])", value.strip())
    if match:
        unit = {"h": 3600, "d": DAY, "w": 7 * DAY}[match.group(2)]
        return now - int(match.group(1)) * unit
    return calendar.timegm(time.strptime(value.strip(), "%Y-%m-%d"))


def day(epoch):
    return time.strftime("%Y-%m-%d", time.gmtime(epoch)) if epoch else "—"


def fmt(n):
    if n is None:
        return "—"
    n = float(n)
    if abs(n) >= 1e6:
        return f"{n / 1e6:.2f}M"
    if abs(n) >= 1e3:
        return f"{n / 1e3:.1f}k"
    return f"{n:.0f}"


def median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def p90(values):
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    return values[min(len(values) - 1, int(round(0.9 * (len(values) - 1))))]


def pct(part, whole):
    return f"{100 * part / whole:.0f}%" if whole else "—"


def cell(text, limit=90):
    text = " ".join(str(text or "").split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def bare_agent(agent_id):
    """SubagentStart/Stop and the Agent tool's `agentId` may differ by an `agent-` prefix."""
    return re.sub(r"^agent-", "", str(agent_id or ""))


def total(vector):
    """What a skill run cost: every token read and written across its span.

    This includes cache reads. They are mostly fixed by the session, not the skill,
    but under dsh they are ~99% of the total and are reported as their own column so
    a reader can see which part of a run's cost the skill actually caused.
    """
    return vector["input"] + vector["cache_creation"] + vector["cache_read"] + vector["output"]


def empty_usage():
    return {"calls": 0, "input": 0, "cache_creation": 0, "cache_read": 0, "output": 0}


def add_usage(into, record):
    for key in into:
        into[key] += int(record.get(key) or 0)


# --------------------------------------------------------------------------- analysis


class Turns:
    """Canonical turn ids.

    Hooks report `prompt_id`; the transcript parser reports the transcript's own
    `promptId` (or the prompt line's uuid on older versions) plus `turn_ts`, the
    timestamp of that prompt line. They normally agree; when they do not, a
    transcript turn maps to the hook prompt submitted nearest to its prompt line.
    """

    def __init__(self, records):
        self.prompts = defaultdict(list)
        self.known = defaultdict(set)
        for r in records:
            if r.get("kind") == "prompt" and r.get("prompt_id"):
                self.prompts[r.get("session_id")].append((parse_ts(r.get("ts")) or 0, r["prompt_id"]))
                self.known[r.get("session_id")].add(r["prompt_id"])
        self.memo = {}

    def canon(self, session, prompt_id, turn_ts=None):
        if prompt_id in self.known[session] or not self.prompts[session]:
            return prompt_id
        key = (session, prompt_id)
        if key not in self.memo and turn_ts:
            epoch = parse_ts(turn_ts)
            at, best = min(((abs(at - epoch), pid) for at, pid in self.prompts[session]), default=(None, None))
            if best is not None and at < 30:
                self.memo[key] = best
        return self.memo.get(key, prompt_id)


def analyze(records, catalog, since, until, previous=None):
    records = [r for r in records if since <= (parse_ts(r.get("ts")) or since) <= until]
    turns = Turns(records)

    sessions = defaultdict(lambda: {"project": "?", "authoring": False, "start": None})
    prompts, outcomes, subagent_turn = {}, {}, {}
    links, spawns = {}, {}
    results, loads = {}, defaultdict(list)
    resources = []
    catalogs = []
    by_kind = Counter(r.get("kind") for r in records)

    for r in records:
        sid, kind = r.get("session_id"), r.get("kind")
        if kind == "session_start":
            sessions[sid].update(project=r.get("project") or "?", authoring=bool(r.get("authoring")),
                                 start=parse_ts(r.get("ts")))
        elif kind == "prompt":
            prompts[(sid, r.get("prompt_id"))] = r
        elif kind == "turn_end":
            outcomes[(sid, r.get("prompt_id"))] = "completed"
        elif kind == "turn_failed":
            outcomes.setdefault((sid, r.get("prompt_id")), "failed")
        elif kind == "subagent_start":
            subagent_turn[(sid, bare_agent(r.get("agent_id")))] = r.get("prompt_id")
        elif kind == "agent_link" and r.get("child_agent_id"):
            links[(sid, bare_agent(r["child_agent_id"]))] = r.get("tool_use_id")
        elif kind == "spawn":
            spawns[(sid, r.get("tool_use_id"))] = r
        elif kind == "activation_result":
            results[(sid, r.get("tool_use_id"))] = r
        elif kind == "load":
            loads[(sid, r.get("tool_use_id"))].append(r)
        elif kind == "resource":
            resources.append(r)
        elif kind == "catalog":
            catalogs.append(r)

    def turn_of(r):
        sid, scope = r.get("session_id"), r.get("scope")
        if scope is None:  # a hook record: prompt_id names the user turn, inside subagents too
            prompt = r.get("prompt_id") or subagent_turn.get((sid, bare_agent(r.get("agent_id"))))
            return turns.canon(sid, prompt)
        if scope == "main":
            return turns.canon(sid, r.get("prompt_id"), r.get("turn_ts"))
        # A subagent record names the parent turn it belongs to: `parent_prompt_id` when the
        # collector knows it (both harnesses do), otherwise the child's own prompt line.
        parent = r.get("parent_prompt_id") or r.get("prompt_id") or subagent_turn.get((sid, bare_agent(scope)))
        return turns.canon(sid, parent)

    # ---- activations -> runs
    # Hooks and the transcript parser both report activations. Tool calls dedupe on
    # tool_use_id; slash commands carry none, so they dedupe on (turn, skill, via).
    activations = {}
    authoring_reads = Counter()
    for r in records:
        if r.get("kind") not in ("activation", "t_activation"):
            continue
        sid = r.get("session_id")
        if r.get("via") == "read" and sessions[sid]["authoring"]:
            authoring_reads[r.get("skill")] += 1
            continue
        scope = r.get("scope") or (bare_agent(r["agent_id"]) if r.get("agent_id") else "main")
        entry = {"session": sid, "turn": turn_of(r), "skill": r.get("skill"), "via": r.get("via"),
                 "scope": "main" if scope == "main" else "subagent", "ts": parse_ts(r.get("ts")),
                 "hash": r.get("hash"), "tool_use_id": r.get("tool_use_id"), "source": r.get("source")}
        key = (sid, r["tool_use_id"]) if r.get("tool_use_id") else (sid, entry["turn"], entry["skill"], entry["via"])
        merged = activations.setdefault(key, entry)
        for field in ("hash", "source"):
            merged[field] = merged.get(field) or entry.get(field)
        if entry["ts"] and (not merged["ts"] or entry["ts"] < merged["ts"]):
            merged["ts"] = entry["ts"]

    runs = {}
    for act in sorted(activations.values(), key=lambda a: a["ts"] or 0):
        result = results.get((act["session"], act["tool_use_id"]))
        if result and not result.get("ok"):
            act["failed"] = result.get("error") or "failed"
        key = (act["session"], act["turn"], act["skill"])
        run = runs.get(key)
        if run is None:
            run = runs[key] = {"session": act["session"], "turn": act["turn"], "skill": act["skill"],
                               "via": act["via"], "scope": act["scope"], "ts": act["ts"], "hash": act["hash"],
                               "attempts": 0, "failures": [], "usage": empty_usage(), "exclusive": 0,
                               "authoring": sessions[act["session"]]["authoring"],
                               "subagent_tokens": 0, "subagents": 0, "loads": []}
        run["attempts"] += 1
        if act.get("failed"):
            run["failures"].append(act["failed"])
        run["hash"] = run["hash"] or act["hash"]
        for load in loads.get((act["session"], act["tool_use_id"]), []):
            run["loads"].append(load)

    # ---- usage -> runs (inclusive: every skill on the stack; exclusive: the innermost)
    turn_usage = defaultdict(empty_usage)

    def stack_at_spawn(sid, agent, depth=0):
        tool_use_id = links.get((sid, agent))
        spawn = spawns.get((sid, tool_use_id))
        if spawn is None or depth > 5:
            return None
        outer = [] if spawn.get("scope") in (None, "main") else (stack_at_spawn(sid, bare_agent(spawn["scope"]), depth + 1) or [])
        return outer + [s for s in spawn.get("skills", []) if s not in outer]

    approx_agents = set()
    for r in records:
        if r.get("kind") != "usage":
            continue
        sid, scope = r.get("session_id"), r.get("scope") or "main"
        turn = turn_of(r)
        stack = list(r.get("skills") or [])
        inherited = []
        if scope != "main":
            agent = bare_agent(scope)
            inherited = stack_at_spawn(sid, agent)
            if inherited is None:
                approx_agents.add((sid, agent))
                inherited = [run["skill"] for run in runs.values()
                             if run["session"] == sid and run["turn"] == turn and run["scope"] == "main"]
            stack = inherited + [s for s in stack if s not in inherited]
        add_usage(turn_usage[(sid, turn)], r)
        for index, skill in enumerate(stack):
            run = runs.get((sid, turn, skill))
            if run is None:
                continue
            add_usage(run["usage"], r)
            if index == len(stack) - 1:
                run["exclusive"] += total(r)
            if scope != "main" and skill in inherited:
                run["subagent_tokens"] += total(r)

    for (sid, tool_use_id), spawn in spawns.items():
        for skill in spawn.get("skills", []):
            run = runs.get((sid, turn_of(spawn), skill))
            if run:
                run["subagents"] += 1

    # ---- outcomes: no Stop for the turn, yet the session moved on to another prompt = interrupted
    last_prompt = defaultdict(float)
    for (sid, _pid), p in prompts.items():
        last_prompt[sid] = max(last_prompt[sid], parse_ts(p.get("ts")) or 0)
    for run in runs.values():
        outcome = outcomes.get((run["session"], run["turn"]))
        if outcome is None:
            started = parse_ts(prompts.get((run["session"], run["turn"]), {}).get("ts")) or run["ts"] or 0
            outcome = "interrupted" if last_prompt[run["session"]] > started else "open"
        run["outcome"] = outcome

    # ---- per skill
    skills = {}
    ids = set(catalog.skills) | {run["skill"] for run in runs.values()}
    for skill_id in ids:
        info = catalog.skills.get(skill_id, {})
        mine = [r for r in runs.values() if r["skill"] == skill_id]
        ok = [r for r in mine if r["attempts"] > len(r["failures"])]
        totals = [total(r["usage"]) for r in ok if r["usage"]["calls"]]
        cached = [r["usage"]["cache_read"] for r in ok if r["usage"]["calls"]]
        clean = [l["tokens"] for r in ok for l in r["loads"] if l.get("clean")]
        versions = defaultdict(list)
        for r in ok:
            versions[r["hash"] or "?"].append(r)
        skills[skill_id] = {
            "id": skill_id,
            "tier": catalog.tier(skill_id),
            "repo": skill_id in catalog.skills,
            "installed": skill_id in catalog.skills,
            "model_invocable": info.get("model_invocable"),
            "listing_tokens": info.get("listing_tokens", 0) if info.get("model_invocable") else 0,
            "body_tokens": info.get("body_tokens"),
            "parent": info.get("parent"),
            "runs": len(ok),
            "runs_authoring": sum(1 for r in ok if r["authoring"]),
            "failed_attempts": sum(len(r["failures"]) for r in mine),
            "errors": Counter(e.splitlines()[0][:120] for r in mine for e in r["failures"]).most_common(3),
            "via": dict(Counter(r["via"] for r in ok)),
            "subagent_runs": sum(1 for r in ok if r["scope"] == "subagent"),
            "sessions": len({r["session"] for r in ok}),
            "projects": sorted({sessions[r["session"]]["project"] for r in ok}),
            "first": min((r["ts"] for r in ok if r["ts"]), default=None),
            "last": max((r["ts"] for r in ok if r["ts"]), default=None),
            "tokens_median": median(totals),
            "tokens_p90": p90(totals),
            "tokens_sum": sum(totals),
            "tokens_median_real": median([total(r["usage"]) for r in ok
                                          if r["usage"]["calls"] and not r["authoring"]]),
            "cache_median": median(cached),
            "output_median": median([r["usage"]["output"] for r in ok if r["usage"]["calls"]]),
            "calls_median": median([r["usage"]["calls"] for r in ok if r["usage"]["calls"]]),
            "subagent_share": (sum(r["subagent_tokens"] for r in ok) / sum(totals)) if sum(totals) else 0,
            "subagents": sum(r["subagents"] for r in ok),
            "load_median": median(clean or [l["tokens"] for r in ok for l in r["loads"]]),
            "measured_runs": len(totals),
            "outcomes": dict(Counter(r["outcome"] for r in ok)),
            "versions": {
                h: {"runs": len(rs), "tokens_median": median([total(r["usage"]) for r in rs if r["usage"]["calls"]]),
                    "first": min((r["ts"] for r in rs if r["ts"]), default=None),
                    "last": max((r["ts"] for r in rs if r["ts"]), default=None),
                    "interrupted": sum(1 for r in rs if r["outcome"] == "interrupted")}
                for h, rs in versions.items()
            },
            "current_hash": info.get("hash"),
        }

    # ---- resources (only from sessions that were not editing this repo)
    files = defaultdict(lambda: {"exec": 0, "read": 0, "edit": 0, "fail": 0, "last_error": None, "last": None})
    authoring_edits = Counter()
    for r in resources:
        if sessions[r.get("session_id")]["authoring"] or r.get("op") == "edit":
            authoring_edits[r.get("skill")] += 1
            continue
        entry = files[(r.get("skill"), r.get("file"))]
        entry[r.get("op")] = entry.get(r.get("op"), 0) + 1
        if r.get("ok") is False:
            entry["fail"] += 1
            entry["last_error"] = (r.get("error") or "").splitlines()[0][:120] if r.get("error") else "failed"
        entry["last"] = max(entry["last"] or 0, parse_ts(r.get("ts")) or 0)
    for skill_id, data in skills.items():
        if not data["installed"]:
            data["files_total"], data["files_used"], data["untouched"] = 0, 0, []
            continue
        shipped = [f for f in catalog.scripts(skill_id) if f != "SKILL.md" and not f.startswith(("tests/", "evals/"))]
        used = {f for (s, f) in files if s == skill_id}
        data["files_total"], data["files_used"] = len(shipped), len(used & set(shipped))
        data["untouched"] = [f for f in shipped if f not in used]

    # ---- sub-skill edges
    edges = []
    declared = set()
    by_turn = defaultdict(list)
    for run in runs.values():
        by_turn[(run["session"], run["turn"])].append(run)
    for edge in catalog.sub_skill_edges():
        declared.add((edge["parent"], edge["child"]))
        parents = [r for r in runs.values() if r["skill"] == edge["parent"] and r["attempts"] > len(r["failures"])]
        same = later = script_only = 0
        misses = []
        for run in parents:
            in_turn = any(o["skill"] == edge["child"] for o in by_turn[(run["session"], run["turn"])])
            after = any(o["skill"] == edge["child"] and o["session"] == run["session"] and (o["ts"] or 0) > (run["ts"] or 0)
                        and o["turn"] != run["turn"] for o in runs.values())
            scripted = any(x.get("skill") == edge["child"] and x.get("op") == "exec"
                           and x.get("session_id") == run["session"] and turn_of(x) == run["turn"] for x in resources)
            same += in_turn
            later += after and not in_turn
            script_only += scripted and not in_turn
            if not in_turn and not after:
                prompt = prompts.get((run["session"], run["turn"]), {})
                misses.append({"ts": run["ts"], "prompt": prompt.get("preview", ""), "scripted": scripted})
        edges.append(dict(edge, parent_runs=len(parents), same_turn=same, later=later, script_only=script_only,
                          misses=sorted(misses, key=lambda m: -(m["ts"] or 0))[:5]))
    co = Counter()
    for group in by_turn.values():
        ordered = sorted(group, key=lambda r: r["ts"] or 0)
        for i, first in enumerate(ordered):
            for second in ordered[i + 1:]:
                pair = (first["skill"], second["skill"])
                if pair[0] != pair[1] and pair not in declared and pair[0] in catalog.skills and pair[1] in catalog.skills:
                    co[pair] += 1

    # ---- prompts
    turn_rows = []
    turn_keys = set(prompts) | set(by_turn) | set(turn_usage)
    for key in turn_keys:
        prompt = prompts.get(key, {})
        ts = parse_ts(prompt.get("ts")) or min((r["ts"] or 0 for r in by_turn.get(key, [])), default=0)
        turn_rows.append({"session": key[0], "ts": ts, "project": sessions[key[0]]["project"],
                          "prompt": prompt.get("preview", ""), "slash": prompt.get("slash"),
                          "skills": [f"{r['skill']} ({r['via']}{', sub' if r['scope'] == 'subagent' else ''})"
                                     for r in sorted(by_turn.get(key, []), key=lambda r: r["ts"] or 0)],
                          "tokens": total(turn_usage[key]) if key in turn_usage else None,
                          "authoring": sessions[key[0]]["authoring"]})
    turn_rows.sort(key=lambda t: -t["ts"])

    # ---- measured listing cost (dsh publishes the catalog it sends, so this is observed)
    listing = defaultdict(list)
    for r in catalogs:
        tokens, entries = int(r.get("tokens") or 0), int(r.get("entries") or 0)
        if not tokens:
            continue
        for skill_id in r.get("skills") or []:
            listing[skill_id].append(tokens / entries if entries else tokens)
    for skill_id, data in skills.items():
        seen = listing.get(skill_id, [])
        data["catalog_tokens"] = round(median(seen)) if seen else None
        data["catalog_emissions"] = len(seen)

    # ---- coverage
    stamps = [parse_ts(r.get("ts")) for r in records if r.get("ts")]
    first_seen = min(stamps) if stamps else None
    coverage = ((until - first_seen) / DAY) if first_seen else 0

    summary = {
        "generated": lib.now_iso(),
        "window": {"since": since, "until": until, "first_record": first_seen, "coverage_days": coverage},
        "totals": {
            "sessions": len({r.get("session_id") for r in records}),
            "authoring_sessions": sum(1 for s in sessions.values() if s["authoring"]),
            "turns": len(turn_keys),
            "turns_with_skill": len(by_turn),
            "runs": sum(s["runs"] for s in skills.values()),
            "tokens": sum(total(u) for u in turn_usage.values()),
            "tokens_cache_read": sum(u["cache_read"] for u in turn_usage.values()),
            "skill_tokens": sum(total(u) for k, u in turn_usage.items() if k in by_turn),
            "records": dict(by_kind),
            "approx_subagents": len(approx_agents),
        },
        "skills": skills,
        "edges": edges,
        "undeclared": [{"parent": a, "child": b, "count": n} for (a, b), n in co.most_common() if n >= 2],
        "files": {f"{s}/{f}": v for (s, f), v in files.items()},
        "catalogs": [{"ts": parse_ts(r.get("ts")), "skills": len(r.get("skills") or []),
                      "entries": r.get("entries"), "tokens": r.get("tokens")} for r in catalogs],
        "authoring": {"reads": dict(authoring_reads), "edits": dict(authoring_edits)},
        "turns": turn_rows,
        "previous": previous,
    }
    summary["flags"] = flags(summary)
    return summary


def flags(summary):
    """Deterministic findings, most actionable first. The agent judges them; it does not invent new ones blindly."""
    out = []
    enough = summary["window"]["coverage_days"] >= MIN_COVERAGE_DAYS
    skills = summary["skills"]
    medians = [s["tokens_median_real"] for s in skills.values() if s["tokens_median_real"]]
    overall = median(medians)
    for s in sorted(skills.values(), key=lambda s: s["id"]):
        if not s["repo"] or s["parent"]:
            continue
        sid = s["id"]
        if s["runs"] == 0 and enough:
            listing = s["catalog_tokens"] or s["listing_tokens"]
            cost = f"; listed in {s['catalog_emissions']} catalogs at ~{fmt(listing)} tokens each" if s["catalog_emissions"] \
                else (f"; costs ~{fmt(listing)} listing tokens every turn" if listing else "")
            out.append(("retire?", sid, f"no runs in {summary['window']['coverage_days']:.0f} days of data{cost}"))
        if s["model_invocable"] and s["runs"] >= 3 and s["via"].get("model", 0) == 0:
            out.append(("under-triggered", sid, f"all {s['runs']} runs were user-invoked; the description never fired on its own"))
        blocked = [e for e, _ in s["errors"] if "disable-model-invocation" in e or "cannot be used" in e.lower()]
        if blocked:
            out.append(("model-blocked", sid, f"the model tried to invoke it and was refused ({blocked[0]})"))
        elif s["failed_attempts"]:
            out.append(("activation-failures", sid, f"{s['failed_attempts']} failed activation(s): {s['errors'][0][0] if s['errors'] else ''}"))
        real = s["tokens_median_real"]
        if s["runs"] >= 3 and overall and real and real > 2 * overall and len(medians) > 2:
            apart = f" ({s['runs_authoring']}/{s['runs']} runs were authoring, excluded)" if s["runs_authoring"] else ""
            out.append(("costly", sid, f"median run {fmt(real)} tokens vs {fmt(overall)} across skills{apart}"))
        bad = s["outcomes"].get("interrupted", 0) + s["outcomes"].get("failed", 0)
        if s["runs"] >= 3 and bad / s["runs"] >= 0.3:
            out.append(("abandoned", sid, f"{bad}/{s['runs']} runs interrupted or failed"))
        if len([h for h in s["versions"] if h != "?"]) > 1:
            out.append(("changed", sid, f"{len(s['versions'])} SKILL.md versions ran in this window — compare them under Versions"))
        if s["runs"] - s["runs_authoring"] >= 5 and s["files_total"] and s["untouched"]:
            out.append(("dead-weight?", sid, f"{len(s['untouched'])}/{s['files_total']} shipped files never read or run"))
    for name, f in summary["files"].items():
        runs = f["exec"] + f["read"]
        if f["exec"] >= 4 and f["fail"] / max(1, runs) >= 0.25:
            out.append(("script-failing", name.split("/", 1)[0], f"`{name}` failed {f['fail']}/{runs} times — last: {f['last_error']}"))
    for e in summary["edges"]:
        if e["parent_runs"] >= 3 and e["same_turn"] / e["parent_runs"] < 0.5:
            out.append(("sub-skill-gap", e["parent"], f"`{e['child']}` loaded in {e['same_turn']}/{e['parent_runs']} runs — check whether its condition applied"))
        if e["script_only"]:
            out.append(("sub-skill-bypassed", e["parent"], f"`{e['child']}` scripts ran {e['script_only']}x without the skill being loaded"))
    for u in summary["undeclared"]:
        out.append(("undeclared-pair", u["parent"], f"`{u['child']}` followed it in {u['count']} turns without a REQUIRED SUB-SKILL line"))
    order = ["model-blocked", "activation-failures", "script-failing", "abandoned", "sub-skill-gap", "sub-skill-bypassed",
             "under-triggered", "costly", "changed", "undeclared-pair", "retire?", "dead-weight?"]
    out.sort(key=lambda f: (order.index(f[0]), f[1]))
    return [{"type": t, "skill": s, "detail": d} for t, s, d in out]


# --------------------------------------------------------------------------- git versions


def version_labels(catalog, summary):
    """Map SKILL.md content hashes to the commit that introduced them, when git can tell."""
    labels = {}
    for skill in summary["skills"].values():
        if not skill["installed"] or len(skill["versions"]) < 2:
            continue
        rel = f"{skill['id']}/SKILL.md"
        try:
            log = subprocess.run(["git", "-C", str(catalog.root), "log", "-n", "30", "--format=%h %as", "--", rel],
                                 capture_output=True, text=True, timeout=10).stdout.split("\n")
            for line in filter(None, log):
                rev, date = line.split(" ", 1)
                blob = subprocess.run(["git", "-C", str(catalog.root), "show", f"{rev}:{rel}"],
                                      capture_output=True, timeout=10).stdout
                labels.setdefault(hashlib.sha1(blob).hexdigest()[:12], f"{rev} ({date})")
        except (OSError, subprocess.SubprocessError, ValueError):
            continue
    return labels


# --------------------------------------------------------------------------- rendering


def render(summary, labels=None, data_path=None):
    labels = labels or {}
    w, t = summary["window"], summary["totals"]
    skills = summary["skills"]
    prev = (summary.get("previous") or {}).get("skills", {})
    lines = [f"# Skill usage insight — {day(w['until'])}", ""]
    lines.append(
        f"Window {day(w['since'])} → {day(w['until'])} · data since {day(w['first_record'])} "
        f"({w['coverage_days']:.1f} days covered) · {t['sessions']} sessions · {t['turns']} turns · "
        f"{t['turns_with_skill']} turns with a skill · {t['runs']} skill runs"
    )
    if t["tokens"]:
        lines.append(f"Tokens: {fmt(t['tokens'])} read and written across {t['sessions']} sessions, of which "
                     f"{fmt(t['tokens_cache_read'])} ({pct(t['tokens_cache_read'], t['tokens'])}) are cache reads; "
                     f"{fmt(t['skill_tokens'])} ({pct(t['skill_tokens'], t['tokens'])}) is in turns that ran a skill.")
    if w["coverage_days"] < MIN_COVERAGE_DAYS:
        lines += ["", f"> **Thin evidence.** Under {MIN_COVERAGE_DAYS} days of data: absence of use is not a retirement "
                      "signal yet, and per-skill medians rest on few runs."]
    if summary.get("previous"):
        lines.append(f"Compared with the snapshot of {summary['previous'].get('date')}.")
    lines += ["", "## At a glance", ""]
    if summary["flags"]:
        lines += [f"- **{f['type']}** `{f['skill']}` — {f['detail']}" for f in summary["flags"][:20]]
        if len(summary["flags"]) > 20:
            lines.append(f"- … {len(summary['flags']) - 20} more in the sections below")
    else:
        lines.append("- Nothing stands out.")

    lines += ["", "## Skills", "",
              "Run = a skill activated in a turn; its lifecycle runs from activation to the end of that turn. "
              "Auth = runs whose session was started inside this repo, i.e. authoring the skill rather than using it; "
              "the cost columns keep them (they are real runs) but the cost flags compare only the rest. "
              "Tokens are everything the model read and wrote across that span, subagents included; the cache-read "
              "column is the part of it that was re-read context rather than new tokens.", "",
              "| Skill | Tier | Runs | Auth | Δ | Via model/user/read | Sess. | Last | Tokens/run (med · p90) | Cache read/run | Out/run | Calls/run | Load | Listed | Subagent share | Interrupted |",
              "|---|---|---:|---:|---:|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    used = sorted((s for s in skills.values() if s["runs"] or s["failed_attempts"]), key=lambda s: (-s["runs"], s["id"]))
    for s in used:
        via = s["via"]
        delta = ""
        if s["id"] in prev:
            delta = f"{s['runs'] - prev[s['id']].get('runs', 0):+d}"
        share = f"{100 * s['subagent_share']:.0f}%" if s["tokens_sum"] else "—"
        load = fmt(s["load_median"]) if s["load_median"] is not None else (f"~{fmt(s['body_tokens'])}" if s["body_tokens"] else "—")
        listed = f"{s['catalog_emissions']}×{fmt(s['catalog_tokens'])}" if s["catalog_emissions"] else (
            f"~{fmt(s['listing_tokens'])}/turn" if s["listing_tokens"] else "—")
        lines.append(
            f"| `{s['id']}` | {s['tier']} | {s['runs']}{' (+' + str(s['failed_attempts']) + ' failed)' if s['failed_attempts'] else ''} | "
            f"{s['runs_authoring']} | {delta} | "
            f"{via.get('model', 0)}/{via.get('user', 0)}/{via.get('read', 0)} | {s['sessions']} | {day(s['last'])} | "
            f"{fmt(s['tokens_median'])} · {fmt(s['tokens_p90'])} | {fmt(s['cache_median'])} | {fmt(s['output_median'])} | {fmt(s['calls_median'])} | "
            f"{load} | {listed} | {share} | "
            f"{s['outcomes'].get('interrupted', 0) + s['outcomes'].get('failed', 0)} |")
    unused = sorted(s["id"] for s in skills.values() if s["repo"] and not s["runs"] and not s["failed_attempts"] and not s["parent"])
    if unused:
        lines += ["", "Not run in this window: " + ", ".join(
            f"`{u}`" + (f" (~{fmt(skills[u]['catalog_tokens'] or skills[u]['listing_tokens'])} listing tokens/emission)"
                        if (skills[u]['catalog_tokens'] or skills[u]['listing_tokens']) else "")
            for u in unused)]
    if summary["catalogs"]:
        sizes = [c["entries"] for c in summary["catalogs"] if c["entries"]]
        tokens = [c["tokens"] for c in summary["catalogs"] if c["tokens"]]
        dead = [s for s in unused]
        dead_tokens = sum(skills[u]["catalog_tokens"] or skills[u]["listing_tokens"] for u in dead)
        lines.append(f"Catalog listings observed: {len(summary['catalogs'])} emission(s), "
                     f"{fmt(median(sizes))} entries and ~{fmt(median(tokens))} tokens each; "
                     f"~{fmt(dead_tokens)} of each emission is skills that never ran.")
    foreign = sorted(s["id"] for s in skills.values() if not s["repo"] and s["runs"])
    if foreign:
        lines.append("Outside this repo (plugins, built-ins, other dirs): " + ", ".join(f"`{f}` ×{skills[f]['runs']}" for f in foreign))

    lines += ["", "## Sub-skills", ""]
    if summary["edges"]:
        lines += ["| Parent → child | Declared as | Parent runs | Same turn | Later in session | Scripts only |",
                  "|---|---|---:|---:|---:|---:|"]
        for e in summary["edges"]:
            lines.append(f"| `{e['parent']}` → `{e['child']}` | {e['kind']}{' · ' + cell(e['section'], 40) if e['section'] else ''} | "
                         f"{e['parent_runs']} | {e['same_turn']} ({pct(e['same_turn'], e['parent_runs'])}) | {e['later']} | {e['script_only']} |")
        for e in summary["edges"]:
            if not e["misses"]:
                continue
            lines += ["", f"**`{e['parent']}` ran without `{e['child']}`** — gated on: _{cell(e['condition'], 300)}_", ""]
            lines += [f"- {day(m['ts'])}: {cell(m['prompt'], 140) or '(prompt not recorded)'}{' — child scripts ran directly' if m['scripted'] else ''}"
                      for m in e["misses"]]
    else:
        lines.append("No sub-skill relationships are declared in this repo.")
    if summary["undeclared"]:
        lines += ["", "Undeclared pairs (child activated after parent in the same turn):", ""]
        lines += [f"- `{u['parent']}` → `{u['child']}` ×{u['count']}" for u in summary["undeclared"]]

    lines += ["", "## Scripts and files", ""]
    if summary["files"]:
        lines += ["| File | Exec | Read | Fail | Last error |", "|---|---:|---:|---:|---|"]
        for name, f in sorted(summary["files"].items(), key=lambda kv: -(kv[1]["exec"] + kv[1]["read"]))[:25]:
            lines.append(f"| `{name}` | {f['exec']} | {f['read']} | {f['fail']} | {cell(f['last_error'] or '', 70)} |")
    else:
        lines.append("No skill file was read or run outside authoring sessions.")
    dead = [s for s in used if s["repo"] and s["runs"] >= 5 and s["untouched"]]
    for s in dead:
        shown = ", ".join(f"`{f}`" for f in s["untouched"][:8])
        lines.append(f"- `{s['id']}`: {s['files_used']}/{s['files_total']} shipped files used; untouched: {shown}"
                     f"{' …' if len(s['untouched']) > 8 else ''}")

    changed = [s for s in skills.values() if len(s["versions"]) > 1]
    if changed:
        lines += ["", "## Versions", "", "| Skill | SKILL.md version | Runs | Tokens/run | Interrupted | Seen |", "|---|---|---:|---:|---:|---|"]
        for s in changed:
            for h, v in sorted(s["versions"].items(), key=lambda kv: kv[1]["first"] or 0):
                label = labels.get(h, h) + (" (current)" if h == s["current_hash"] else "")
                lines.append(f"| `{s['id']}` | {label} | {v['runs']} | {fmt(v['tokens_median'])} | {v['interrupted']} | "
                             f"{day(v['first'])} → {day(v['last'])} |")

    lines += ["", "## Prompts → skills (latest 15)", "", "| When | Project | Prompt | Skills | Tokens |", "|---|---|---|---|---:|"]
    for row in [r for r in summary["turns"] if r["skills"]][:15]:
        lines.append(f"| {day(row['ts'])} | {cell(row['project'], 24)} | {cell(row['prompt'], 80) or '—'} | "
                     f"{', '.join(row['skills'])} | {fmt(row['tokens'])} |")
    quiet = [r for r in summary["turns"] if not r["skills"] and r["prompt"] and not r["slash"] and not r["authoring"]][:15]
    if quiet:
        lines += ["", "## Prompts that ran no skill (latest 15, for trigger review)", ""]
        lines += [f"- {day(r['ts'])} · {cell(r['project'], 24)}: {cell(r['prompt'], 140)}" for r in quiet]

    touched = Counter(summary["authoring"]["edits"])
    if touched:
        lines += ["", "Maintenance activity — skill files read or edited while working on this repo, not counted as use: "
                  + ", ".join(f"`{k}` ×{v}" for k, v in touched.most_common())]

    lines += ["", "## Decisions", "", "<!-- Filled in by the agent following SKILL.md: one line per skill that needs a decision -->", ""]
    lines += ["## Method", "",
              f"- Records: {', '.join(f'{k} {v}' for k, v in sorted(t['records'].items()))}.",
              "- Sources: Claude Code and Codex hooks (`via` a transcript or rollout flush) and dsh session logs, "
              "collected by `hooks/collect.py`, `hooks/collect_codex.py` and `hooks/collect_dsh.py` into the same records.",
              "- Listed = how often the harness published the skill in the model's catalog, and the median share of that "
              "message the skill accounts for (dsh and Codex publish one; the total message size is in the line above); "
              "`~n/turn` is the estimate from the description alone, used when no catalog was observed.",
              "- Activation sources: `model` = the harness's skill tool, `user` = a slash command or gesture, "
              "`read` = a SKILL.md read with the file tool (ignored in sessions started inside this repo, which are authoring).",
              "- Tokens are everything read and written across the run, subagents included (input + cache creation + "
              "cache read + output). Under dsh cache reads reach ~99% of that, so the cache-read column separates "
              "re-read context from the new tokens a run actually caused.",
              "- Load = context growth across the activation (tool result plus injected SKILL.md), from the next API call; "
              "`~n` is a size estimate of SKILL.md where no activating call exists (slash commands).",
              "- Interrupted = the turn never completed but the session continued; failed = the turn ended with an error.",
              f"- Subagent tokens joined to the parent skill through the call that spawned them, when the child's log can "
              f"be matched to it; {t['approx_subagents']} subagent(s) could not be joined and were attributed to every "
              "main-thread skill of their turn."]
    if data_path:
        lines.append(f"- Data: `{data_path}`.")
    return "\n".join(lines) + "\n"


def snapshot(summary):
    return {"date": day(summary["window"]["until"]),
            "skills": {k: {"runs": v["runs"], "tokens_median": v["tokens_median"]} for k, v in summary["skills"].items()}}


def previous_snapshot(base, today):
    folder = base / "snapshots"
    if not folder.is_dir():
        return None
    older = sorted(p for p in folder.glob("*.json") if p.stem < today)
    if not older:
        return None
    try:
        return json.loads(older[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


DECISIONS = re.compile(r"(## Decisions\n)(.*?)(\n## Method\n)", re.S)


def keep_decisions(path, markdown):
    """Re-running on the same day refreshes the numbers but keeps decisions already written."""
    if not path.exists():
        return markdown
    old = DECISIONS.search(path.read_text(encoding="utf-8"))
    if not old or ("<!--" in old.group(2) and old.group(2).count("\n") <= 3):  # still the empty placeholder
        return markdown
    return DECISIONS.sub(lambda m: m.group(1) + old.group(2) + m.group(3), markdown, count=1)


def collect_dsh(base, since=0.0):
    """Pick up new dsh session logs before reading. dsh needs no hooks, so this is the install.

    The whole corpus is offered every run, not just the analysis window: the cursor
    in `<data>/state/dsh.json` skips what is already collected, and a wide window
    later must not find a hole where a narrow one was collected first.

    Returns the collector's report, or `{"errors": [...]}` when it could not run.
    A failed collection is not fatal: the records already collected still count.
    """
    collector = lib.SKILL_DIR / "hooks" / "collect_dsh.py"
    if not collector.is_file():
        return None
    try:
        done = subprocess.run([sys.executable, str(collector), "--json", "--since", "0"],
                              capture_output=True, text=True, timeout=900)
        report = json.loads(done.stdout) if done.stdout.strip() else {}
        if done.returncode != 0 and not report.get("errors"):
            report.setdefault("errors", []).append((done.stderr or "").strip()[:200] or f"exit {done.returncode}")
        return report
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"errors": [str(exc)[:200]]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--since", default="30d", help="window start: 30d, 2w, 12h or YYYY-MM-DD (default: %(default)s)")
    parser.add_argument("--out", type=Path, help="insight path (default: insights/<today>.md)")
    parser.add_argument("--stdout", action="store_true", help="print the markdown instead of writing it")
    parser.add_argument("--json", action="store_true", help="print the summary JSON instead of writing anything")
    parser.add_argument("--no-collect", action="store_true", help="do not pick up new dsh session logs first")
    args = parser.parse_args(argv)

    now = time.time()
    base = lib.data_dir()
    if base is None:
        print("no writable data directory (set SKILL_USAGE_DATA_DIR)", file=sys.stderr)
        return 1
    since = parse_since(args.since, now)
    collected = None if args.no_collect else collect_dsh(base)
    collection_errors = (collected or {}).get("errors") or []
    if collection_errors:
        print(f"dsh collection failed: {collection_errors[0]}", file=sys.stderr)
    today = day(now)
    catalog = lib.Catalog()
    previous = previous_snapshot(base, today)
    records = list(lib.read_records(base, since_epoch=since))
    if not records:
        print("no usage data: install the Claude Code hooks with scripts/install_hooks.py, or run a dsh session "
              "(its logs are collected by hooks/collect_dsh.py)", file=sys.stderr)
        return 1
    summary = analyze(records, catalog, since, now, previous=previous)
    summary["totals"]["dsh_sessions_collected"] = (collected or {}).get("collected", 0)
    summary["totals"]["dsh_records_collected"] = (collected or {}).get("records", 0)
    errors = base / "errors.log"
    text = errors.read_text(encoding="utf-8", errors="replace") if errors.exists() else ""
    summary["totals"]["hook_errors"] = sum(1 for line in text.splitlines() if line.startswith("--- "))

    if args.json:
        print(json.dumps(summary, indent=1, default=str, ensure_ascii=False))
        return 0
    markdown = render(summary, version_labels(catalog, summary), data_path=base)
    if summary["totals"]["hook_errors"]:
        markdown = markdown.replace("## Method\n", f"## Method\n\n- {summary['totals']['hook_errors']} hook error(s) "
                                    f"logged in `{errors}`.\n", 1)
    if args.stdout:
        print(markdown, end="")
        return 0

    out = args.out or INSIGHTS / f"{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    markdown = keep_decisions(out, markdown)
    out.write_text(markdown, encoding="utf-8")
    (base / "snapshots").mkdir(exist_ok=True)
    (base / "snapshots" / f"{today}.json").write_text(json.dumps(snapshot(summary)), encoding="utf-8")
    print(f"insight: {out}")
    print(f"window: {summary['window']['coverage_days']:.1f} days covered, {summary['totals']['runs']} runs, "
          f"{len(summary['flags'])} flags")
    for flag in summary["flags"]:
        print(f"- {flag['type']}: {flag['skill']} — {flag['detail']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
