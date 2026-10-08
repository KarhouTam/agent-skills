"""Regression tests for deterministic verification checks (scripts/verify.py)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts import verify


def test_external_refs_ignores_already_renamed_files(tmp_path, monkeypatch):
    """A renamed class keeps the old name as a prefix; migrated files are not stale."""
    monkeypatch.chdir(tmp_path)
    for directory in (verify.DYNAMO_SKIPS_DIR, verify.DYNAMO_EXPECTED_FAILURES_DIR):
        target = Path(directory)
        target.mkdir(parents=True)
        (target / "TestShapeOpsCUDA.test_foo").write_text("x")
        (target / "TestShapeOpsDeviceCUDA.test_foo").write_text("y")

    source = Path("test/test_shape_ops.py")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        "class TestShapeOpsDevice(TestCase):\n"
        "    def test_foo(self, device):\n"
        "        pass\n"
    )

    result = verify._check_external_refs(str(source), ["TestShapeOps"])
    assert "TestShapeOpsDeviceCUDA.test_foo" not in result.details
    assert "TestShapeOpsCUDA.test_foo" in result.details


def test_stale_patterns_only_flags_module_level_device_type(tmp_path):
    source = tmp_path / "test_x.py"

    source.write_text(
        "class TestX(TestCase):\n"
        "    def test_a(self, device):\n"
        "        device_type = device.type\n"
    )
    assert verify._check_stale_patterns(str(source)).passed

    source.write_text('device_type = "cuda"\n\nclass TestX(TestCase):\n    pass\n')
    assert not verify._check_stale_patterns(str(source)).passed

    source.write_text('device_type == "cuda"\n\nclass TestX(TestCase):\n    pass\n')
    assert verify._check_stale_patterns(str(source)).passed


def test_find_class_end_stops_at_module_level_code():
    lines = [
        "class TestXCUDA(TestCase):",
        "    def test_a(self):",
        "        pass",
        "x = torch.randn(3).cuda()",
        "def helper():",
        "    pass",
    ]
    assert verify._find_class_end(lines, 0) == 3


def test_class_split_matches_full_method_name(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "analyst_report.json").write_text(
        json.dumps(
            {
                "new_classes": [
                    {"name": "TestFooCPU", "tests": ["test_x"], "strategy": "cpu_only"}
                ]
            }
        )
    )
    source = tmp_path / "test_cs.py"
    source.write_text(
        "class TestFoo(TestCase):\n"
        "    def test_x_extended(self):\n"
        "        pass\n\n"
        "class TestFooCPU(TestCase):\n"
        "    def test_x(self):\n"
        "        pass\n"
    )
    result = verify._check_class_split(str(source), ["TestFoo"], workspace=workspace)
    assert result.passed, result.details


def test_check_imports_ignores_comments_and_identifiers(tmp_path):
    source = tmp_path / "test_imports.py"

    source.write_text("# gated on TEST_CUDA\ndef test_onlyOnce():\n    pass\n")
    assert verify._check_imports(str(source)).passed

    source.write_text(
        "from torch.testing._internal.common_utils import TEST_CUDA\n"
    )
    assert not verify._check_imports(str(source)).passed
