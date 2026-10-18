"""End-to-end tests for the Codex half: rollout log -> collect_codex.py -> records.

Codex hooks carry no token counts, so the collector takes prompts and file use from
the hook payload and activations, usage and turn outcomes from the rollout log it
writes anyway. The rollout shapes here follow a real one: `event_msg`/`token_count`
carries `last_token_usage` per API call, and a function call is followed by the
token count of the call that issued it.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(SKILL_DIR / "hooks"))

import codex_transcript  # noqa: E402
import install_hooks  # noqa: E402
import usage_lib  # noqa: E402

COLLECT = SKILL_DIR / "hooks" / "collect_codex.py"
SESSION = "sess-codex-1"
TURN = "turn-1"


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


def rollout(tmp_path, repo, calls=1):
    """One turn: a call that reads the skill, then a second call under its stack."""
    lines = [
        {"timestamp": "2026-10-18T10:00:00.000Z", "ordinal": 0, "type": "session_meta",
         "payload": {"session_id": SESSION, "cwd": "/tmp/project"}},
        {"timestamp": "2026-10-18T10:00:00.100Z", "ordinal": 1, "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": TURN}},
        {"timestamp": "2026-10-18T10:00:00.200Z", "ordinal": 2, "type": "turn_context",
         "payload": {"turn_id": TURN, "cwd": "/tmp/project", "model": "gpt-5-codex"}},
        {"timestamp": "2026-10-18T10:00:00.300Z", "ordinal": 3, "type": "event_msg",
         "payload": {"type": "item_completed", "turn_id": TURN,
                     "item": {"type": "UserMessage", "content": [{"type": "text", "text": "teach me"}]}}},
        {"timestamp": "2026-10-18T10:00:01.000Z", "ordinal": 4, "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command", "call_id": "call_a",
                     "arguments": json.dumps({"cmd": f"sed -n 1,40p {repo / 'teach' / 'SKILL.md'}"})}},
        {"timestamp": "2026-10-18T10:00:01.500Z", "ordinal": 5, "type": "event_msg",
         "payload": {"type": "token_count", "info": {"last_token_usage": {
             "input_tokens": 1000, "cached_input_tokens": 0, "output_tokens": 50}}}},
        {"timestamp": "2026-10-18T10:00:02.000Z", "ordinal": 6, "type": "response_item",
         "payload": {"type": "function_call", "name": "exec_command", "call_id": "call_b",
                     "arguments": json.dumps({"cmd": f"python3 {repo / 'teach' / 'scripts' / 'note.py'}"})}},
        {"timestamp": "2026-10-18T10:00:02.500Z", "ordinal": 7, "type": "event_msg",
         "payload": {"type": "token_count", "info": {"last_token_usage": {
             "input_tokens": 3000, "cached_input_tokens": 2000, "output_tokens": 100}}}},
    ]
    for _ in range(calls - 1):
        lines.append({"timestamp": "2026-10-18T10:00:03.000Z", "ordinal": len(lines), "type": "event_msg",
                      "payload": {"type": "token_count", "info": {"last_token_usage": {
                          "input_tokens": 4000, "cached_input_tokens": 3000, "output_tokens": 20}}}})
    lines.append({"timestamp": "2026-10-18T10:00:04.000Z", "ordinal": len(lines), "type": "event_msg",
                  "payload": {"type": "task_complete", "turn_id": TURN}})
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    return path


def records(data):
    out = []
    for path in sorted((data / "sessions").glob("*.jsonl")):
        out += [json.loads(line) for line in path.read_text().splitlines()]
    return out


def hook(data, repo, event, **fields):
    payload = dict({"hook_event_name": event, "session_id": SESSION, "turn_id": TURN,
                    "cwd": "/tmp/project"}, **fields)
    env = dict(os.environ, SKILL_USAGE_ROOT=str(repo), SKILL_USAGE_DATA_DIR=str(data))
    done = subprocess.run([sys.executable, str(COLLECT)], input=json.dumps(payload),
                          capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0, done.stderr
    assert done.stdout == "", "Codex turns a hook's stdout into model context"
    return done


def test_usage_is_charged_to_the_stack_before_the_call_that_loads_the_skill(repo, tmp_path):
    path = rollout(tmp_path, repo)
    parsed, cursor = codex_transcript.parse_transcript(
        str(path), codex_transcript.new_cursor(), usage_lib.Catalog(repo))
    usage = [r for r in parsed if r["kind"] == "usage"]

    assert [r["kind"] for r in parsed].count("t_activation") == 1
    assert usage[0]["skills"] == [], "the call that decided to load the skill does not pay for it"
    assert (usage[0]["input"], usage[0]["cache_read"], usage[0]["output"]) == (1000, 0, 50)
    assert usage[1]["skills"] == ["teach"]
    assert (usage[1]["input"], usage[1]["cache_read"]) == (1000, 2000), "cached input is split out of input"

    load = [r for r in parsed if r["kind"] == "load"][0]
    assert load["skill"] == "teach" and load["tokens"] == 1950, "3000 context - (1000 input + 50 output)"
    assert load["clean"] is True

    again, _ = codex_transcript.parse_transcript(str(path), cursor, usage_lib.Catalog(repo))
    assert again == [], "the cursor makes a second flush a no-op"


def test_catalog_listing_resolves_the_short_skill_roots(repo, tmp_path):
    lines = [
        {"timestamp": "2026-10-18T10:00:00.000Z", "ordinal": 0, "type": "event_msg",
         "payload": {"type": "task_started", "turn_id": TURN}},
        {"timestamp": "2026-10-18T10:00:00.500Z", "ordinal": 1, "type": "response_item",
         "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": (
             "<skills_instructions>\n## Skills\n### Skill roots\n"
             f"- `r0` = `{repo}`\n- `r1` = `/elsewhere/skills`\n### Available skills\n"
             "- teach: Teach a topic. (file: r0/teach/SKILL.md)\n"
             "- foreign: Someone else's. (file: r1/foreign/SKILL.md)\n"
             "</skills_instructions>\n")}]}},
    ]
    path = tmp_path / "rollout.jsonl"
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    parsed, _ = codex_transcript.parse_transcript(
        str(path), codex_transcript.new_cursor(), usage_lib.Catalog(repo))
    catalog = [r for r in parsed if r["kind"] == "catalog"][0]
    assert catalog["entries"] == 2
    assert catalog["skills"] == ["teach"], "only this repo's skills are collected"
    assert catalog["tokens"] > 0


def test_hooks_record_the_prompt_a_mention_and_a_failing_script(repo, tmp_path):
    data, transcript = tmp_path / "data", rollout(tmp_path, repo)
    hook(data, repo, "SessionStart", source="startup", model="gpt-5-codex", transcript_path=str(transcript))
    hook(data, repo, "UserPromptSubmit", prompt="$teach explain dispatch")
    hook(data, repo, "PostToolUse", tool_name="Bash", tool_use_id="call_b",
         tool_input={"cmd": f"python3 {repo / 'teach' / 'scripts' / 'note.py'}"},
         tool_response={"output": "Process exited with code 1"})
    hook(data, repo, "Stop", transcript_path=str(transcript))

    got = records(data)
    start = [r for r in got if r["kind"] == "session_start"][0]
    assert (start["project"], start["authoring"], start["model"]) == ("project", False, "gpt-5-codex")
    prompt = [r for r in got if r["kind"] == "prompt"][0]
    assert prompt["prompt_id"] == TURN and prompt["preview"].startswith("$teach")
    mention = [r for r in got if r["kind"] == "activation"][0]
    assert (mention["skill"], mention["via"]) == ("teach", "user")

    resource = [r for r in got if r["kind"] == "resource"][0]
    assert (resource["skill"], resource["file"], resource["op"], resource["ok"]) == (
        "teach", "scripts/note.py", "exec", False)

    assert {r["kind"] for r in got} >= {"usage", "t_activation", "turn_end"}
    assert not (data / "errors.log").exists()


def test_collector_never_fails_or_prints_on_garbage(repo, tmp_path):
    env = dict(os.environ, SKILL_USAGE_ROOT=str(repo), SKILL_USAGE_DATA_DIR=str(tmp_path / "data"))
    done = subprocess.run([sys.executable, str(COLLECT)], input="{ not json",
                          capture_output=True, text=True, env=env, timeout=60)
    assert done.returncode == 0 and done.stdout == ""


def test_codex_install_keeps_foreign_hooks_and_uninstalls(tmp_path, monkeypatch, capsys):
    home = tmp_path / "codex"
    home.mkdir()
    path = home / "hooks.json"
    foreign = {"type": "command", "command": "~/bin/mine.sh"}
    path.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [foreign]}]}}))
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "no-claude"))
    monkeypatch.setenv("DSH_HOME", str(tmp_path / "no-dsh"))

    assert install_hooks.main([]) == 0
    first = json.loads(path.read_text())
    assert first["hooks"]["Stop"][0]["hooks"] == [foreign]
    assert str(install_hooks.CODEX_COLLECT) in first["hooks"]["Stop"][1]["hooks"][0]["command"]
    assert "trust" in capsys.readouterr().out

    assert install_hooks.main([]) == 0, "a second install is idempotent"
    assert json.loads(path.read_text()) == first
    assert install_hooks.main(["--status"]) == 0

    assert install_hooks.main(["--uninstall"]) == 0
    assert json.loads(path.read_text()) == {"hooks": {"Stop": [{"hooks": [foreign]}]}}
    assert install_hooks.main(["--status"]) == 1
