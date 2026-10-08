"""Regression tests for the AST test linter (scripts/linter.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.linter import check_file


def test_check_file_reports_undecodable_file_instead_of_raising(tmp_path):
    path = tmp_path / "test_bad_encoding.py"
    path.write_bytes(
        b"class TestX:\n    def test_a(self):\n        pass\n# caf\xe9\n"
    )

    messages = check_file(str(path))

    assert any(m.name == "[parse_error]" for m in messages)


def test_inherited_hw_classification_is_accepted(tmp_path):
    path = tmp_path / "test_inherit.py"
    path.write_text(
        "class Base:\n"
        "    hw_classification = HardwareClassification.GENERIC\n"
        "    def test_a(self):\n"
        "        pass\n\n"
        "class Child(Base):\n"
        "    def test_b(self):\n"
        "        pass\n"
    )
    assert [m.name for m in check_file(str(path)) if m.name == "[hw_classification]"] == []


def test_invalid_own_hw_classification_is_still_flagged(tmp_path):
    path = tmp_path / "test_invalid.py"
    path.write_text(
        "class Base:\n"
        "    hw_classification = HardwareClassification.GENERIC\n"
        "    def test_a(self):\n"
        "        pass\n\n"
        "class Child(Base):\n"
        "    hw_classification = 'bogus'\n"
        "    def test_b(self):\n"
        "        pass\n"
    )
    assert [m.name for m in check_file(str(path)) if m.name == "[hw_classification]"]
