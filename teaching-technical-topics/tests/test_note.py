from __future__ import annotations

from pathlib import Path

from conftest import NOTE, run, write_draft, write_material, write_note


def test_migrate_flat_layout_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    (root / "pytorch").mkdir(parents=True)
    (root / "pytorch" / "dynamo.md").write_text(
        "---\nproject: pytorch\ntopic: dynamo\ndate: 2026-10-01\nnutshell: x\n"
        "related: []\nverified_against: t@1\nconfidence: verified\n---\n# D\n",
        encoding="utf-8",
    )
    (root / "pytorch" / "notebooks").mkdir()
    (root / "pytorch" / "notebooks" / "dynamo.py").write_text("print(1)\n", encoding="utf-8")
    (root / ".drafts" / "vllm").mkdir(parents=True)
    (root / ".drafts" / "vllm" / "sched.md").write_text(
        "---\nproject: vllm\ntopic: sched\ndate: 2026-10-02\nnutshell: y\n"
        "related: []\nverified_against: v@1\nconfidence: verified\n---\n# S\n",
        encoding="utf-8",
    )

    run(NOTE, "migrate", root)

    assert (root / "pytorch" / "dynamo" / "note.md").is_file()
    assert (root / "pytorch" / "dynamo" / "dynamo.py").is_file()
    assert (root / ".drafts" / "vllm" / "sched" / "note.md").is_file()
    assert not (root / "pytorch" / "notebooks").exists()
    run(NOTE, "check", root)

    again = run(NOTE, "migrate", root)
    assert "nothing to migrate" in again.stdout
    run(NOTE, "check", root)


def test_index_is_landscape_and_excludes_drafts(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    write_note(root, "pytorch/dynamo", nutshell="Dynamo 抓图。")
    write_note(root, "vllm/sched", nutshell="调度决定 batch。")
    write_material(root, "pytorch/dynamo", "demo.py")
    write_draft(root, "pytorch/inductor", nutshell="Inductor lowering。")
    run(NOTE, "add", root / "vllm" / "sched" / "note.md")
    run(NOTE, "add", root / "pytorch" / "dynamo" / "note.md", "--related", "vllm/sched")

    index = (root / "INDEX.md").read_text(encoding="utf-8")
    assert "# 教学记忆总览" in index
    assert "## 按项目" in index
    assert "[demo.py](./pytorch/dynamo/demo.py)" in index
    assert "pytorch/dynamo — vllm/sched" in index
    assert "thread one" in index
    assert "inductor" not in index  # drafts are never reference material
    run(NOTE, "check", root)

    note_path = root / "pytorch" / "dynamo" / "note.md"
    note_path.write_text(
        note_path.read_text(encoding="utf-8").replace("- thread one", "- thread two"),
        encoding="utf-8",
    )
    stale = run(NOTE, "check", root, check=False)
    assert stale.returncode == 1
    assert "INDEX.md is stale" in stale.stdout
    run(NOTE, "index", root)
    run(NOTE, "check", root)


def test_promote_keeps_materials_and_consumes_draft(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    write_draft(root, "pytorch/inductor")
    write_material(root, "pytorch/inductor", "inductor.py")
    run(NOTE, "index", root)

    run(NOTE, "promote", root / ".drafts" / "pytorch" / "inductor" / "note.md")

    assert (root / "pytorch" / "inductor" / "note.md").is_file()
    assert (root / "pytorch" / "inductor" / "inductor.py").is_file()
    assert not (root / ".drafts" / "pytorch" / "inductor").exists()
    run(NOTE, "check", root)


def test_add_makes_related_links_bidirectional(tmp_path: Path) -> None:
    root = tmp_path / "notes"
    write_note(root, "pytorch/dynamo")
    write_note(root, "vllm/sched")
    run(NOTE, "add", root / "pytorch" / "dynamo" / "note.md", "--related", "vllm/sched")

    dynamo = (root / "pytorch" / "dynamo" / "note.md").read_text(encoding="utf-8")
    sched = (root / "vllm" / "sched" / "note.md").read_text(encoding="utf-8")
    assert "related: [vllm/sched]" in dynamo
    assert "related: [pytorch/dynamo]" in sched
    assert "[vllm/sched](../../vllm/sched/note.md)" in dynamo
    assert "[pytorch/dynamo](../../pytorch/dynamo/note.md)" in sched
    run(NOTE, "check", root)
