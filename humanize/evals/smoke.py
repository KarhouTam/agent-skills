#!/usr/bin/env python3
"""Deterministic checks for run_role.py and the skill's file layout.

Offline (the default) exercises dry-run argv, config resolution, resume wiring, and the
brief/ledger/prompt-file contract without any model call. `--live` additionally runs each
harness for real — one tiny call per check — and asserts a sentinel token round-trips through
round 1 and resume.

    python3 evals/smoke.py            # offline, seconds
    python3 evals/smoke.py --live     # + ~10 tiny model calls
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
RUNNER = SKILL_DIR / "scripts" / "run_role.py"
SENTINEL = "AR_SMOKE"

FAILURES: list[str] = []
CHECKS = 0


def check(name: str, fn) -> None:
    global CHECKS
    CHECKS += 1
    try:
        fn()
    except AssertionError as exc:
        FAILURES.append(f"{name}: {exc}")
        print(f"FAIL  {name}: {exc}")
    except Exception as exc:  # noqa: BLE001 - a crashed check is a failure too
        FAILURES.append(f"{name}: {type(exc).__name__}: {exc}")
        print(f"FAIL  {name}: {type(exc).__name__}: {exc}")
    else:
        print(f"ok    {name}")


def invoke(runner_args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), *runner_args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def write_config(root: Path, payload: dict | str) -> Path:
    path = root / f"cfg-{time.monotonic_ns()}.json"
    text = payload if isinstance(payload, str) else json.dumps(payload)
    path.write_text(text, encoding="utf-8")
    return path


def dry(root: Path, config: Path, role: str, round_no: int, *extra: str) -> dict:
    prompt = root / "prompt.md"
    if not prompt.exists():
        prompt.write_text("Reply with OK\n", encoding="utf-8")
    result = invoke(
        [
            "--config",
            str(config),
            "run",
            "--role",
            role,
            "--round",
            str(round_no),
            "--prompt",
            str(prompt),
            "--out",
            str(root / "out.md"),
            "--run-dir",
            str(root / "run"),
            "--dry-run",
            *extra,
        ]
    )
    assert result.returncode == 0, f"dry-run exited {result.returncode}: {result.stderr.strip()}"
    return json.loads(result.stdout)


def seed_session(
    root: Path, role: str, harness: str, sid: str, cwd: str | None = None
) -> None:
    run_dir = root / "run"
    run_dir.mkdir(exist_ok=True)
    entry = {"harness": harness, "session_id": sid}
    if cwd is not None:
        entry["cwd"] = cwd
    (run_dir / "sessions.json").write_text(json.dumps({role: entry}), encoding="utf-8")


def offline(root: Path) -> None:
    shipped = SKILL_DIR / "harness.json"
    codex = write_config(root, {"challenger": {"harness": "codex"}})
    claude = write_config(root, {"challenger": {"harness": "claude-code"}})
    opencode = write_config(root, {"challenger": {"harness": "opencode"}})
    dsh = write_config(root, {"challenger": {"harness": "dsh"}})

    def preflight() -> None:
        shipped_config = json.loads(shipped.read_text(encoding="utf-8"))
        expected = shipped_config.get("challenger", {}).get("harness", "native")
        result = invoke(["--config", str(shipped), "preflight"])
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        assert "implementer" in result.stdout and "challenger" in result.stdout, result.stdout
        challenger_line = next(line for line in result.stdout.splitlines() if line.startswith("challenger"))
        assert expected in challenger_line, f"shipped challenger {expected}: {challenger_line}"
        assert challenger_line.rstrip().endswith("ok"), f"shipped config does not resolve: {challenger_line}"

    check("preflight resolves the shipped config", preflight)

    def skill_layout() -> None:
        prompts = SKILL_DIR / "prompts"
        implementer = (prompts / "implementer.md").read_text(encoding="utf-8")
        challenger = (prompts / "challenger.md").read_text(encoding="utf-8")
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for placeholder in ("{{ROUND}}", "{{BRIEF}}", "{{PAYLOAD}}", "{{COMMS}}"):
            assert placeholder in implementer, f"implementer missing {placeholder}"
            assert placeholder in challenger, f"challenger missing {placeholder}"
        for marker in ("DISPUTE F<n>", "ESCALATE F<n>"):
            assert marker in implementer, f"implementer missing {marker}"
        for marker in ("VERDICT: PASS|FAIL", "DISPUTE RESPONSES", "CONCEDE|MAINTAIN"):
            assert marker in challenger, f"challenger missing {marker}"
        for marker in ("prompts/implementer.md", "prompts/challenger.md", "BRIEF.md",
                       "LEDGER.md", "## Arbitration", "## PASS Guard",
                       "### Session keep-alive", "### Communication method ({{COMMS}})",
                       "Team mode", "Relay mode"):
            assert marker in skill, f"SKILL.md missing {marker}"
        # The role prompts must live in one place only.
        assert "You are a skilled implementer" not in skill, "role prompt duplicated in SKILL.md"
        assert "You are a rigorous" not in skill, "role prompt duplicated in SKILL.md"

    check("role prompts live in prompts/ and SKILL.md points at them", skill_layout)

    def comms_blocks() -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for mode in ("team", "relay"):
            for role in ("implementer", "challenger"):
                marker = f"*{role} / {mode}*"
                assert marker in skill, f"SKILL.md missing a {{COMMS}} block for {marker}"
        # The blocks are inserted verbatim, so each must carry the heading the prompt slot
        # stands in for.
        assert skill.count("## How you communicate") == 4, "expected one heading per {{COMMS}} block"

    check("{{COMMS}} documents a team and a relay block for both roles", comms_blocks)

    def brief_template() -> None:
        skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
        for marker in ("## Acceptance criteria", "## Artifact", "## Constraints / non-goals"):
            assert marker in skill, f"brief template missing {marker}"

    check("the brief template carries goal, criteria, artifact, constraints", brief_template)

    def shipped_config_stands() -> None:
        config = json.loads(shipped.read_text(encoding="utf-8"))
        assert {"implementer", "challenger"} <= set(config), config

    check("harness.json defines both roles", shipped_config_stands)

    def round_one_readonly() -> None:
        cases = {
            "codex": (codex, ["-s", "read-only"], True),
            "claude-code": (claude, ["--allowedTools=Read,Grep,Glob,Bash",
                                     "--disallowedTools=Edit,Write,NotebookEdit"], False),
            "opencode": (opencode, [], False),
            "dsh": (dsh, ["--profile", "headless", "--json"], False),
        }
        for name, (config, expected, uses_stdin) in cases.items():
            launch = dry(root, config, "challenger", 1)
            assert launch["harness"] == name, launch
            assert launch["resume"] is False, launch
            assert launch["stdin"] is uses_stdin, f"{name} stdin: {launch}"
            for fragment in expected:
                assert fragment in launch["argv"], f"{name} missing {fragment}: {launch['argv']}"

    check("challenger round 1 takes the read-only path on every harness", round_one_readonly)

    def implementer_flags() -> None:
        codex_impl = dry(root, write_config(root, {"implementer": {"harness": "codex"}}), "implementer", 1)
        assert "-s" in codex_impl["argv"] and "workspace-write" in codex_impl["argv"], codex_impl["argv"]
        claude_impl = dry(root, write_config(root, {"implementer": {"harness": "claude-code"}}), "implementer", 1)
        assert "bypassPermissions" in claude_impl["argv"], claude_impl["argv"]
        opencode_impl = dry(root, write_config(root, {"implementer": {"harness": "opencode"}}), "implementer", 1)
        assert "--auto" in opencode_impl["argv"], opencode_impl["argv"]

    check("implementer round 1 takes the write path on every harness", implementer_flags)

    def model_flag() -> None:
        config = write_config(root, {"challenger": {"harness": "opencode", "model": "alibaba-cn/x"}})
        launch = dry(root, config, "challenger", 1)
        assert launch["argv"][launch["argv"].index("-m") + 1] == "alibaba-cn/x", launch["argv"]

    check("model is forwarded as the harness's own model flag", model_flag)

    def resume_wiring() -> None:
        sid = "01a14775-ab52-7f63-bd0d-e307a7c8094e"
        cases = {
            "codex": (codex, ["exec", "resume", sid]),
            "claude-code": (claude, ["--resume", sid]),
            "opencode": (opencode, ["-s", sid]),
        }
        for name, (config, expected) in cases.items():
            run_root = root / f"resume-{name}"
            run_root.mkdir()
            (run_root / "prompt.md").write_text("continue\n", encoding="utf-8")
            seed_session(run_root, "challenger", name, sid)
            launch = dry(run_root, config, "challenger", 2)
            assert launch["resume"] is True, launch
            assert launch["session_id"] == sid, launch
            index = launch["argv"].index(expected[0])
            assert launch["argv"][index : index + len(expected)] == expected, launch["argv"]
        codex_resume = dry(root / "resume-codex", codex, "challenger", 2)
        assert "-C" not in codex_resume["argv"], "codex resume must not pass -C"
        assert "-s" not in codex_resume["argv"], "codex resume must not pass -s"

    check("round 2 resumes the recorded session on every session harness", resume_wiring)

    def reset_starts_over() -> None:
        run_root = root / "reset"
        run_root.mkdir()
        (run_root / "prompt.md").write_text("start over\n", encoding="utf-8")
        seed_session(run_root, "challenger", "opencode", "ses_old")
        launch = dry(run_root, opencode, "challenger", 2, "--reset-session")
        assert launch["resume"] is False, launch
        assert "ses_old" not in launch["argv"], launch["argv"]

    check("--reset-session drops the recorded session", reset_starts_over)

    def dsh_resumes() -> None:
        run_root = root / "dsh-resume"
        run_root.mkdir()
        (run_root / "prompt.md").write_text("delta only\n", encoding="utf-8")
        sid = "session-0d1e2f3a-4b5c-6d7e-8f90-aabbccddeeff"
        seed_session(run_root, "challenger", "dsh", sid)
        launch = dry(run_root, dsh, "challenger", 2)
        assert launch["resume"] is True, launch
        assert launch["persistence"] == "session", launch
        assert launch["session_id"] == sid, launch
        index = launch["argv"].index("--session-id")
        assert launch["argv"][index + 1] == sid, launch["argv"]
        assert launch["argv"][-1] == "delta only\n", launch["argv"]

    check("dsh resumes the recorded session like the other session harnesses", dsh_resumes)

    def dsh_cwd_guard() -> None:
        run_root = root / "dsh-cwd"
        run_root.mkdir()
        (run_root / "prompt.md").write_text("go\n", encoding="utf-8")
        seed_session(run_root, "challenger", "dsh", "session-x", str(root / "elsewhere"))
        result = invoke(
            [
                "--config",
                str(dsh),
                "run",
                "--role",
                "challenger",
                "--round",
                "2",
                "--prompt",
                str(run_root / "prompt.md"),
                "--out",
                str(run_root / "out.md"),
                "--run-dir",
                str(run_root / "run"),
                "--dry-run",
            ]
        )
        assert result.returncode == 2, f"expected 2, got {result.returncode}"
        assert "original --cwd" in result.stderr, result.stderr

    check("a dsh resume with a mismatched cwd exits 2 before launching", dsh_cwd_guard)

    def dsh_final_parsing() -> None:
        sys.path.insert(0, str(SKILL_DIR / "scripts"))
        import run_role  # noqa: PLC0415

        stream = (
            '{"type":"session","sessionId":"session-x","cwd":"/tmp"}\n'
            '{"type":"status","phase":"turn_end"}\n'
            '{"type":"final","text":"VERDICT: PASS"}\n'
        )
        assert run_role._final_dsh(stream, None) == "VERDICT: PASS"
        for bad in ('{"type":"status"}\n', '{"type":"final","text":""}\n', ""):
            try:
                run_role._final_dsh(bad, None)
            except run_role.InvocationError:
                pass
            else:
                raise AssertionError(f"stream without a final text must raise: {bad!r}")

    check("dsh final message is parsed from the --json final event", dsh_final_parsing)

    def native_is_rejected() -> None:
        config = write_config(root, {"implementer": {"harness": "native"}})
        result = invoke(
            [
                "--config",
                str(config),
                "run",
                "--role",
                "implementer",
                "--round",
                "1",
                "--prompt",
                str(root / "prompt.md"),
                "--out",
                str(root / "out.md"),
                "--run-dir",
                str(root / "run"),
                "--dry-run",
            ]
        )
        assert result.returncode == 3, f"expected 3, got {result.returncode}"

    check("a native role is rejected by the runner", native_is_rejected)

    def missing_session_fails() -> None:
        run_root = root / "no-session"
        run_root.mkdir()
        (run_root / "prompt.md").write_text("go\n", encoding="utf-8")
        result = invoke(
            [
                "--config",
                str(codex),
                "run",
                "--role",
                "challenger",
                "--round",
                "2",
                "--prompt",
                str(run_root / "prompt.md"),
                "--out",
                str(run_root / "out.md"),
                "--run-dir",
                str(run_root / "run"),
                "--dry-run",
            ]
        )
        assert result.returncode == 5, f"expected 5, got {result.returncode}"

    check("round 2 without a recorded session exits 5", missing_session_fails)

    def unknown_harness_fails() -> None:
        config = write_config(root, {"challenger": {"harness": "claud-code"}})
        result = invoke(
            [
                "--config",
                str(config),
                "run",
                "--role",
                "challenger",
                "--round",
                "1",
                "--prompt",
                str(root / "prompt.md"),
                "--out",
                str(root / "out.md"),
                "--run-dir",
                str(root / "run"),
                "--dry-run",
            ]
        )
        assert result.returncode == 2, f"expected 2, got {result.returncode}"

    check("an unknown harness name exits 2", unknown_harness_fails)

    def missing_binary_fails() -> None:
        config = write_config(root, {"challenger": {"harness": "codex", "path": "/nope/codex"}})
        result = invoke(["--config", str(config), "preflight"])
        assert result.returncode == 1, f"expected 1, got {result.returncode}"
        assert "missing" in result.stdout, result.stdout

    check("an explicit but missing path fails preflight", missing_binary_fails)

    def broken_config_warns() -> None:
        config = write_config(root, "{ this is not json")
        result = invoke(["--config", str(config), "preflight"])
        assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
        assert "both roles run native" in result.stderr, result.stderr

    check("a broken config warns and falls back to native", broken_config_warns)


def live(root: Path) -> None:
    """Real calls: one sentinel round 1 plus one resume round per harness."""
    token = f"{SENTINEL}_{int(time.time())}"
    cases = {
        "claude-code": "claude-code",
        "codex": "codex",
        "opencode": "opencode",
        "dsh": "dsh",
    }
    for name, harness in cases.items():
        case_root = root / f"live-{name}"
        case_root.mkdir()
        config = write_config(case_root, {"challenger": {"harness": harness}})
        prompt = case_root / "r1.md"
        prompt.write_text(f"Reply with exactly this token and nothing else: {token}\n", encoding="utf-8")
        out1 = case_root / "r1.out.md"

        def run_round(round_no: int, prompt_path: Path, out_path: Path) -> subprocess.CompletedProcess:
            return invoke(
                [
                    "--config",
                    str(config),
                    "run",
                    "--role",
                    "challenger",
                    "--round",
                    str(round_no),
                    "--prompt",
                    str(prompt_path),
                    "--out",
                    str(out_path),
                    "--run-dir",
                    str(case_root / "run"),
                    "--cwd",
                    str(case_root),
                    "--timeout",
                    "300",
                ],
                timeout=360,
            )

        def round_one(harness=harness, case_root=case_root, out1=out1, prompt=prompt) -> None:
            result = run_round(1, prompt, out1)
            assert result.returncode == 0, f"{harness} round 1 failed: {result.stderr.strip()[-400:]}"
            assert token in out1.read_text(encoding="utf-8"), out1.read_text(encoding="utf-8")[:200]

        check(f"{name}: round 1 returns the sentinel", round_one)

        def round_two(harness=harness, case_root=case_root) -> None:
            prompt2 = case_root / "r2.md"
            prompt2.write_text(
                "What token did I ask you to reply with in my previous message? "
                "Reply with only that token.\n",
                encoding="utf-8",
            )
            out2 = case_root / "r2.out.md"
            result = run_round(2, prompt2, out2)
            assert result.returncode == 0, f"{harness} resume failed: {result.stderr.strip()[-400:]}"
            assert token in out2.read_text(encoding="utf-8"), out2.read_text(encoding="utf-8")[:200]

        check(f"{name}: round 2 resumes and remembers the sentinel", round_two)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also make real model calls")
    parser.add_argument("--keep", action="store_true", help="keep the temp workspace")
    args = parser.parse_args()

    root = Path(tempfile.mkdtemp(prefix="ar-smoke-"))
    print(f"workspace: {root}")
    try:
        offline(root)
        if args.live:
            live(root)
    finally:
        if not args.keep and not FAILURES:
            import shutil

            shutil.rmtree(root, ignore_errors=True)
    passed = CHECKS - len(FAILURES)
    print(f"\n{passed}/{CHECKS} checks passed")
    if FAILURES:
        print(f"workspace kept for debugging: {root}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
