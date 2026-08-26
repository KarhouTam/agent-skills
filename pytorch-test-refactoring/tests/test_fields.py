"""Tests for field resolution and field-aware workflow behavior."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

import utils
from agent.tasks import build_analyst_task, build_checker_task, build_coder_tasks
from flow import RefactorFlow
from state import CoderTask, ReviewFindings
from utils import (
    compute_applicable_rules,
    get_reference_dir,
    get_workspace,
    resolve_field,
)


def test_resolve_field_from_manifests():
    assert resolve_field("test/distributed/test_nccl.py") == "distributed"
    assert resolve_field("test/dynamo/test_compile.py") == "graph"
    assert resolve_field("test/test_ops.py") == "core"


def test_resolve_field_absolute_path(monkeypatch):
    monkeypatch.setattr(
        utils,
        "run_git",
        lambda *args, **kwargs: str(Path.cwd().resolve()) + "\n",
    )
    absolute = Path.cwd() / "test" / "inductor" / "test_compile.py"
    assert resolve_field(str(absolute)) == "graph"


def test_resolve_field_rejects_overlap(monkeypatch):
    monkeypatch.setattr(
        utils,
        "_read_field_test_paths",
        lambda field: {"test/ambiguous/test_file.py"},
    )
    with pytest.raises(ValueError, match="Ambiguous field"):
        resolve_field("test/ambiguous/test_file.py")


def test_fielded_workspace(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert get_workspace("test_foo", "graph") == Path("agent_space") / "refactor" / "graph" / "test_foo"
    assert get_workspace("test_foo", "core") == Path("agent_space") / "refactor" / "core" / "test_foo"


def test_reference_dirs_are_fielded():
    core = Path(get_reference_dir("core"))
    distributed = Path(get_reference_dir("distributed"))
    assert core.name == "reference"
    assert distributed.name == "distributed"
    assert distributed.parent == core


def test_non_core_rules_are_cleanup_only():
    assignments = {"TestFoo": "Strategy2"}
    assert compute_applicable_rules(assignments, "core") == [
        "strategy_2",
        "cleanup",
    ]
    assert compute_applicable_rules(assignments, "graph") == ["cleanup"]


def test_analyst_prompt_is_field_aware():
    core = build_analyst_task(
        "test/test_ops.py",
        "agent_space/refactor/core/test_ops",
        get_reference_dir("core"),
    )
    graph = build_analyst_task(
        "test/dynamo/test_compile.py",
        "agent_space/refactor/graph/test_compile",
        get_reference_dir("graph"),
    )
    assert "field-agnostic baseline" not in core.prompt
    assert "**Field:** `graph`" in graph.prompt
    assert "field-agnostic baseline" in graph.prompt


def test_coder_and_checker_prompts_are_field_aware():
    task = CoderTask(
        coder_id="coder-1",
        rule="cleanup",
        rule_description="Import cleanup and external reference updates",
        instructions="- remove stale imports",
    )
    prompt = build_coder_tasks(
        "test/distributed/test_nccl.py",
        "agent_space/refactor/distributed/test_nccl",
        [task],
        total_rules=1,
    )[0].prompt
    assert "**Field:** `distributed`" in prompt
    assert "field-agnostic cleanup baseline" in prompt

    checker = build_checker_task(
        "test/distributed/test_nccl.py",
        "agent_space/refactor/distributed/test_nccl",
        get_reference_dir("distributed"),
        3,
        "",
    )
    assert "**Field:** `distributed`" in checker.prompt
    assert "field-agnostic baseline" in checker.prompt


def test_non_core_local_test_is_skipped():
    flow = RefactorFlow()
    flow.state.file_path = "test/distributed/test_nccl.py"
    flow.state.field = "distributed"
    flow.state.review_findings = ReviewFindings(all_clear=True, findings=[])
    flow._phase_local_test()
    assert flow.state.test_sub_phase == "done"
    assert flow.state.local_test is None
