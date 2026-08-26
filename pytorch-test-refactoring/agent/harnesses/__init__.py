"""Harness registry.

Adding a harness is: implement ``Harness`` in a new module under
``agent/harnesses/``, then add one entry to ``HARNESSES`` below. The
orchestrator's ``--harness`` choices and its resume/feed commands derive from
this registry, so no orchestration code needs to change.
"""

from __future__ import annotations

from agent.harness import Harness
from agent.harnesses.claude import ClaudeHarness
from agent.harnesses.codex import CodexHarness


HARNESSES: dict[str, type[Harness]] = {
    "claude": ClaudeHarness,
    "codex": CodexHarness,
}

DEFAULT_HARNESS = "claude"


def get_harness(harness: str | None = None) -> Harness:
    name = harness or DEFAULT_HARNESS
    cls = HARNESSES.get(name)
    if cls is None:
        raise ValueError(
            f"Unknown harness {name!r}. Expected one of {sorted(HARNESSES)}."
        )
    return cls()
