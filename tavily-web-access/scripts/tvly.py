#!/usr/bin/env python3
"""Front door for Tavily web access.

Every command goes through this script so a key is always supplied, keys rotate
when the API rejects one, and large payloads can stay on disk instead of in the
conversation. Keys live outside this repo in ~/.tavily/keys.json (mode 0600);
the first run fetches them from the gist below.

    python3 tvly.py search "query" --max-results 5
    python3 tvly.py extract "https://example.com" -o /tmp/page.json
    python3 tvly.py crawl "https://docs.example.com" --output-dir /tmp/docs
    python3 tvly.py --refresh-keys
"""

from __future__ import annotations

import json
import os
import re
import socket
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

# Prefer IPv4: some hosts blackhole their IPv6 route and requests then stalls on
# the connect timeout before falling back (~60s per call), while curl is
# unaffected because it tries both families at once. Patch before importing the
# SDK so its HTTP stack inherits the patched resolver.
_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    results = _getaddrinfo(*args, **kwargs)
    return [result for result in results if result[0] == socket.AF_INET] or results


socket.getaddrinfo = _ipv4_first

from tavily import (  # noqa: E402  (import follows the resolver patch)
    BadRequestError,
    InvalidAPIKeyError,
    TavilyClient,
    UsageLimitExceededError,
)

# ForbiddenError covers 403/432/433 but is not re-exported from the package root.
from tavily.errors import ForbiddenError  # noqa: E402

GIST_RAW = "https://gist.githubusercontent.com/KarhouTam/d915a40bf415620a1268b9df4bce5763/raw"
KEYS_FILE = Path.home() / ".tavily" / "keys.json"
INSTALL_HINT = "pip install tavily-python"

# The API's own classification of a key-specific failure: 429 / 403+432+433 / 401.
# Anything else (a malformed query, a timeout) will not be fixed by another key.
ROTATE_ON = (UsageLimitExceededError, ForbiddenError, InvalidAPIKeyError)

METHODS = {
    "search": "search",
    "extract": "extract",
    "map": "map",
    "crawl": "crawl",
    "research": "research",
    "research-status": "get_research",
}
# Parameters the API expects as a list; everything else is a scalar.
LIST_PARAMS = {"include_domains", "exclude_domains", "select_paths", "select_domains", "exclude_paths"}
VALUE_OPTIONS = {"-o": "output", "--output": "output", "--output-dir": "output_dir", "--max-wait": "max_wait"}
POLL_SECONDS = 5

USAGE = """usage: tvly.py <command> [arguments] [--param value ...]

Commands:
  search "query"          web search
  extract URL [URL ...]   page content as markdown
  map URL                 discover the URLs on a site
  crawl URL               bulk content from a site section
  research "question"     multi-source synthesis with citations
  research-status ID      fetch a research task started with --no-wait

Options handled here:
  -o, --output FILE    write the response to FILE and print a one-line summary
  --output-dir DIR     crawl only: one markdown file per page
  --no-wait            research only: return the request_id immediately
  --max-wait SECONDS   research only: polling budget (default 300)
  --refresh-keys       re-fetch the key list from the gist
  --check-keys         report the stored key count and exit
  --json               accepted for compatibility; output is always JSON

Any other --flag value is passed to the Tavily SDK with dashes turned into
underscores: --max-results 5, --include-raw-content markdown,
--include-domains a.com,b.com, --extract-depth advanced.
"""


class Usage(Exception):
    """Bad command line."""


def fetch_keys() -> list[str]:
    """Read the key list from the personal gist."""
    with urllib.request.urlopen(GIST_RAW, timeout=30) as response:
        text = response.read().decode("utf-8", "replace")
    keys = [token for token in text.split() if token.startswith("tvly-")]
    if not keys:
        raise RuntimeError(f"no tvly- keys found at {GIST_RAW}")
    return keys


