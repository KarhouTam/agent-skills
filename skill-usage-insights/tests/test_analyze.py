"""What `analyze.py` reports when a record set is ambiguous.

Three things the insight learned to distrust: an unknown tool result reported as a
failure, a whole-catalog token count charged to every skill in it, and runs from
authoring sessions driving the cost comparison.
"""

import json
import sys
import time
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import analyze  # noqa: E402
import usage_lib  # noqa: E402


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "skills"
    write(root / ".gitignore", "/*\n!/.gitignore\n!/teach/\n")
    write(root / "skills.json", json.dumps({"sources": []}))
    write(root / "teach" / "SKILL.md", "---\nname: teach\ndescription: Use when teaching.\n---\n\n# Teach\n")
    write(root / "teach" / "scripts" / "note.py", "print('note')\n")
    return root


def summarize(repo, records):
    now = time.time()
    return analyze.analyze(records, usage_lib.Catalog(repo), now - 3600, now + 60)


def session(authoring, cwd, ts):
    return {"kind": "session_start", "session_id": "s1", "ts": ts, "project": "p",
            "authoring": authoring, "cwd": cwd}


def usage(turn, tokens, ts):
    return {"kind": "usage", "session_id": "s1", "scope": "main", "prompt_id": turn,
            "skills": ["teach"], "calls": 1, "input": tokens, "cache_creation": 0,
            "cache_read": 0, "output": 0, "ts": ts}


def activation(turn, ts):
    return {"kind": "activation", "session_id": "s1", "skill": "teach", "via": "user", "prompt_id": turn, "ts": ts}


def iso(offset):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() + offset)) + ".000Z"


def test_unknown_tool_result_is_not_a_failure(repo):
    records = [
        session(False, "/tmp/project", iso(-600)),
        {"kind": "resource", "session_id": "s1", "skill": "teach", "file": "scripts/note.py",
         "op": "exec", "ok": None, "ts": iso(-500)},
    ]
    entry = summarize(repo, records)["files"]["teach/scripts/note.py"]
    assert entry["exec"] == 1 and entry["fail"] == 0, "an unreported result is unknown, not failed"


def test_listing_cost_is_the_skills_share_of_the_catalog(repo):
    records = [
        session(False, "/tmp/project", iso(-600)),
        activation("t1", iso(-500)),
        {"kind": "catalog", "session_id": "s1", "entries": 20, "tokens": 2000, "skills": ["teach"], "ts": iso(-500)},
        usage("t1", 100, iso(-400)),
    ]
    skill = summarize(repo, records)["skills"]["teach"]
    assert skill["catalog_emissions"] == 1
    assert skill["catalog_tokens"] == 100, "2000 tokens spread over 20 entries, not 2000 for one skill"


def test_authoring_runs_do_not_drive_the_cost_flag(repo):
    records = [session(True, str(repo), iso(-900))]
    for index in range(3):
        turn = f"t{index}"
        records += [activation(turn, iso(-800 + index * 10)), usage(turn, 5_000_000, iso(-790 + index * 10))]
    summary = summarize(repo, records)
    skill = summary["skills"]["teach"]
    assert skill["runs"] == 3 and skill["runs_authoring"] == 3
    assert skill["tokens_median_real"] is None
    assert [f for f in analyze.flags(summary) if f["type"] == "costly" and f["skill"] == "teach"] == []
