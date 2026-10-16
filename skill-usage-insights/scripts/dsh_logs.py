"""Read DeepSeek Harness session logs.

dsh persists every session as zstd-compressed JSONL at
`$DSH_HOME/sessions/<workspace-slug>/<session-id>/session.v3.jsonl.zstd`. The
file is a *multi-frame* zstd stream — one frame per append — and a decoder that
handles only the first frame sees the session header and nothing else. Node's
`zlib.zstdDecompressSync` decodes one frame per call and dsh always ships Node,
so `decode_events` drives `dsh_decode.mjs` and never re-implements zstd here.

Stdlib only, like the rest of the repo: this runs before any analysis.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

DECODER = Path(__file__).resolve().parent / "dsh_decode.mjs"
DECODE_TIMEOUT = 120
_DECODER = False  # False = not probed yet, None = probed and unusable
WANTED = (
    "session", "turn/start", "turn/end", "assistant/message", "tool/call", "tool/result",
    "user/message", "subagent/catalog", "goal/change", "command/run",
)


class DecodeError(RuntimeError):
    """The log could not be decoded: no usable decoder, or a log that is not readable."""


def dsh_home():
    return Path(os.environ.get("DSH_HOME") or os.path.expanduser("~/.dsh")).expanduser()


def sessions_root(home=None):
    return Path(home or dsh_home()) / "sessions"


def session_paths(root=None, since=0.0):
    """Every `session.v3.jsonl.zstd` under the sessions root, newest first.

    `since` is an epoch; a log whose mtime is older is one the session can no
    longer be appending to, so it cannot hold anything newer than the window.
    """
    root = Path(root or sessions_root())
    if not root.is_dir():
        return []
    found = [path for path in root.glob("*/*/session.v3.jsonl.zstd") if path.stat().st_mtime >= since]
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


NODE_HINT = "Node 22.15+ (it has zstd in node:zlib)"


def decoder_command():
    """The argv that decodes one log, or None when this machine cannot decode.

    Having *a* `node` is not enough: dsh ships its own recent Node, but an older one
    earlier on PATH has no `node:zlib.zstdDecompressSync` and would fail per log
    instead of once. So the exact import the decoder does is probed, not the version.
    """
    global _DECODER
    if _DECODER is not False:
        return _DECODER or None
    _DECODER = None
    node = shutil.which("node") or shutil.which("nodejs")
    if node and DECODER.is_file():
        probe = "import('node:zlib').then(z => process.exit(z.zstdDecompressSync ? 0 : 1), () => process.exit(1))"
        try:
            if subprocess.run([node, "-e", probe], capture_output=True, timeout=30).returncode == 0:
                _DECODER = [node, str(DECODER)]
        except (OSError, subprocess.SubprocessError):
            _DECODER = None
    return _DECODER


def decode_events(path, timeout=DECODE_TIMEOUT):
    """Every event of one session log, in file order, each stamped with its log path."""
    command = decoder_command()
    if command is None:
        raise DecodeError(f"no usable Node on PATH: decoding dsh session logs needs {NODE_HINT}")
    try:
        done = subprocess.run(command + [str(path)], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DecodeError(f"{path}: {exc}") from exc
    if done.returncode != 0:
        raise DecodeError(f"{path}: decoder exited {done.returncode}: {reason(done.stderr)}")
    try:
        events = json.loads(done.stdout or "[]")
    except ValueError as exc:
        raise DecodeError(f"{path}: decoder output is not JSON ({exc})") from exc
    for event in events:
        event["log"] = str(path)
    return events


def reason(stderr):
    """The informative line of a Node failure: the exception, not the offending import."""
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if ":" in line and not line.startswith(("at ", "file://", "^")) and "node:" not in line.split(":")[0]:
            return line[:300]
    return lines[-1][:300] if lines else "no output"


def session_header(events):
    """The `session` event every log starts with: id, cwd, creation time, parent."""
    for event in events:
        if event.get("type") == "session":
            return event
    return {}


def decode_available():
    return decoder_command() is not None


def main(argv=None):  # pragma: no cover - a debugging aid, not part of the pipeline
    """`python3 dsh_logs.py [--root DIR] [--since EPOCH] [--head N]`: decode and count."""
    import argparse

    parser = argparse.ArgumentParser(description="Decode dsh session logs and summarise them.")
    parser.add_argument("--root", type=Path, default=None, help="sessions root (default: $DSH_HOME/sessions)")
    parser.add_argument("--since", type=float, default=0.0, help="epoch; skip older logs")
    parser.add_argument("--head", type=int, default=12, help="how many logs to show")
    args = parser.parse_args(argv)

    paths = session_paths(args.root, args.since)
    if not paths:
        print(f"no session logs under {args.root or sessions_root()}", file=sys.stderr)
        return 1
    print(f"{len(paths)} session log(s) under {args.root or sessions_root()}")
    for path in paths[: args.head]:
        try:
            events = decode_events(path)
        except DecodeError as exc:
            print(f"  {path.parent.name}: {exc}")
            continue
        header = session_header(events)
        print(f"  {header.get('id', path.parent.name)}  {len(events)} events  cwd={header.get('cwd', '?')}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
