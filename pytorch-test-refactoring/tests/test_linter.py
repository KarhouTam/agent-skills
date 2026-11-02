"""Regression tests for the AST test linter (scripts/linter.py).

The linter is vendored verbatim from pytorch/pytorch at a pinned SHA; these
tests pin down the vendored behavior the skill depends on.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scripts.linter as linter
from scripts.linter import check_file


def test_undecodable_file_raises_like_upstream(tmp_path):
    """Upstream check_file only catches SyntaxError; a bad encoding raises."""
    import pytest

    path = tmp_path / "test_bad_encoding.py"
    path.write_bytes(
        b"class TestX:\n    def test_a(self):\n        pass\n# caf\xe9\n"
    )

    with pytest.raises(UnicodeDecodeError):
        check_file(str(path))


def test_parse_error_is_reported_for_syntactically_broken_file(tmp_path):
    path = tmp_path / "test_broken.py"
    path.write_text("class TestX(")

    messages = check_file(str(path))

    assert any(m.name == "[parse_error]" for m in messages)


def test_this_class_must_declare_its_own_hw_classification(tmp_path):
    path = tmp_path / "test_own_decl.py"
    path.write_text(
        "class TestA:\n"
        "    hw_classification = HardwareClassification.ACCELERATOR\n"
        "    def test_a(self, device):\n"
        "        pass\n"
    )
    assert [m.name for m in check_file(str(path)) if m.name == "[hw_classification]"] == []


def test_missing_hw_classification_is_flagged(tmp_path):
    path = tmp_path / "test_missing.py"
    path.write_text(
        "class TestA:\n"
        "    def test_a(self):\n"
        "        pass\n"
    )
    assert [m.name for m in check_file(str(path)) if m.name == "[hw_classification]"]


def test_generic_class_gets_accelerator_api_error(tmp_path):
    path = tmp_path / "test_accel.py"
    path.write_text(
        "import torch\n"
        "class TestY:\n"
        "    hw_classification = HardwareClassification.GENERIC\n"
        "    def test_a(self):\n"
        "        assert torch.cuda.is_available()\n"
    )
    names = [m.name for m in check_file(str(path))]
    assert "[accelerator_api]" in names


def test_allowlisted_file_is_skipped(tmp_path, monkeypatch):
    path = tmp_path / "test_skip.py"
    path.write_text(
        "class TestS:\n"
        "    def test_a(self):\n"
        "        pass\n"
    )
    rel_path = os.path.relpath(str(path), linter.REPO_ROOT)
    monkeypatch.setattr(linter, "_allowlist", {rel_path})

    assert check_file(str(path)) == []