def save_state(state: dict) -> None:
    """Persist the key list and active index. Best effort: sandboxes may deny it."""
    try:
        KEYS_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        KEYS_FILE.parent.chmod(0o700)
        handle, temp_name = tempfile.mkstemp(dir=KEYS_FILE.parent, prefix=".keys-", suffix=".tmp")
        with os.fdopen(handle, "w", encoding="utf-8") as temp_file:
            temp_file.write(json.dumps(state, indent=2) + "\n")
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, KEYS_FILE)
    except OSError as error:
        print(f"tvly.py: could not persist {KEYS_FILE} ({error}); continuing", file=sys.stderr)


def load_state(refresh: bool) -> tuple[list[str], int]:
    """Return (keys, active index), bootstrapping from the gist on first use."""
    state: dict = {}
    if KEYS_FILE.is_file() and not refresh:
        try:
            state = json.loads(KEYS_FILE.read_text())
        except (OSError, json.JSONDecodeError):
            state = {}

    keys = [key for key in state.get("keys", []) if isinstance(key, str) and key.startswith("tvly-")]
    if keys and not refresh:
        return keys, int(state.get("active", 0)) % len(keys)

    keys = fetch_keys()
    print(f"tvly.py: fetched {len(keys)} keys from gist into {KEYS_FILE}", file=sys.stderr)
    save_state({"keys": keys, "active": 0})
    return keys, 0


def coerce(name: str, value: str):
    """Turn a command-line string into the type the SDK expects."""
    if name in LIST_PARAMS:
        return [item.strip() for item in value.split(",") if item.strip()]
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    for cast in (int, float):
        try:
            return cast(value)
        except ValueError:
            pass
    return value


def parse_args(argv: list[str]):
    """Split argv into (command, positionals, params, local options)."""
    local = {
        "output": None,
        "output_dir": None,
        "no_wait": False,
        "max_wait": 300.0,
        "refresh": False,
        "check": False,
        "help": False,
    }
    command: str | None = None
    positionals: list[str] = []
    params: dict = {}

    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in VALUE_OPTIONS:
            if index + 1 >= len(argv):
                raise Usage(f"{arg} needs a value")
            local[VALUE_OPTIONS[arg]] = argv[index + 1]
            index += 2
        elif arg == "--no-wait":
            local["no_wait"] = True
            index += 1
        elif arg in ("-h", "--help"):
            local["help"] = True
            index += 1
        elif arg == "--refresh-keys":
            local["refresh"] = True
            index += 1
        elif arg == "--check-keys":
            local["check"] = True
            index += 1
        elif arg == "--json":
            index += 1
        elif arg.startswith("--"):
            name, _, inline = arg[2:].partition("=")
            name = name.replace("-", "_")
            if inline:
                params[name] = coerce(name, inline)
                index += 1
            elif index + 1 < len(argv) and not argv[index + 1].startswith("--"):
                params[name] = coerce(name, argv[index + 1])
                index += 2
            else:
                params[name] = True
                index += 1
        elif command is None:
            command = arg
            index += 1
        else:
            positionals.append(arg)
            index += 1

    return command, positionals, params, local


def build_params(command: str, positionals: list[str], params: dict) -> dict:
    """Attach the positional argument the command needs."""
    if not positionals:
        raise Usage(f"{command} needs an argument")
    if command == "extract":
        params["urls"] = positionals
        return params
    if len(positionals) > 1:
        raise Usage(f"{command} takes a single argument")
    first = {"search": "query", "map": "url", "crawl": "url", "research": "input", "research-status": "request_id"}
    params[first[command]] = positionals[0]
    return params


def call_with_rotation(keys: list[str], active: int, operation):
    """Run operation(client), advancing the key when the API rejects it."""
    for attempt in range(len(keys)):
        index = (active + attempt) % len(keys)
        try:
            with TavilyClient(api_key=keys[index]) as client:
                result = operation(client)
        except ROTATE_ON as error:
            if attempt == len(keys) - 1:
                raise
            next_index = (index + 1) % len(keys)
            print(
                f"tvly.py: key #{index + 1} rejected ({type(error).__name__}), "
                f"trying key #{next_index + 1} of {len(keys)}",
                file=sys.stderr,
            )
            save_state({"keys": keys, "active": next_index})
            continue
        if index != active:
            save_state({"keys": keys, "active": index})
        return result
    raise AssertionError("unreachable")


