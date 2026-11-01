from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import NOTE, SYNC, run, write_draft, write_material, write_note

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def bare_remote(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    return remote


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def tree(remote: Path, ref: str) -> list[str]:
    return sorted(git(remote, "ls-tree", "-r", "--name-only", ref).splitlines())


def branches(remote: Path) -> list[str]:
    return sorted(git(remote, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines())


def seed_machine(root: Path) -> None:
    write_note(root, "pytorch/dynamo", nutshell="Dynamo 抓图。")
    write_material(root, "pytorch/dynamo", "demo.py")
    write_draft(root, "vllm/sched", nutshell="调度决定 batch。")
    write_material(root, "vllm/sched", "sched.ipynb", '{"cells": []}')
    write_material(root, "vllm/sched", "sched.py", "print(1)\n")
    run(NOTE, "add", root / "pytorch" / "dynamo" / "note.md")
    run(NOTE, "index", root)


def test_init_publishes_main_and_draft_branches(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a = tmp_path / "a"
    seed_machine(a)
    run(SYNC, "init", remote, a)

    assert tree(remote, "main") == [
        "pytorch/_project.md",
        "pytorch/dynamo/demo.py",
        "pytorch/dynamo/note.md",
    ]
    assert branches(remote) == ["main", "script/vllm/sched"]
    draft = tree(remote, "script/vllm/sched")
    assert "vllm/sched/note.md" in draft
    assert "vllm/sched/sched.py" in draft
    assert "vllm/sched/sched.ipynb" in draft
    assert not any("state.json" in path for path in draft)
    assert "pytorch/dynamo/note.md" in draft  # the branch is based on main


def test_second_machine_pulls_main_and_drafts(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    seed_machine(a)
    run(SYNC, "init", remote, a)
    run(SYNC, "init", remote, b)
    run(NOTE, "index", b)

    assert (b / "pytorch" / "dynamo" / "note.md").is_file()
    assert (b / ".drafts" / "vllm" / "sched" / "note.md").is_file()
    assert (b / "vllm" / "sched" / "sched.py").is_file()
    check = run(NOTE, "check", b)
    assert "1 draft(s) awaiting the user's confirmation: vllm/sched" in check.stdout


def test_draft_update_propagates_to_other_machine(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    seed_machine(a)
    run(SYNC, "init", remote, a)
    run(SYNC, "init", remote, b)

    with open(b / ".drafts" / "vllm" / "sched" / "note.md", "a", encoding="utf-8") as fh:
        fh.write("- 新线头。\n")
    run(SYNC, "draft", "vllm/sched", b)
    assert "新线头" in git(remote, "show", "script/vllm/sched:vllm/sched/note.md")

    run(SYNC, "pull", a)
    assert "新线头" in (a / ".drafts" / "vllm" / "sched" / "note.md").read_text(encoding="utf-8")


def test_conflict_is_reported_and_force_resolves(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    seed_machine(a)
    run(SYNC, "init", remote, a)
    run(SYNC, "init", remote, b)

    with open(b / ".drafts" / "vllm" / "sched" / "note.md", "a", encoding="utf-8") as fh:
        fh.write("- B edit\n")
    run(SYNC, "draft", "vllm/sched", b)
    with open(a / ".drafts" / "vllm" / "sched" / "note.md", "a", encoding="utf-8") as fh:
        fh.write("- A edit\n")

    pulled = run(SYNC, "pull", a, check=False)
    assert pulled.returncode == 1
    assert "CONFLICT draft vllm/sched" in pulled.stderr
    assert "- A edit" in (a / ".drafts" / "vllm" / "sched" / "note.md").read_text(encoding="utf-8")

    pushed = run(SYNC, "draft", "vllm/sched", a, check=False)
    assert pushed.returncode == 1
    run(SYNC, "draft", "vllm/sched", a, "--force")
    run(SYNC, "pull", b, "--force")
    assert "- A edit" in (b / ".drafts" / "vllm" / "sched" / "note.md").read_text(encoding="utf-8")


def test_archive_merges_branch_and_removes_draft(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    seed_machine(a)
    run(SYNC, "init", remote, a)
    run(SYNC, "init", remote, b)

    run(NOTE, "promote", b / ".drafts" / "vllm" / "sched" / "note.md")
    run(NOTE, "check", b)
    run(SYNC, "archive", "vllm/sched", b)

    assert "vllm/sched/note.md" in tree(remote, "main")
    assert "vllm/sched/sched.py" in tree(remote, "main")
    assert branches(remote) == ["main"]
    merges = git(remote, "log", "--merges", "--format=%s", "main")
    assert "archive vllm/sched" in merges

    run(SYNC, "pull", a)
    run(NOTE, "index", a)
    run(NOTE, "check", a)
    assert (a / "vllm" / "sched" / "note.md").is_file()
    assert not (a / ".drafts" / "vllm" / "sched").exists()


def test_push_main_conflict_and_force(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    seed_machine(a)
    run(SYNC, "init", remote, a)
    run(SYNC, "init", remote, b)

    with open(a / "pytorch" / "_project.md", "a", encoding="utf-8") as fh:
        fh.write("\n- A learner note\n")
    with open(b / "pytorch" / "_project.md", "a", encoding="utf-8") as fh:
        fh.write("\n- B learner note\n")
    run(SYNC, "push", a)
    failed = run(SYNC, "push", b, check=False)
    assert failed.returncode == 1
    assert "CONFLICT main: pytorch/_project.md" in failed.stderr

    run(SYNC, "push", b, "--force")
    run(SYNC, "pull", a, "--force")
    project = (a / "pytorch" / "_project.md").read_text(encoding="utf-8")
    assert "B learner note" in project


def test_promoted_but_unarchived_topic_never_reaches_main(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a = tmp_path / "a"
    seed_machine(a)
    run(SYNC, "init", remote, a)

    run(NOTE, "promote", a / ".drafts" / "vllm" / "sched" / "note.md")
    run(SYNC, "push", a)
    assert "vllm/sched/note.md" not in tree(remote, "main")

    run(SYNC, "archive", "vllm/sched", a)
    assert "vllm/sched/note.md" in tree(remote, "main")
    assert branches(remote) == ["main"]


def test_local_only_and_offline_are_safe(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    write_draft(plain, "pytorch/foo")
    local = run(SYNC, "pull", plain)
    assert "local-only" in local.stdout

    remote = bare_remote(tmp_path)
    a = tmp_path / "a"
    seed_machine(a)
    run(SYNC, "init", remote, a)
    offline = tmp_path / "moved.git"
    remote.rename(offline)
    try:
        failed = run(SYNC, "pull", a, check=False)
        assert failed.returncode == 1
        assert "FAILED" in failed.stderr
        draft_failed = run(SYNC, "draft", "vllm/sched", a, check=False)
        assert draft_failed.returncode == 1
        assert (a / ".drafts" / "vllm" / "sched" / "note.md").is_file()
    finally:
        offline.rename(remote)


def test_missing_local_draft_is_restored_by_pull(tmp_path: Path) -> None:
    remote = bare_remote(tmp_path)
    a, b = tmp_path / "a", tmp_path / "b"
    seed_machine(a)
    run(SYNC, "init", remote, a)
    run(SYNC, "init", remote, b)

    shutil.rmtree(b / ".drafts" / "vllm")
    run(SYNC, "pull", b)
    assert (b / ".drafts" / "vllm" / "sched" / "note.md").is_file()
    assert (b / "vllm" / "sched" / "sched.py").is_file()
