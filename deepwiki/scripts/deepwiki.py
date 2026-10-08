#!/usr/bin/env python3
"""DeepWiki over plain HTTP: a repo's wiki page tree, and one focused question.

    python3 scripts/deepwiki.py structure pytorch/pytorch
    python3 scripts/deepwiki.py ask vllm-project/vllm "how does prefix caching work?"

The endpoint is public, key-less and stateless, so this needs nothing but `python3`
and network. It unwraps the SSE framing and prints the answer text; a repo that is
not indexed exits non-zero with a readable reason.

Exit codes: 0 = ok, 1 = the server reported an error, 2 = bad usage or unreachable.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

ENDPOINT = "https://mcp.deepwiki.com/mcp"
TIMEOUT = 180


def call(tool: str, arguments: dict) -> str:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
         "params": {"name": tool, "arguments": arguments}}
    ).encode()
    request = urllib.request.Request(
        ENDPOINT,
        data=body,
        headers={"content-type": "application/json",
                 "accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        raw = response.read().decode("utf-8", "replace")

    # Streamable HTTP answers as SSE; the JSON-RPC message is the `data:` payload.
    payload = next((line[6:] for line in reversed(raw.splitlines()) if line.startswith("data: ")), raw)
    message = json.loads(payload)
    if "error" in message:
        raise SystemExit(f"error: {message['error'].get('message', message['error'])}")
    result = message["result"]
    if result.get("isError"):
        raise SystemExit("error: " + result["content"][0]["text"])
    return result["content"][0]["text"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    for name, tool in (("structure", "read_wiki_structure"), ("ask", "ask_wiki_question")):
        parser = sub.add_parser(name, help=f"call {tool}")
        parser.add_argument("repo", help="owner/repo, e.g. pytorch/pytorch")
        if name == "ask":
            parser.add_argument("question", help="the question to ask about that repo")
        parser.set_defaults(tool=tool)
    args = ap.parse_args()

    arguments = {"repoName": args.repo}
    if args.command == "ask":
        arguments["question"] = args.question
    try:
        print(call(args.tool, arguments))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