def run_research(client, params: dict, local: dict) -> dict:
    """Start a research task, then poll it unless asked not to wait."""
    started = client.research(**params)
    request_id = started.get("request_id")
    print(f"tvly.py: research request_id={request_id}", file=sys.stderr)
    if local["no_wait"] or not request_id:
        return started

    deadline = time.monotonic() + float(local["max_wait"])
    result = started
    while time.monotonic() < deadline:
        if result.get("status") in ("completed", "failed"):
            return result
        time.sleep(POLL_SECONDS)
        result = client.get_research(request_id)
        print(f"tvly.py: research {result.get('status')}", file=sys.stderr)
    return result


def write_pages(result: dict, directory: str) -> list[str]:
    """Write one markdown file per crawled page."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for index, page in enumerate(result.get("results", []), 1):
        slug = re.sub(r"[^A-Za-z0-9]+", "-", urlparse(page.get("url", "")).path).strip("-")
        path = target / f"{index:03d}-{(slug or 'index')[:60]}.md"
        path.write_text(f"# {page.get('url', '')}\n\n{page.get('raw_content') or ''}\n")
        written.append(str(path))
    return written


def summarize(command: str, result: dict) -> str:
    """One line describing what came back, for when the payload went to a file."""
    if command == "extract":
        return f"extract: {len(result.get('results', []))} ok, {len(result.get('failed_results', []))} failed"
    if command == "map":
        return f"map: {len(result.get('results', []))} urls"
    if command == "crawl":
        return f"crawl: {len(result.get('results', []))} pages, {len(result.get('failed_results', []))} failed"
    if command in ("research", "research-status"):
        return f"research: status={result.get('status')}, {len(result.get('content') or '')} chars"
    return f"search: {len(result.get('results', []))} results"


def emit(command: str, result: dict, local: dict) -> None:
    if local["output_dir"] and command == "crawl":
        written = write_pages(result, local["output_dir"])
        print(f"crawl: wrote {len(written)} pages to {local['output_dir']}")
        for path in written:
            print(path)
        return

    payload = json.dumps(result, indent=2, ensure_ascii=False)
    if local["output"]:
        Path(local["output"]).write_text(payload + "\n")
        print(summarize(command, result))
    else:
        print(payload)


def main() -> int:
    try:
        command, positionals, params, local = parse_args(sys.argv[1:])
    except Usage as error:
        print(f"tvly.py: {error}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    if local["help"]:
        print(USAGE, file=sys.stderr)
        return 0
    if command is None and not (local["refresh"] or local["check"]):
        print(USAGE, file=sys.stderr)
        return 2
    if command is not None and command not in METHODS:
        print(f"tvly.py: unknown command {command!r}", file=sys.stderr)
        print(USAGE, file=sys.stderr)
        return 2

    try:
        keys, active = load_state(local["refresh"])
    except Exception as error:  # network, HTTP, or gist format failure
        print(f"tvly.py: could not load API keys: {error}", file=sys.stderr)
        print("tvly.py: check the gist URL or export TAVILY_API_KEY and call the SDK directly.", file=sys.stderr)
        return 1

    if local["refresh"] or local["check"]:
        print(f"tvly.py: {len(keys)} keys available in {KEYS_FILE}", file=sys.stderr)
    if command is None:
        return 0

    try:
        params = build_params(command, positionals, params)
    except Usage as error:
        print(f"tvly.py: {error}", file=sys.stderr)
        return 2

    try:
        result = call_with_rotation(
            keys, active, lambda client: run_research(client, params, local) if command == "research"
            else getattr(client, METHODS[command])(**params)
        )
    except ROTATE_ON as error:
        print(f"tvly.py: all {len(keys)} keys rejected; last error: {error}", file=sys.stderr)
        return 3
    except BadRequestError as error:
        print(f"tvly.py: bad request: {error}", file=sys.stderr)
        return 4
    except Exception as error:
        print(f"tvly.py: {type(error).__name__}: {error}", file=sys.stderr)
        return 4

    emit(command, result, local)
    return 0


if __name__ == "__main__":
    sys.exit(main())
