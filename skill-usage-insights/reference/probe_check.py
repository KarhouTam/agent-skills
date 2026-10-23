"""Check probe outcomes: which superpowers skill fired in each probe child.

Usage: python3 reference/probe_check.py <probe_start_ts> <session_id>=<skill> [...]
Probe children are the subagent sessions created after <probe_start_ts>; their
activations are joined from the collected records.
"""
import json
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(SKILL_DIR / "hooks"))
import usage_lib  # noqa: E402

start, *pairs = sys.argv[1:]
expected = dict(pair.split("=", 1) for pair in pairs)

data = SKILL_DIR / "data"
rows = list(usage_lib.read_records(data, 0.0))

# child sessions born after the probes started, subagents of this session
starts = {r.get("session_id"): r for r in rows if r.get("kind") == "session_start"}
children = {sid: r for sid, r in starts.items()
            if (r.get("ts") or "") >= start and r.get("origin") == "subagent"}

acts = {}
for row in rows:
    if row.get("kind") == "activation" and row.get("session_id") in children:
        acts.setdefault(row["session_id"], set()).add(row.get("skill"))

lines = []
for sid, skill in expected.items():
    fired = sorted(acts.get(sid, set()))
    ok = skill in acts.get(sid, set())
    lines.append(f"{'FIRE ' if ok else 'MISS '} {skill:36} child={sid[:8]} fires={fired}")
print("\n".join(lines))
unmatched = [sid for sid in children if sid not in expected]
if unmatched:
    print("probe children not in expectation:", unmatched)
sys.exit(0 if all(s in acts.get(c, set()) for c, s in expected.items()) else 1)
