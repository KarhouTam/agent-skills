"""End-to-end tests for the dsh half: session log -> collect_dsh.py -> records -> analyze.

The logs here are built the way dsh writes them: zstd-compressed JSONL, one frame
per append, at $DSH_HOME/sessions/<slug>/<session-id>/session.v3.jsonl.zstd. Every
frame below is its own `zstandard` compression of its own line, so a decoder that
read only the first frame would fail these tests rather than pass them.

`zstandard` is needed to *write* the fixtures; the collector never uses it — it
drives scripts/dsh_decode.mjs through Node, which dsh always ships.
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

import dsh_logs  # noqa: E402
import usage_lib  # noqa: E402

zstandard = pytest.importorskip("zstandard", reason="fixtures are zstd-compressed dsh logs")
if not dsh_logs.decode_available():  # pragma: no cover - depends on the machine
    pytest.skip("the dsh collector needs `node` to decode logs", allow_module_level=True)

SESSION = "11111111-2222-3333-4444-555555555555"
CHILD = "99999999-8888-7777-6666-555555555555"
COLLECT = SKILL_DIR / "hooks" / "collect_dsh.py"


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def now_ms():
    return int(time.time() * 1000)


@pytest.fixture
def repo(tmp_path):
    """A skills repo shaped like this one: two self-developed, one public, one nested."""
    root = tmp_path / "skills"
    write(root / ".gitignore", "/*\n!/.gitignore\n!/teach/\n!/diagram/\n")
    write(root / "skills.json", json.dumps(
        {"sources": [{"repo": "o/r", "ref": "x", "skills": {"handoff": {"path": "h"}}}]}))
    write(root / "teach" / "SKILL.md",
          "---\nname: teach\ndescription: Use when the user wants to learn how something works.\n---\n\n# Teach\n\n"
          "**REQUIRED SUB-SKILL:** `wiki`. What to ask lives there.\n")
    write(root / "teach" / "scripts" / "note.py", "print('note')\n")
    write(root / "wiki" / "SKILL.md",
          "---\nname: wiki\ndescription: Use when asking about repo internals.\n---\n\n# Wiki\n")
    write(root / "wiki" / "scripts" / "wiki.py", "print('wiki')\n")
    write(root / "diagram" / "SKILL.md",
          "---\nname: diagram\ndescription: Use when drawing diagrams.\n---\n\n# D\n")
    write(root / "handoff" / "SKILL.md",
          "---\nname: handoff\ndescription: Use to hand off a session.\n---\n\n# H\n")
    return root


class Log:
    """One dsh session log: one event per zstd frame, like the real writer."""

    def __init__(self, home, session, cwd, parent=None):
        self.path = Path(home) / "sessions" / "--slug--" / session / "session.v3.jsonl.zstd"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seq = self.turn = self.step_no = 0
        self.calls = []
        self.session = session
        header = {"type": "session", "version": 3, "id": session, "createdAt": now_ms(), "cwd": str(cwd),
                  "agentPreset": "cordis"}
        if parent:
            header.update(parentSession=parent, origin="subagent", delegationDepth=1)
        self.append(header)

    def append(self, event):
        event.setdefault("seq", self.seq)
        event.setdefault("time", now_ms() + self.seq)
        self.seq += 1
        with open(self.path, "ab") as handle:
            handle.write(zstandard.ZstdCompressor().compress((json.dumps(event) + "\n").encode()))
        return event

    # -- the events a real session has ------------------------------------------

    def prompt(self, text):
        self.turn += 1
        self.step_no = 0
        self.append({"type": "turn/start", "data": {"turn": self.turn}})
        self.append({"type": "user/message", "data": {
            "role": "user", "id": f"u{self.turn}", "source": {"kind": "user", "rpcId": "rpc"},
            "content": [{"type": "text", "text": text}]}})
        return self.turn

    def notice(self, kind, text):
        self.append({"type": "user/message", "data": {
            "role": "user", "id": f"s{self.seq}", "source": {"kind": kind, "form": "notice"},
            "content": [{"type": "text", "text": text}]}})

    def catalog(self, names):
        self.append({"type": "user/message", "data": {
            "role": "user", "id": f"c{self.seq}", "source": {
                "kind": "skill-catalog", "form": "catalog",
                "entries": [{"name": n, "description": f"Use when {n} is the right tool for this job."}
                            for n in names]},
            "content": [{"type": "text", "text": "<available_skills>…"}]}})

    def step(self, input_tokens, output_tokens, calls=(), cache_read=0):
        """One assistant message, its usage, and the tool calls it made. Returns the call ids."""
        self.step_no += 1
        self.append({"type": "step/start", "data": {"turn": self.turn, "step": self.step_no}})
        content, ids = [{"type": "text", "text": "…"}], []
        for name, arguments in calls:
            call_id = f"call_{self.seq}"
            ids.append(call_id)
            content.append({"type": "tool-call", "id": call_id, "name": name, "arguments": json.dumps(arguments)})
        self.append({"type": "assistant/message", "data": {
            "turn": self.turn, "step": self.step_no,
            "message": {"role": "assistant", "content": content, "model": "deepseek-flash"},
            "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens,
                      "cacheReadTokens": cache_read, "reasoningTokens": 0}}})
        for call_id, (name, arguments) in zip(ids, calls):
            self.append({"type": "tool/call", "data": {"turn": self.turn, "step": self.step_no,
                                                       "callId": call_id, "name": name,
                                                       "arguments": json.dumps(arguments)}})
        for call_id in ids:
            self.append({"type": "tool/result", "data": {
                "turn": self.turn, "step": self.step_no,
                "message": {"source": {"kind": "tool", "callId": call_id},
                            "content": [{"type": "tool-result", "toolCallId": call_id,
                                         "content": [{"type": "text", "text": "ok"}]}]}}})
        self.append({"type": "step/end", "data": {"turn": self.turn, "step": self.step_no}})
        return ids

    def fail(self, call_id, code, name="FsError"):
        self.append({"type": "tool/result", "data": {
            "turn": self.turn, "step": self.step_no, "error": {"name": name, "code": code},
            "message": {"source": {"kind": "tool", "callId": call_id},
                        "content": [{"type": "tool-result", "toolCallId": call_id, "isError": True,
                                     "content": [{"type": "text", "text": "Error: nope"}]}]}}})

    def end(self, reason="completed"):
        self.append({"type": "turn/end", "data": {"turn": self.turn, "reason": {"kind": reason}}})


def scenario(repo, home):
    """A turn whose skill loads mid-turn, then a second turn that inherits it."""
    log = Log(home, SESSION, repo)
    log.catalog(["teach", "wiki", "diagram", "handoff"])
    log.prompt("/teach how does dispatch work")
    log.step(1000, 50, [("skill", {"name": "teach"})])          # step 1 decides; not teach's cost
    log.step(1450, 30, [("bash", {"command": f"python3 {repo / 'teach/scripts/note.py'} run"})])
    log.step(1600, 40, [("skill", {"name": "wiki"}), ("read", {"file_path": str(repo / "wiki" / "SKILL.md")})])
    log.step(1800, 200, [("bash", {"command": f"python3 {repo / 'wiki/scripts/wiki.py'} ask"})])
    log.end()
    log.prompt("carry on")
    log.step(2500, 60)
    log.end()
    return log


def run_collect(repo, home, data, *extra):
    env = dict(os.environ, SKILL_USAGE_ROOT=str(repo), SKILL_USAGE_DATA_DIR=str(data), DSH_HOME=str(home))
    done = subprocess.run([sys.executable, str(COLLECT), "--json", *extra],
                          capture_output=True, text=True, env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def records(data):
    out = []
    for path in sorted((data / "sessions").glob("*.jsonl")):
        out += [json.loads(line) for line in path.read_text().splitlines()]
    return out


def analyze_repo(repo, data):
    import analyze

    now = time.time()
    return analyze.analyze(list(usage_lib.read_records(data)), usage_lib.Catalog(repo), now - 86400, now + 60)


def test_collect_decodes_every_frame_and_attributes_tokens(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    result = run_collect(repo, home, data)
    assert result["collected"] == 1 and result["records"] > 0

    summary = analyze_repo(repo, data)
    teach, wiki = summary["skills"]["teach"], summary["skills"]["wiki"]

    assert teach["runs"] == 1 and teach["via"] == {"user": 1}
    # The skill call and the SKILL.md read are one run: activations dedupe on tool_use_id.
    assert wiki["runs"] == 1 and wiki["via"] == {"model": 1}
    assert summary["skills"]["diagram"]["runs"] == 0
    assert summary["skills"]["handoff"]["tier"] == "public"
    reads = [r for r in records(data) if r["kind"] == "activation" and r["skill"] == "wiki"]
    assert sorted(r["via"] for r in reads) == ["model", "read"]

    # The step that decides to load a skill is not part of its cost, but every later step
    # of the turn is: teach is on the stack from step 2 on, so it is charged the whole turn,
    # and wiki — loaded during step 3 — only from step 4 on. Their spans overlap, so the
    # tokens of a turn exceed the tokens of any single run in it.
    turn1 = (1000 + 50) + (1450 + 30) + (1600 + 40) + (1800 + 200)
    assert teach["tokens_sum"] == turn1, "teach is on the stack for the whole turn"
    assert wiki["tokens_sum"] == (1800 + 200), "wiki is charged only from the step after it loaded"
    # Turn 2's tokens belong to no run: a skill's lifecycle ends with its turn even though it
    # is still in context afterwards. The turn totals still account for them.
    assert sum(r["tokens"] or 0 for r in summary["turns"]) == turn1 + (2500 + 60)
    # Load = the next step's context minus what the issuing step ended with (its input plus
    # its output). Teach is loaded by the step that read 1000 and wrote 50, so the step that
    # pays for it reads 1450: 1450 - (1000 + 50). Wiki is loaded by the step that read 1600
    # and wrote 40: 1800 - (1600 + 40).
    assert teach["load_median"] == 1450 - (1000 + 50)
    assert wiki["load_median"] == 1800 - (1600 + 40)

    edge = next(e for e in summary["edges"] if e["parent"] == "teach" and e["child"] == "wiki")
    assert (edge["parent_runs"], edge["same_turn"]) == (1, 1)
    assert "What to ask lives there." in edge["condition"]


def test_second_run_appends_nothing_and_grows_incrementally(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    log = scenario(repo, home)
    run_collect(repo, home, data)
    before = len(records(data))

    again = run_collect(repo, home, data)
    assert again["collected"] == 0 and again["records"] == 0, "a log with nothing new is skipped"
    assert len(records(data)) == before

    log.prompt("one more thing")
    log.step(3000, 70)
    log.end()
    grown = run_collect(repo, home, data)
    assert grown["collected"] == 1 and 0 < grown["records"] < before + 10, "only the new turn is emitted"
    assert len(records(data)) > before
    assert run_collect(repo, home, data)["records"] == 0, "and then it settles again"


def test_unreadable_log_is_skipped_not_fatal(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    broken = home / "sessions" / "--slug--" / "broken" / "session.v3.jsonl.zstd"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"not zstd at all")
    result = run_collect(repo, home, data)
    assert result["collected"] == 1 and result["records"] > 0
    assert any("broken" in message for message in result["errors"])


def test_status_reports_what_is_collectible(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    before = run_collect(repo, home, data, "--status")
    assert before["sessions_on_disk"] == 1 and before["uncollected"] == 1 and before["decoder"]
    run_collect(repo, home, data)
    after = run_collect(repo, home, data, "--status")
    assert after["uncollected"] == 0 and after["collected_files"] == 1


def test_subagent_tokens_join_the_parent_skill(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    parent = Log(home, SESSION, repo)
    parent.prompt("use teach, then delegate")
    parent.step(1000, 10, [("skill", {"name": "teach"})])
    spawn = parent.step(1100, 20, [("subagent", {"description": "look it up"})])[0]
    child = Log(home, CHILD, repo, parent=SESSION)
    child.prompt("look it up")
    child.step(400, 25)
    child.end()
    parent.step(1200, 30)
    parent.end()

    run_collect(repo, home, data)
    summary = analyze_repo(repo, data)
    teach = summary["skills"]["teach"]
    assert summary["totals"]["approx_subagents"] == 0, "the child log names the call that spawned it"
    assert teach["subagents"] == 1 and teach["subagent_share"] > 0
    assert teach["tokens_sum"] == (1100 + 20) + (1200 + 30) + (400 + 25)
    assert any(r["kind"] == "agent_link" and r["tool_use_id"] == spawn for r in records(data))


def test_goal_rounds_do_not_multiply_prompts(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    log = Log(home, SESSION, repo)
    log.prompt("only prompt")
    for _ in range(3):  # the goal tool restates the objective on every autonomous round
        log.notice("goal", '<goal_round>\nObjective: "teach me dispatch"\nRound: 1/256\n\nContinue working.')
    log.catalog(["teach", "wiki"])
    log.step(1000, 10)
    log.end()

    run_collect(repo, home, data)
    prompts = [r for r in records(data) if r["kind"] == "prompt"]
    assert [p["preview"] for p in prompts] == ["only prompt"]
    assert [r["skill"] for r in records(data) if r["kind"] == "activation"] == []


def test_goal_objective_fires_the_slash_gesture(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    log = Log(home, SESSION, repo)
    log.turn = 1
    log.append({"type": "turn/start", "data": {"turn": 1}})
    log.notice("goal", '<goal_round>\nObjective: "/teach dispatch"\nRound: 1/256')
    log.step(1000, 10)
    log.end()

    run_collect(repo, home, data)
    prompts = [r for r in records(data) if r["kind"] == "prompt"]
    assert len(prompts) == 1
    assert prompts[0]["preview"] == "/teach dispatch" and prompts[0]["slash"] == "teach"
    assert [r["skill"] for r in records(data) if r["kind"] == "activation"] == ["teach"]


def test_subagent_prompts_are_not_user_prompts(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    parent = Log(home, SESSION, repo)
    parent.prompt("/teach dispatch for the child")
    parent.step(1000, 10, [("subagent", {"description": "dispatch"})])
    # A child's first user-role message is the dispatcher's text, slash gesture and all.
    child = Log(home, CHILD, repo, parent=SESSION)
    child.prompt("/teach dispatch for the child")
    child.step(300, 15)
    child.end()
    parent.end()

    run_collect(repo, home, data)
    prompts = [r for r in records(data) if r["kind"] == "prompt"]
    assert len(prompts) == 1, "a dispatcher's text is not a second prompt"
    assert prompts[0]["session_id"] == SESSION
    assert [r["skill"] for r in records(data) if r["kind"] == "activation"] == ["teach"], \
        "and the same `/teach` is not counted twice"
    assert analyze_repo(repo, data)["skills"]["teach"]["runs"] == 1


def test_failed_tool_result_patches_the_resource_it_belongs_to(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    log = Log(home, SESSION, tmp_path / "project")  # a session outside the repo: use, not authoring
    log.prompt("read the diagram skill")
    read = log.step(900, 10, [("read", {"file_path": str(repo / "diagram" / "SKILL.md")})])[0]
    log.fail(read, "FS_NOT_FOUND")
    log.end("error")

    run_collect(repo, home, data)
    summary = analyze_repo(repo, data)
    entry = summary["files"]["diagram/SKILL.md"]
    assert (entry["read"], entry["fail"]) == (1, 1)
    assert "FS_NOT_FOUND" in entry["last_error"]
    assert [r["kind"] for r in records(data)].count("turn_failed") == 1


def test_catalog_emission_is_measured(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    log = scenario(repo, home)
    log.catalog(["teach", "wiki", "diagram", "handoff", "not-ours"])
    run_collect(repo, home, data)
    catalogs = [r for r in records(data) if r["kind"] == "catalog"]
    assert len(catalogs) == 2
    assert catalogs[1]["entries"] == 5, "every entry the harness sent is counted"
    assert set(catalogs[1]["skills"]) == {"teach", "wiki", "diagram", "handoff"}, "foreign skills are dropped"
    assert catalogs[1]["tokens"] > 0

    summary = analyze_repo(repo, data)
    assert summary["skills"]["teach"]["catalog_emissions"] == 2
    assert summary["skills"]["teach"]["catalog_tokens"] > 0
    assert "Catalog listings observed" in __import__("analyze").render(summary)


def test_authoring_session_reads_are_maintenance(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    log = Log(home, SESSION, repo)  # cwd is the repo itself: maintenance, not use
    log.prompt("let me reorganise the repo")
    log.step(1000, 10, [("read", {"file_path": str(repo / "diagram" / "SKILL.md")})])
    log.end()

    run_collect(repo, home, data)
    summary = analyze_repo(repo, data)
    assert summary["skills"]["diagram"]["runs"] == 0
    assert summary["authoring"]["reads"] == {"diagram": 1}


def test_collector_writes_only_under_the_data_dir(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    run_collect(repo, home, data)
    assert (data / "sessions").is_dir() and (data / "state" / "dsh.json").is_file()
    assert sorted(p.name for p in home.iterdir()) == ["sessions"]


# ---- degraded machines: no corpus, an unusable Node, nothing collected ----------------


def fake_node(tmp_path, exit_code, stdout=""):
    """A `node` that answers the zstd probe with `exit_code`, standing in for an old one."""
    binary = tmp_path / "bin" / "node"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(f"#!/bin/sh\necho '{stdout}'\nexit {exit_code}\n")
    binary.chmod(0o755)
    return str(binary.parent)


def collect_env(repo, data, home, node_dir):
    # `/bin` stays on PATH so the fake node's own `#!/bin/sh` can run.
    return dict(os.environ, SKILL_USAGE_ROOT=str(repo), SKILL_USAGE_DATA_DIR=str(data),
                DSH_HOME=str(home), PATH=f"{node_dir}:/bin")


def run(env, *args, **kwargs):
    done = subprocess.run([sys.executable, str(COLLECT), *args], capture_output=True, text=True, env=env,
                          timeout=kwargs.pop("timeout", 120))
    return done


def test_node_without_zstd_is_detected_once_not_per_log(repo, tmp_path):
    """The machine may have two Nodes; only one has zstd. Probe, do not assume."""
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    bindir = fake_node(tmp_path, exit_code=1)  # an old Node fails the probe
    status = run(collect_env(repo, data, home, bindir), "--status", "--json")
    report = json.loads(status.stdout)
    assert report["decoder"] is False and report["unreadable"] == []
    assert status.returncode == 1 and "Node 22.15+" in report["hint"]

    done = run(collect_env(repo, data, home, bindir), "--json")
    result = json.loads(done.stdout)
    assert result["collected"] == 0 and len(result["errors"]) == 1, "one actionable error, not one per log"
    assert "Node 22.15+" in result["errors"][0]


def test_a_node_that_answers_the_probe_is_used(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    bindir = fake_node(tmp_path, exit_code=0, stdout="not json")  # probe passes...
    done = run(collect_env(repo, data, home, bindir), "--json")
    result = json.loads(done.stdout)  # ...so it is chosen, and then fails to decode
    assert result["collected"] == 0 and result["errors"]
    assert "decoder" in result["errors"][0], "a Node that cannot decode must be reported"


def test_status_without_a_corpus_is_not_a_success(repo, tmp_path):
    """No logs means collection is off, whatever the decoder says."""
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    home.mkdir()
    done = run(collect_env(repo, data, home, str(Path(sys.executable).parent)), "--status")
    assert done.returncode == 1
    assert "session logs       0" in done.stdout


def test_collect_reports_an_empty_corpus_once(repo, tmp_path):
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    home.mkdir()
    result = run_collect(repo, home, data)
    assert result["collected"] == 0 and result["logs"] == 0
    assert len(result["errors"]) == 1 and "no session logs" in result["errors"][0]


def test_failed_collection_is_logged_for_the_next_insight(repo, tmp_path):
    """A failure the user does not see on the console still shows up in errors.log."""
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    env = collect_env(repo, data, home, fake_node(tmp_path, 1))
    done = run(env, "--json")
    assert json.loads(done.stdout)["errors"]
    assert "Node 22.15+" in (data / "errors.log").read_text()


def test_analysis_survives_an_unusable_decoder(repo, tmp_path):
    """A broken Node must not lose the records already collected: analyze says so and carries on."""
    home, data = tmp_path / "dsh-home", tmp_path / "data"
    scenario(repo, home)
    run_collect(repo, home, data)
    out = tmp_path / "insight.md"

    env = dict(os.environ, SKILL_USAGE_DATA_DIR=str(data), DSH_HOME=str(home), **{"PATH": f"{fake_node(tmp_path, 1)}:/bin"})
    analyzer = SKILL_DIR / "scripts" / "analyze.py"
    done = subprocess.run([sys.executable, str(analyzer), "--since", "1d", "--out", str(out)],
                          capture_output=True, text=True, env=env, timeout=300)
    assert done.returncode == 0, done.stderr
    assert "dsh collection failed" in done.stderr and "Node 22.15+" in done.stderr
    assert out.is_file(), "the insight is still written from what is already collected"
    assert "`teach`" in out.read_text()







