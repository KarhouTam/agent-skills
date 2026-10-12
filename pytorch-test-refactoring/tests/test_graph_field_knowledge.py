"""Tests that keep the graph field's list and knowledge document honest.

The ADR (docs/adr/0001-graph-field-knowledge-model.md) makes the census the
contract for every number in reference/graph/test_infra.md, and the list the
field's source of truth. These tests enforce both without needing a PyTorch
checkout.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SKILL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import graph_test_infra_census as census  # noqa: E402

GRAPH_DIR = SKILL_ROOT / "reference" / "graph"
LIST_FILE = GRAPH_DIR / "test_list.txt"
DOC_FILE = GRAPH_DIR / "test_infra.md"
CENSUS_FILE = GRAPH_DIR / "test_infra_census.json"

FAMILIES = ["fx", "dynamo", "inductor", "aoti", "export", "onnx"]


def test_field_list_is_clean():
    raw = LIST_FILE.read_text()
    lines = raw.splitlines()
    paths = [line.strip() for line in lines if line.strip()]

    assert raw.endswith("\n"), "field list must end with a newline"
    assert all(line.strip() == line for line in lines if line.strip()), "trailing whitespace"
    assert len(paths) == len(set(paths)), "duplicate entries in the field list"
    assert all(p.endswith(".py") for p in paths), "non-Python entry in the field list"


def test_every_listed_path_is_classified():
    paths = [line.strip() for line in LIST_FILE.read_text().splitlines() if line.strip()]
    unclassified = []
    for rel in paths:
        if census.classify(rel):
            continue
        if any(re.search(rx, rel) for rx in census.OUT_OF_SCOPE.values()):
            continue
        unclassified.append(rel)
    assert unclassified == [], f"listed paths in no known family: {unclassified}"


def test_doc_numbers_match_census():
    doc = DOC_FILE.read_text()
    data = json.loads(CENSUS_FILE.read_text())

    rows = re.findall(r"^\| `([^`]+)` \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \| (\d+) \|$", doc, re.M)
    label_to_key = {label: key for key, (label, _) in census.PATTERNS.items()}
    checked = 0
    for label, *values in rows:
        key = label_to_key.get(label)
        if key is None:
            continue
        for family, value in zip(FAMILIES, values):
            expected = data["families"][family]["mechanisms"].get(key, [0, 0])[0]
            assert int(value) == expected, (
                f"{label} in {family}: document says {value}, census says {expected}"
            )
            checked += 1
    assert checked > 80, f"only {checked} coupling cells cross-checked"


def test_sentinel_and_family_tables_match_census():
    doc = DOC_FILE.read_text()
    data = json.loads(CENSUS_FILE.read_text())

    for family in FAMILIES:
        info = data["families"][family]
        row = re.search(rf"^\| `{family}` \| .+? \| (\d+) \| (\d+) \| (\d+) \|$", doc, re.M)
        assert row, f"no family table row for {family}"
        assert (int(row.group(1)), int(row.group(2)), int(row.group(3))) == (
            info["files"],
            info["routed"],
            info["loc"],
        ), f"family table out of date for {family}"

    for name, info in data["sentinel_ledgers"].items():
        row = re.search(rf"^\| `{name}` \| (\d+) \| (\d+) \|$", doc, re.M)
        assert row, f"no sentinel table row for {name}"
        assert (int(row.group(1)), int(row.group(2))) == (info["entries"], info["device_keyed"])
