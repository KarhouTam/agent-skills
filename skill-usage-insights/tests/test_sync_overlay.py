"""Tests for sync_skills.py's overlay.json rules.

The overlay mechanism is how public-tier skills get local adaptations without
editing upstream copies; `replaceLine` (added for frontmatter fixes such as
trigger descriptions) shares `insertAfter`'s contract: idempotent, and loud
when the anchor disappears.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import sync_skills  # noqa: E402


def make_skill(tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Old description.\n---\n\n# Demo\n", encoding="utf-8")
    return skill


def test_replace_line_swaps_its_anchor_once_and_is_idempotent(tmp_path):
    skill = make_skill(tmp_path)
    rule = {"file": "SKILL.md", "find": "description: Old description.",
            "text": "description: New description with trigger words."}

    assert sync_skills.replace_line(skill, rule) == "SKILL.md"
    assert "description: New description with trigger words." in (skill / "SKILL.md").read_text()
    assert "Old description" not in (skill / "SKILL.md").read_text()

    assert sync_skills.replace_line(skill, rule) == "SKILL.md (already applied)"


def test_replace_line_fails_loudly_when_the_anchor_is_gone(tmp_path):
    skill = make_skill(tmp_path)
    rule = {"file": "SKILL.md", "find": "description: Renamed upstream.", "text": "x"}
    with pytest.raises(RuntimeError, match="anchor missing"):
        sync_skills.replace_line(skill, rule)


def test_apply_overlay_runs_both_rule_kinds(tmp_path):
    overlay = tmp_path / "overlays" / "demo"
    overlay.mkdir(parents=True)
    (overlay / "overlay.json").write_text(__import__("json").dumps({
        "replaceLine": [{"file": "SKILL.md", "find": "description: Old description.",
                         "text": "description: Replaced."}],
        "insertAfter": [{"file": "SKILL.md", "after": "# Demo", "text": "Inserted line."}],
    }), encoding="utf-8")

    sync_skills.OVERLAYS = tmp_path / "overlays"
    applied = sync_skills.apply_overlay("demo", make_skill(tmp_path))
    body = (tmp_path / "demo" / "SKILL.md").read_text()
    assert "description: Replaced." in body and "Inserted line." in body
    assert any("already applied" not in line for line in applied)


def test_remove_line_drops_its_exact_line_once_and_is_idempotent(tmp_path):
    skill = make_skill(tmp_path)
    rule = {"file": "SKILL.md", "find": "description: Old description."}

    assert sync_skills.remove_line(skill, rule) == "SKILL.md"
    body = (skill / "SKILL.md").read_text()
    assert "description:" not in body
    assert len(body.splitlines()) == 5, "the line goes whole, exactly one line gone"
    assert sync_skills.remove_line(skill, rule) == "SKILL.md (already applied)"


def test_remove_line_never_eats_a_partial_match(tmp_path):
    skill = make_skill(tmp_path)
    sync_skills.remove_line(skill, {"file": "SKILL.md", "find": "description: Old"})
    assert "description: Old description." in (skill / "SKILL.md").read_text()


def test_remove_files_deletes_listed_files_and_skips_absent_ones(tmp_path):
    skill = make_skill(tmp_path)
    (skill / "references").mkdir()
    (skill / "references" / "a.md").write_text("x", encoding="utf-8")

    removed = sync_skills.remove_files(skill, ["references/a.md", "references/b.md"])
    assert removed == ["references/a.md"]
    assert not (skill / "references" / "a.md").exists()

    assert sync_skills.remove_files(skill, ["references/a.md"]) == []
