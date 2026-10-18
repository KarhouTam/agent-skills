#!/usr/bin/env python3
"""Run one round of one humanize role on the harness selected in harness.json.

The HARNESSES registry below is the single source of truth for how each harness is
launched, how its session is identified, and where its final message comes from.
SKILL.md documents the loop; this script only owns the per-harness plumbing.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SKILL_DIR / "harness.json"
ROLES = ("implementer", "challenger")


class InvocationError(Exception):
    """Bad usage or bad configuration; nothing was launched."""


@dataclass
class Role:
    harness: str = "native"
    model: str | None = None
    path: str | None = None


@dataclass
class Ctx:
    role: str
    spec: Role
    binary: str
    cwd: Path
    prompt: str
    session_id: str | None
    resume: bool
    out_file: Path | None


@dataclass
class Launch:
    argv: list[str]
    stdin: str | None = None
    out_file: Path | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class Harness:
    name: str
    candidates: tuple[str, ...]
    persistence: str  # "session" | "replay"
    session_source: str | None  # "caller" | "stdout" | None
    session_re: str | None
    build: Callable[[Ctx], Launch]
    final: Callable[[str, Path | None], str]


def resolve_binary(spec: Role, candidates: tuple[str, ...]) -> str | None:
    if spec.path:
        # An explicit path is authoritative: never fall back to PATH behind the user's back.
        path = Path(spec.path).expanduser()
        return str(path) if path.is_file() and os.access(path, os.X_OK) else None
    for candidate in candidates:
        if not candidate:
            continue
        if os.sep in candidate:
            path = Path(candidate).expanduser()
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
        else:
            found = shutil.which(candidate)
            if found:
                return found
    return None


# --- claude-code ------------------------------------------------------------


def _build_claude(ctx: Ctx) -> Launch:
    argv = [ctx.binary, "-p", "--output-format", "json"]
    if ctx.resume:
        argv += ["--resume", ctx.session_id or ""]
    else:
        argv += ["--session-id", ctx.session_id or ""]
    if ctx.role == "challenger":
        tools = "Read,Grep,Glob,Bash"
        argv += ["--tools", tools, "--allowedTools", tools, "--permission-prompts", "none"]
    else:
        argv += ["--permission-mode", "bypassPermissions"]
    if ctx.spec.model:
        argv += ["--model", ctx.spec.model]
    argv.append(ctx.prompt)
    return Launch(argv=argv, session_id=ctx.session_id)


def _final_claude(stdout: str, _out_file: Path | None) -> str:
    try:
        data = json.loads(stdout)
    except ValueError as exc:
        raise InvocationError(f"claude did not emit one JSON object: {exc}") from exc
    if not isinstance(data, dict) or data.get("is_error"):
        raise InvocationError(f"claude reported an error: {str(data)[:300]}")
    result = data.get("result")
    if not isinstance(result, str) or not result.strip():
        raise InvocationError("claude JSON carried no non-empty 'result' field")
    return result.strip()


# --- codex ------------------------------------------------------------------


def _build_codex(ctx: Ctx) -> Launch:
    sandbox = "read-only" if ctx.role == "challenger" else "workspace-write"
    if ctx.resume:
        # `codex exec resume` accepts no -C/-s: cwd and sandbox come from the session.
        argv = [ctx.binary, "exec", "resume", ctx.session_id or "", "--json"]
    else:
        argv = [ctx.binary, "exec", "--json", "-C", str(ctx.cwd), "-s", sandbox]
    argv += ["--skip-git-repo-check"]
    if ctx.spec.model:
        argv += ["-m", ctx.spec.model]
    argv += ["-o", str(ctx.out_file), "-"]
    return Launch(argv=argv, stdin=ctx.prompt, out_file=ctx.out_file)


def _final_codex(_stdout: str, out_file: Path | None) -> str:
    if out_file is None or not out_file.is_file():
        raise InvocationError("codex produced no -o last-message file")
    text = out_file.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        raise InvocationError("codex -o last-message file was empty")
    return text


# --- opencode ---------------------------------------------------------------


def _build_opencode(ctx: Ctx) -> Launch:
    argv = [ctx.binary, "run", "--format", "json", "--dir", str(ctx.cwd)]
    if ctx.resume:
        argv += ["-s", ctx.session_id or ""]
    if ctx.role == "implementer":
        argv += ["--auto"]
    if ctx.spec.model:
        argv += ["-m", ctx.spec.model]
    argv.append(ctx.prompt)
    return Launch(argv=argv, session_id=ctx.session_id)


def _final_opencode(stdout: str, _out_file: Path | None) -> str:
    texts: list[tuple[str | None, str]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict) or event.get("type") != "text":
            continue
        part = event.get("part") or {}
        text = part.get("text")
        if isinstance(text, str) and text:
            texts.append((part.get("messageID"), text))
    if not texts:
        raise InvocationError("opencode emitted no text events")
    last_message = texts[-1][0]
    return "\n".join(text for message, text in texts if message == last_message).strip()


# --- dsh --------------------------------------------------------------------


def _build_dsh(ctx: Ctx) -> Launch:
    return Launch(argv=[ctx.binary, "--profile", "headless", ctx.prompt])


def _final_dsh(stdout: str, _out_file: Path | None) -> str:
    text = stdout.strip()
    if not text:
        raise InvocationError("dsh headless printed an empty final message")
    return text


HARNESSES: dict[str, Harness] = {
    "claude-code": Harness(
        name="claude-code",
        candidates=("~/.local/bin/claude", "claude"),
        persistence="session",
        session_source="caller",
        session_re=None,
        build=_build_claude,
        final=_final_claude,
    ),
    "codex": Harness(
        name="codex",
        candidates=("codex", "~/.local/bin/codex"),
        persistence="session",
        session_source="stdout",
        session_re=r'"thread_id"\s*:\s*"([^"]+)"',
        build=_build_codex,
        final=_final_codex,
    ),
    "opencode": Harness(
        name="opencode",
        candidates=("~/.opencode/bin/opencode", "opencode"),
        persistence="session",
        session_source="stdout",
        session_re=r"ses_[A-Za-z0-9]+",
        build=_build_opencode,
        final=_final_opencode,
    ),
    "dsh": Harness(
        name="dsh",
        candidates=("dsh",),
        persistence="replay",
        session_source=None,
        session_re=None,
        build=_build_dsh,
        final=_final_dsh,
    ),
}

VALID_HARNESSES = ("native", *HARNESSES)


# --- configuration ----------------------------------------------------------


def load_config(path: Path) -> tuple[dict[str, Role], list[str]]:
    """Missing or unreadable config falls back to all-native, loudly."""
    warnings: list[str] = []
    fallback = {role: Role() for role in ROLES}
    if not path.is_file():
        warnings.append(f"no config at {path}: both roles run native")
        return fallback, warnings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        warnings.append(f"cannot read {path} ({exc}): both roles run native")
        return fallback, warnings
    if not isinstance(data, dict):
        warnings.append(f"{path} is not a JSON object: both roles run native")
        return fallback, warnings

    roles: dict[str, Role] = {}
    for role in ROLES:
        raw = data.get(role) or {}
        if not isinstance(raw, dict):
            warnings.append(f"{path}: '{role}' is not an object: running native")
            roles[role] = Role()
            continue
        harness = raw.get("harness", "native")
        if harness not in VALID_HARNESSES:
            raise InvocationError(
                f"{path}: unknown harness {harness!r} for '{role}'; "
                f"valid values: {', '.join(VALID_HARNESSES)}"
            )
        roles[role] = Role(
            harness=harness,
            model=raw.get("model") or None,
            path=raw.get("path") or None,
        )
    for key in data:
        if key not in ROLES:
            warnings.append(f"{path}: ignoring unknown key {key!r}")
    return roles, warnings


def describe(role: str, spec: Role) -> str:
    if spec.harness == "native":
        return f"{role:<12} native       in-process (driver agent tool)          ok"
    harness = HARNESSES[spec.harness]
    binary = resolve_binary(spec, harness.candidates)
    model = f" model={spec.model}" if spec.model else ""
    status = "ok" if binary else "missing"
    return (
        f"{role:<12} {spec.harness:<12} {harness.persistence:<8} "
        f"{binary or 'MISSING'}{model}  {status}"
    )


# --- subcommands ------------------------------------------------------------


def cmd_preflight(args: argparse.Namespace) -> int:
    roles, warnings = load_config(Path(args.config))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"config: {args.config}")
    missing = False
    for role in ROLES:
        print(describe(role, roles[role]))
        if roles[role].harness != "native" and not resolve_binary(
            roles[role], HARNESSES[roles[role].harness].candidates
        ):
            missing = True
    if missing:
        print(
            "error: a configured harness binary was not found; "
            "set 'path' for that role in harness.json",
            file=sys.stderr,
        )
        return 1
    return 0


def _read_sessions(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_sessions(path: Path, sessions: dict) -> None:
    path.write_text(json.dumps(sessions, indent=2) + "\n", encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    if args.round < 1:
        print("error: --round is 1-based; pass 1 for the first round", file=sys.stderr)
        return 2
    roles, warnings = load_config(Path(args.config))
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    spec = roles[args.role]
    if spec.harness == "native":
        print(
            f"error: '{args.role}' is configured native — run it with this harness's own "
            "agent tool, not run_role.py",
            file=sys.stderr,
        )
        return 3

    harness = HARNESSES[spec.harness]
    binary = resolve_binary(spec, harness.candidates)
    if not binary:
        if spec.path:
            detail = f"configured path is not executable: {spec.path}"
        else:
            detail = f"tried: {', '.join(harness.candidates)}"
        print(
            f"error: no {spec.harness} binary for '{args.role}' ({detail}); "
            f"set 'path' for that role in {args.config}",
            file=sys.stderr,
        )
        return 4

    prompt_path = Path(args.prompt)
    if not prompt_path.is_file():
        print(f"error: prompt file not found: {prompt_path}", file=sys.stderr)
        return 2
    prompt = prompt_path.read_text(encoding="utf-8")

    run_dir = Path(args.run_dir).resolve()
    out_path = Path(args.out).resolve()
    cwd = Path(args.cwd).resolve() if args.cwd else Path.cwd()
    sessions_path = run_dir / "sessions.json"
    sessions = _read_sessions(sessions_path)

    resume = args.round > 1 and not args.reset_session
    if harness.persistence == "replay":
        resume = False

    session_id: str | None = None
    if harness.persistence == "session":
        if resume:
            session_id = (sessions.get(args.role) or {}).get("session_id")
            if not session_id:
                print(
                    f"error: no session recorded for '{args.role}' in {sessions_path}; "
                    "run round 1 first or pass --reset-session",
                    file=sys.stderr,
                )
                return 5
        elif harness.session_source == "caller":
            session_id = str(uuid.uuid4())

    out_file = run_dir / f"round-{args.round}-{args.role}.harness-out"
    launch = harness.build(
        Ctx(
            role=args.role,
            spec=spec,
            binary=binary,
            cwd=cwd,
            prompt=prompt,
            session_id=session_id,
            resume=resume,
            out_file=out_file,
        )
    )

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "role": args.role,
                    "harness": harness.name,
                    "binary": binary,
                    "persistence": harness.persistence,
                    "resume": resume,
                    "session_id": session_id,
                    "cwd": str(cwd),
                    "argv": launch.argv,
                    "stdin": launch.stdin is not None,
                    "out_file": str(launch.out_file) if launch.out_file else None,
                }
            )
        )
        return 0

    run_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # Never let a harness inherit our stdin: only codex is fed through it.
    io = {"input": launch.stdin} if launch.stdin is not None else {"stdin": subprocess.DEVNULL}
    try:
        proc = subprocess.run(
            launch.argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=args.timeout,
            **io,
        )
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = 6
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = f"timed out after {args.timeout}s"
    except OSError as exc:
        returncode = 1
        stdout = ""
        stderr = f"failed to launch {binary}: {exc}"
    duration_ms = int((time.monotonic() - started) * 1000)

    log_path = run_dir / f"round-{args.round}-{args.role}.log"
    log_path.write_text(
        "argv: "
        + json.dumps(launch.argv)
        + f"\ncwd: {cwd}\nstdin: {'prompt file' if launch.stdin else '(none)'}\n"
        + f"\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n",
        encoding="utf-8",
    )

    if harness.session_source == "stdout" and harness.session_re:
        match = re.search(harness.session_re, stdout)
        if match:
            session_id = match.group(1) if match.lastindex else match.group(0)

    summary = {
        "role": args.role,
        "harness": harness.name,
        "model": spec.model,
        "round": args.round,
        "persistence": harness.persistence,
        "resume": resume,
        "session_id": session_id,
        "exit": returncode,
        "duration_ms": duration_ms,
        "out": str(out_path),
        "log": str(log_path),
    }

    if session_id and harness.persistence == "session":
        sessions[args.role] = {"harness": harness.name, "session_id": session_id}
        _write_sessions(sessions_path, sessions)

    failed = returncode != 0
    if not failed:
        try:
            final = harness.final(stdout, launch.out_file)
        except InvocationError as exc:
            summary["exit"] = returncode = 1
            summary["error"] = str(exc)
            failed = True
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(final + "\n", encoding="utf-8")

    with (run_dir / "run.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(summary) + "\n")

    print(json.dumps(summary))
    if failed:
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        print(f"error: {args.role} on {harness.name} failed (exit {returncode})", file=sys.stderr)
        if tail:
            print(tail, file=sys.stderr)
        print(f"log: {log_path}", file=sys.stderr)
        return 6 if returncode == 6 else 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to harness.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="resolve both roles and check binaries")
    preflight.set_defaults(func=cmd_preflight)
    preflight.add_argument("--config", default=argparse.SUPPRESS, help="path to harness.json")

    run = subparsers.add_parser("run", help="run one round of one role")
    run.add_argument("--config", default=argparse.SUPPRESS, help="path to harness.json")
    run.add_argument("--role", required=True, choices=ROLES)
    run.add_argument("--round", required=True, type=int, help="1-based round number")
    run.add_argument("--prompt", required=True, help="file holding this round's message")
    run.add_argument("--out", required=True, help="file to write the final message to")
    run.add_argument("--run-dir", required=True, help="directory for sessions, logs, run.jsonl")
    run.add_argument("--cwd", default=None, help="working directory for the harness process")
    run.add_argument("--reset-session", action="store_true", help="start a new session")
    run.add_argument("--dry-run", action="store_true", help="print the resolved launch only")
    run.add_argument("--timeout", type=int, default=1800, help="process timeout in seconds")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except InvocationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
