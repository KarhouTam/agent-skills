"""End-to-end tests: hook payloads -> collect.py -> records -> analyze.

Each test builds a throwaway skills repo and drives collect.py exactly as Claude
Code would: one subprocess per hook event, JSON on stdin, transcripts appended
as the conversation goes. Payload and transcript shapes follow the hooks
reference (code.claude.com/docs/en/hooks) and real Claude Code transcripts:
assistant messages split across lines that share `message.id` and `usage`.
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

COLLECT = SKILL_DIR / "hooks" / "collect.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "skills"
    write(root / ".gitignore", "/*\n!/.gitignore\n!/teach/\n!/wiki/\n!/diagram/\n")
    write(root / "skills.json", json.dumps({"sources": [{"repo": "o/r", "ref": "x", "skills": {"handoff": {"path": "h"}}}]}))
    write(root / "teach" / "SKILL.md",
          "---\nname: teach\ndescription: Use when the user wants to learn how something works.\n"
          "disable-model-invocation: true\n---\n\n# Teach\n\n## Step 1\n\nWhen the question sits in a public repo, map it first.\n\n"
          "**REQUIRED SUB-SKILL:** `wiki`. What to query lives there.\n")
    write(root / "teach" / "scripts" / "note.py", "print('note')\n")
    write(root / "wiki" / "SKILL.md", "---\nname: wiki\ndescription: Use when asking about repo internals.\n---\n\n# Wiki\n")
    write(root / "wiki" / "scripts" / "wiki.py", "print('wiki')\n")
    write(root / "diagram" / "SKILL.md", "---\nname: diagram\ndescription: Use when drawing diagrams of any kind.\n---\n\n# D\n")
    return root


class Session:
    """One Claude Code session: emits hook events and grows its transcript."""

    def __init__(self, root, data, sid, cwd, prompt_ids=True):
        self.root, self.data, self.sid, self.cwd = root, data, sid, cwd
        self.transcript = data.parent / "transcripts" / f"{sid}.jsonl"
        self.transcript.parent.mkdir(parents=True, exist_ok=True)
        self.transcript.touch()
        self.prompt_ids, self.prompt, self.n = prompt_ids, None, 0
        self.env = dict(os.environ, SKILL_USAGE_ROOT=str(root), SKILL_USAGE_DATA_DIR=str(data),
                        DSH_HOME=str(data.parent / "dsh-home"))  # keep dsh collection out of a test run
        self.env.pop("SKILL_USAGE_DISABLE", None)

    def hook(self, event, **fields):
        payload = {"session_id": self.sid, "transcript_path": str(self.transcript), "cwd": str(self.cwd),
                   "hook_event_name": event, **fields}
        if self.prompt and "prompt_id" not in fields:
            payload["prompt_id"] = self.prompt
        done = subprocess.run([sys.executable, str(COLLECT)], input=json.dumps(payload), text=True,
                              capture_output=True, env=self.env, timeout=30)
        assert done.returncode == 0 and not done.stdout, done.stderr

    def line(self, entry, path=None):
        now = time.time()
        entry.setdefault("timestamp", time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z")
        entry.setdefault("sessionId", self.sid)
        with open(path or self.transcript, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")

    def user(self, text, **extra):
        # Real prompts are seconds apart; legacy transcripts (no promptId) are matched to hook prompts by time.
        time.sleep(0.25)
        self.n += 1
        self.prompt = f"P{self.n}"
        self.hook("UserPromptSubmit", prompt=text)
        if text.startswith("/"):
            name, _, args = text[1:].partition(" ")
            self.hook("UserPromptExpansion", expansion_type="slash_command", command_name=name,
                      command_args=args, command_source="userSettings", prompt=text)
            text = f"<command-message>{name}</command-message>\n<command-name>/{name}</command-name>\n<command-args>{args}</command-args>"
        entry = {"type": "user", "uuid": f"u{self.n}", "message": {"role": "user", "content": text}, **extra}
        if self.prompt_ids:
            entry["promptId"] = self.prompt
        self.line(entry)

    def meta(self, text):
        entry = {"type": "user", "isMeta": True, "uuid": f"m{time.time()}",
                 "message": {"role": "user", "content": [{"type": "text", "text": text}]}}
        if self.prompt_ids:
            entry["promptId"] = self.prompt
        self.line(entry)

    def assistant(self, mid, context, output, tools=(), path=None, sidechain=False):
        """One API call. `tools` are (id, name, input); each block gets its own line, like Claude Code writes them."""
        usage = {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": context - 10,
                 "output_tokens": output}
        blocks = [{"type": "text", "text": "…"}] + [{"type": "tool_use", "id": i, "name": n, "input": inp} for i, n, inp in tools]
        for block in blocks:
            self.line({"type": "assistant", "uuid": f"{mid}-{block.get('id', 't')}", "isSidechain": sidechain,
                       "message": {"id": mid, "model": "claude-x", "role": "assistant", "content": [block], "usage": usage}},
                      path=path)

    def tool_result(self, tool_use_id, text="ok"):
        entry = {"type": "user", "uuid": f"r{tool_use_id}",
                 "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tool_use_id, "content": text}]}}
        if self.prompt_ids:
            entry["promptId"] = self.prompt
        self.line(entry)


def records(data):
    out = []
    for path in sorted((data / "sessions").glob("*.jsonl")):
        out += [json.loads(line) for line in path.read_text().splitlines()]
    return out


def run_scenario(repo, data, prompt_ids=True):
    """Two turns of /teach (one interrupted), a plain turn, and an authoring session."""
    s = Session(repo, data, "S1", repo.parent / "project", prompt_ids=prompt_ids)
    s.cwd.mkdir(exist_ok=True)
    s.hook("SessionStart", source="startup", model="claude-x")

    # Turn 1: /teach -> model loads wiki -> runs wiki.py -> spawns a subagent.
    s.user("/teach ELI5 how does dispatch work in pytorch")
    s.meta(f"Base directory for this skill: {repo / 'teach'}\n\n# Teach")
    s.assistant("m1", 1000, 50, tools=[("tu1", "Skill", {"skill": "wiki"})])
    s.hook("PreToolUse", tool_name="Skill", tool_input={"skill": "wiki"}, tool_use_id="tu1")
    s.hook("PostToolUse", tool_name="Skill", tool_input={"skill": "wiki"}, tool_use_id="tu1", tool_response={})
    s.tool_result("tu1", "Launching skill: wiki")
    s.meta(f"Base directory for this skill: {repo / 'wiki'}\n\n# Wiki")
    s.assistant("m2", 1450, 30, tools=[("tu2", "Bash", {"command": f"python3 {repo / 'wiki/scripts/wiki.py'} ask o/r q"})])
    s.hook("PostToolUse", tool_name="Bash", tool_input={"command": f"python3 {repo / 'wiki/scripts/wiki.py'} ask o/r q"},
           tool_use_id="tu2", tool_response={}, duration_ms=900)
    s.tool_result("tu2")
    s.assistant("m3", 1600, 40, tools=[("tu3", "Agent", {"subagent_type": "Explore", "prompt": "find it"})])
    s.hook("SubagentStart", agent_id="agent-a1", agent_type="Explore")
    sub = data.parent / "transcripts" / "S1" / "subagents" / "agent-a1.jsonl"
    sub.parent.mkdir(parents=True, exist_ok=True)
    s.line({"type": "user", "isSidechain": True, "uuid": "su1", "message": {"role": "user", "content": "find it"}}, path=sub)
    s.assistant("sa1", 500, 20, path=sub, sidechain=True)
    s.assistant("sa2", 700, 25, path=sub, sidechain=True)
    s.hook("SubagentStop", agent_id="agent-a1", agent_type="Explore", agent_transcript_path=str(sub),
           stop_hook_active=False, last_assistant_message="found")
    s.hook("PostToolUse", tool_name="Agent", tool_input={"subagent_type": "Explore"}, tool_use_id="tu3",
           tool_response={"status": "completed", "agentId": "a1", "totalToolUseCount": 2})
    s.tool_result("tu3", "found")
    s.assistant("m4", 1800, 200)
    s.hook("Stop", stop_hook_active=False, last_assistant_message="done")

    # Turn 2: no skill.
    s.user("fix the flaky test in ci")
    s.assistant("m5", 2100, 80)
    s.hook("Stop", stop_hook_active=False, last_assistant_message="done")

    # Turn 3: /teach again, interrupted by the user (no Stop), wiki never loaded.
    s.user("/teach why is the scheduler designed this way")
    s.meta(f"Base directory for this skill: {repo / 'teach'}\n\n# Teach")
    s.assistant("m6", 2500, 60)

    # Turn 4: a follow-up; SessionEnd flushes the tail.
    s.user("ok thanks")
    s.assistant("m7", 2600, 10)
    s.hook("Stop", stop_hook_active=False, last_assistant_message="bye")
    s.hook("SessionEnd", reason="prompt_input_exit")

    # An authoring session inside the repo: reading SKILL.md there is maintenance, not use.
    a = Session(repo, data, "S2", repo / "diagram")
    a.hook("SessionStart", source="startup")
    a.user("tighten the diagram description")
    a.hook("PostToolUse", tool_name="Read", tool_input={"file_path": str(repo / "diagram" / "SKILL.md")},
           tool_use_id="tr1", tool_response={})
    a.hook("PostToolUse", tool_name="Edit", tool_input={"file_path": str(repo / "diagram" / "SKILL.md")},
           tool_use_id="te1", tool_response={})
    return s


def analyze_repo(repo, data, until_offset=60.0):
    import analyze
    import usage_lib

    catalog = usage_lib.Catalog(repo)
    now = time.time()
    return analyze.analyze(list(usage_lib.read_records(data)), catalog, now - 86400, now + until_offset)


@pytest.mark.parametrize("prompt_ids", [True, False], ids=["promptId", "legacy-uuid"])
def test_runs_tokens_and_sub_skills(repo, tmp_path, prompt_ids):
    data = tmp_path / "data"
    run_scenario(repo, data, prompt_ids=prompt_ids)
    summary = analyze_repo(repo, data)
    teach, wiki, diagram = (summary["skills"][k] for k in ("teach", "wiki", "diagram"))

    assert teach["runs"] == 2 and teach["via"] == {"user": 2}
    assert teach["outcomes"] == {"completed": 1, "interrupted": 1}
    assert wiki["runs"] == 1 and wiki["via"] == {"model": 1}
    assert diagram["runs"] == 0, "a SKILL.md read in an authoring session is not a run"
    assert summary["authoring"]["reads"] == {"diagram": 1}

    # Turn 1 inclusive tokens for teach: m1..m4 plus the subagent. wiki starts after m1, the call that chose it.
    turn1 = (1000 + 50) + (1450 + 30) + (1600 + 40) + (1800 + 200)
    subagent = (500 + 20) + (700 + 25)
    turn3 = 2500 + 60
    assert teach["tokens_sum"] == turn1 + subagent + turn3
    assert wiki["tokens_sum"] == turn1 - 1050 + subagent
    assert wiki["subagents"] == 1 and teach["subagents"] == 1
    assert summary["totals"]["approx_subagents"] == 0
    # Load = context growth across the activation: 1450 - (1000 + 50).
    assert wiki["load_median"] == 400

    edge = next(e for e in summary["edges"] if e["parent"] == "teach" and e["child"] == "wiki")
    assert (edge["parent_runs"], edge["same_turn"]) == (2, 1)
    assert "why is the scheduler" in edge["misses"][0]["prompt"]
    assert "public repo" in edge["condition"]

    assert summary["files"]["wiki/scripts/wiki.py"]["exec"] == 1
    assert wiki["untouched"] == []
    turn_rows = {r["prompt"]: r for r in summary["turns"]}
    assert turn_rows["fix the flaky test in ci"]["skills"] == []
    assert turn_rows["fix the flaky test in ci"]["tokens"] == 2100 + 80


def test_flush_is_incremental_and_idempotent(repo, tmp_path):
    data = tmp_path / "data"
    s = run_scenario(repo, data)
    before = [r for r in records(data) if r["kind"] == "usage"]
    s.hook("Stop", stop_hook_active=False)
    s.hook("SessionEnd", reason="other")
    after = [r for r in records(data) if r["kind"] == "usage"]
    assert sum(r["calls"] for r in after) == sum(r["calls"] for r in before) == 7 + 2


def test_transcript_lag_is_picked_up_by_the_next_flush(repo, tmp_path):
    data = tmp_path / "data"
    s = Session(repo, data, "S9", tmp_path)
    s.user("/teach how does paging work")
    with open(s.transcript, "a") as handle:  # half-written line: the transcript lags the hook
        handle.write('{"type": "assistant", "message": {"id": "m1", "usage": {"output_tokens": 5')
    s.hook("Stop")
    assert not [r for r in records(data) if r["kind"] == "usage"]
    with open(s.transcript, "a") as handle:
        handle.write(', "input_tokens": 100}}}\n')
    s.hook("SessionEnd", reason="other")
    usage = [r for r in records(data) if r["kind"] == "usage"]
    assert [(u["skills"], u["output"]) for u in usage] == [(["teach"], 5)]


def test_flags_need_enough_coverage(repo, tmp_path):
    data = tmp_path / "data"
    run_scenario(repo, data)
    thin = analyze_repo(repo, data)
    assert not [f for f in thin["flags"] if f["type"] == "retire?"]
    thick = analyze_repo(repo, data, until_offset=20 * 86400)
    retire = {f["skill"] for f in thick["flags"] if f["type"] == "retire?"}
    assert retire == {"diagram"}


def test_failed_model_invocation_is_reported(repo, tmp_path):
    data = tmp_path / "data"
    s = Session(repo, data, "S3", tmp_path)
    s.user("explain how autograd works")
    s.hook("PreToolUse", tool_name="Skill", tool_input={"skill": "teach"}, tool_use_id="tx")
    s.hook("PostToolUseFailure", tool_name="Skill", tool_input={"skill": "teach"}, tool_use_id="tx",
           error="Skill teach cannot be used with Skill tool due to disable-model-invocation")
    summary = analyze_repo(repo, data)
    assert summary["skills"]["teach"]["runs"] == 0
    assert summary["skills"]["teach"]["failed_attempts"] == 1
    assert [f["type"] for f in summary["flags"] if f["skill"] == "teach"] == ["model-blocked"]


def test_collector_never_fails(repo, tmp_path):
    env = dict(os.environ, SKILL_USAGE_ROOT=str(repo), SKILL_USAGE_DATA_DIR=str(tmp_path / "data"))
    for stdin in ["not json", json.dumps({"hook_event_name": "Stop", "session_id": "x", "transcript_path": "/nope"}),
                  json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": {"command": "echo 'unterminated"}})]:
        done = subprocess.run([sys.executable, str(COLLECT)], input=stdin, text=True, capture_output=True, env=env)
        assert done.returncode == 0 and not done.stdout


def test_render_has_every_section(repo, tmp_path):
    import analyze

    data = tmp_path / "data"
    run_scenario(repo, data)
    markdown = analyze.render(analyze_repo(repo, data))
    for heading in ["# Skill usage insight", "## At a glance", "## Skills", "## Sub-skills", "## Scripts and files",
                    "## Prompts → skills", "## Prompts that ran no skill", "## Decisions", "## Method"]:
        assert heading in markdown
    assert "| `teach` | self | 2 |" in markdown
    assert "Thin evidence" in markdown
    assert "fix the flaky test in ci" in markdown


def test_cli_writes_insight_and_keeps_decisions(repo, tmp_path):
    data = tmp_path / "data"
    s = run_scenario(repo, data)
    out = tmp_path / "insight.md"
    command = [sys.executable, str(SKILL_DIR / "scripts" / "analyze.py"), "--since", "1d", "--out", str(out)]
    done = subprocess.run(command, env=s.env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert f"insight: {out}" in done.stdout
    assert list((data / "snapshots").glob("*.json"))

    decided = out.read_text().replace(
        "<!-- Filled in by the agent following SKILL.md: one line per skill that needs a decision -->",
        "- **Keep** `teach` — 2 runs.")
    out.write_text(decided)
    assert subprocess.run(command, env=s.env, capture_output=True, timeout=60).returncode == 0
    assert "- **Keep** `teach` — 2 runs." in out.read_text()


def test_cli_without_data_says_how_to_start(tmp_path):
    env = dict(os.environ, SKILL_USAGE_DATA_DIR=str(tmp_path / "missing"), XDG_DATA_HOME=str(tmp_path / "xdg"))
    done = subprocess.run([sys.executable, str(SKILL_DIR / "scripts" / "analyze.py"), "--stdout"], env=env,
                          capture_output=True, text=True, timeout=60)
    # <skill>/data may exist on a machine that already collects; only assert the empty case when it does not.
    if not (SKILL_DIR / "data").is_dir():
        assert done.returncode == 1 and "install_hooks.py" in done.stderr


def test_forked_session_skips_copied_history(repo, tmp_path):
    data = tmp_path / "data"
    s = Session(repo, data, "F1", tmp_path)
    s.line({"type": "user", "uuid": "old", "promptId": "OLD", "timestamp": "2020-01-01T00:00:00.000Z",
            "message": {"role": "user", "content": "copied from the parent session"}})
    s.line({"type": "assistant", "uuid": "olda", "timestamp": "2020-01-01T00:00:01.000Z",
            "message": {"id": "mold", "content": [], "usage": {"input_tokens": 99999, "output_tokens": 1}}})
    s.hook("SessionStart", source="fork")
    s.user("continue here")
    s.assistant("mnew", 100, 5)
    s.hook("Stop")
    usage = [r for r in records(data) if r["kind"] == "usage"]
    assert [(u["prompt_id"], u["input"] + u["cache_read"], u["output"]) for u in usage] == [("P1", 100, 5)]
