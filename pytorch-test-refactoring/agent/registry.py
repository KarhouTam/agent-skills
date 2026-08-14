"""Harness adapter registry.

Adding a harness is: implement BaseAdapter, then register it here. The
orchestrator selects an adapter by name and delegates all harness-specific
emission to it, so no orchestration code needs to branch on the harness.
"""

from __future__ import annotations

from agent.adapter import BaseAdapter
from agent.claude_code import ClaudeCodeAdapter
from agent.codex import CodexAdapter


HARNESS_ADAPTERS: dict[str, type[BaseAdapter]] = {
    "claude": ClaudeCodeAdapter,
    "codex": CodexAdapter,
}

DEFAULT_HARNESS = "claude"


def get_adapter(harness: str | None = None) -> BaseAdapter:
    name = harness or DEFAULT_HARNESS
    cls = HARNESS_ADAPTERS.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown harness {name!r}. Expected one of {sorted(HARNESS_ADAPTERS)}."
        )
    return cls()
