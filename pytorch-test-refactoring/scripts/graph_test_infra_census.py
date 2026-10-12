#!/usr/bin/env python3
"""Census of the graph field's test infrastructure.

Regenerates every number quoted in ``reference/graph/test_infra.md`` from a
PyTorch checkout, so the document can be re-verified after HEAD moves.

    python scripts/graph_test_infra_census.py --repo /path/to/pytorch
    python scripts/graph_test_infra_census.py --repo /path/to/pytorch --write

Without ``--write`` the report goes to stdout as markdown. ``--write`` also
refreshes ``reference/graph/test_infra_census.json`` with the same data in
machine-readable form, pinned to the checkout's HEAD commit.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LIST = SKILL_ROOT / "reference" / "graph" / "test_list.txt"
JSON_OUT = SKILL_ROOT / "reference" / "graph" / "test_infra_census.json"

# Family -> predicate. Order matters: aoti is a subset of the inductor directory.
FAMILY_ORDER = ["fx", "dynamo", "inductor", "aoti", "export", "onnx"]


def classify(rel: str) -> str | None:
    if rel.startswith("test/inductor/test_aot_inductor") or rel.startswith(
        "test/inductor/test_aoti_"
    ):
        return "aoti"
    if rel.startswith("test/inductor/") or rel in (
        "test/distributed/test_inductor_collectives.py",
        "test/distributed/test_inductor_compile_collectives.py",
    ):
        return "inductor"
    if rel.startswith("test/dynamo/") or rel == "test/distributed/test_dynamo_distributed.py":
        return "dynamo"
    if rel.startswith("test/fx/") or re.fullmatch(r"test/test_fx[A-Za-z_]*\.py", rel):
        return "fx"
    if rel.startswith("test/export/"):
        return "export"
    if rel.startswith("test/onnx/"):
        return "onnx"
    return None


# Coupling / mechanism vocabulary, keyed by a safe identifier and mapped to
# (label, regex). Every pattern is line-counted, so a line matching several
# patterns counts once for each.
PATTERNS: dict[str, tuple[str, str]] = {
    "onlyCUDA": ("@onlyCUDA", r"@onlyCUDA\b"),
    "onlyCPU": ("@onlyCPU", r"@onlyCPU\b"),
    "onlyXPU": ("@onlyXPU", r"@onlyXPU\b"),
    "onlyAccelerator": ("@onlyAccelerator", r"@onlyAccelerator\b"),
    "onlyOn": ("@onlyOn", r"@onlyOn\b"),
    "onlyNativeDeviceTypes": ("@onlyNativeDeviceTypes", r"@onlyNativeDeviceTypes\b"),
    "only_for": ("only_for=", r"only_for\s*="),
    "skipCUDAIf": ("@skipCUDAIf", r"@skipCUDAIf\b"),
    "skipCUDAIfRocm": ("@skipCUDAIfRocm", r"@skipCUDAIfRocm\b"),
    "skipXPU": ("@skipXPU", r"@skipXPU\b"),
    "skipXPUIf": ("@skipXPUIf", r"@skipXPUIf\b"),
    "skipMPS": ("@skipMPS", r"@skipMPS\b"),
    "skipIfRocm": ("@skipIfRocm", r"@skipIfRocm\b"),
    "requires_gpu": ("@requires_gpu", r"@requires_gpu\b"),
    "requires_cuda_and_triton": (
        "@requires_cuda_and_triton",
        r"@requires_cuda_and_triton\b",
    ),
    "requires_triton": ("@requires_triton", r"@requires_triton\b"),
    "cuda_is_available": ("torch.cuda.is_available()", r"torch\.cuda\.is_available\(\)"),
    "torch_cuda_attr": ("torch.cuda.*", r"torch\.cuda\."),
    "dot_cuda_call": (".cuda()", r"\.cuda\(\)"),
    "cuda_string": ("\"cuda\" literal", r"[\"']cuda[\"']"),
    "device_cuda_kwarg": ("device=\"cuda", r"device\s*=\s*[\"']cuda"),
    "TEST_CUDA": ("TEST_CUDA", r"\bTEST_CUDA\b"),
    "GPU_TYPE": ("GPU_TYPE", r"\bGPU_TYPE\b"),
    "HAS_GPU": ("HAS_GPU", r"\bHAS_GPU\b"),
    "HAS_CUDA_AND_TRITON": ("HAS_CUDA_AND_TRITON", r"\bHAS_CUDA_AND_TRITON\b"),
    "torch_xpu": ("torch.xpu", r"torch\.xpu\b"),
    "mps": ("mps", r"\bmps\b"),
    "mtia": ("mtia", r"\bmtia\b"),
    "device_type_instantiation": (
        "instantiate_device_type_tests",
        r"\binstantiate_device_type_tests\b",
    ),
    "parametrized_instantiation": (
        "instantiate_parametrized_tests",
        r"\binstantiate_parametrized_tests\b",
    ),
    "parametrize_decorator": ("@parametrize", r"@parametrize\b"),
    "make_test_cls_with_patches": (
        "make_test_cls_with_patches",
        r"\bmake_test_cls_with_patches\b",
    ),
    "make_test_cls_with_mocked_export": (
        "make_test_cls_with_mocked_export",
        r"\bmake_test_cls_with_mocked_export\b",
    ),
    "copy_tests": ("copy_tests", r"\bcopy_tests\b"),
    "add_test_failures": ("add_test_failures", r"\badd_test_failures\b"),
    "TestFailure": ("TestFailure", r"\bTestFailure\b"),
    "hw_classification": ("hw_classification", r"\bhw_classification\b"),
    "ops_decorator": ("@ops", r"@ops\b"),
    "skipOps": ("skipOps", r"\bskipOps\b"),
    "DecorateInfo": ("DecorateInfo", r"\bDecorateInfo\b"),
    "slowTest_decorator": ("@slowTest", r"@slowTest\b"),
    "expectedFailure_decorator": ("@expectedFailure", r"@expectedFailure\b"),
}

# Import lines reveal the base class behind an alias, which AST base expressions
# cannot (e.g. `from torch._dynamo.test_case import TestCase as TorchTestCase`).
BASE_IMPORTS: dict[str, tuple[str, str]] = {
    "dynamo_test_case": (
        "torch._dynamo.test_case.TestCase",
        r"from torch\._dynamo\.test_case import",
    ),
    "inductor_test_case": (
        "torch._inductor.test_case.TestCase",
        r"from torch\._inductor\.test_case import",
    ),
    "common_utils": (
        "torch.testing._internal.common_utils",
        r"from torch\.testing\._internal\.common_utils import",
    ),
    "jit_utils": ("torch.testing._internal.jit_utils", r"from torch\.testing\._internal\.jit_utils import"),
    "dynamo_testing": ("torch._dynamo.testing", r"from torch\._dynamo\.testing import"),
    "inductor_utils": (
        "torch.testing._internal.inductor_utils",
        r"from torch\.testing\._internal\.inductor_utils import",
    ),
    "unittest": ("unittest", r"^import unittest\b|^from unittest import"),
    "inductor_utils_mod": ("torch._inductor.utils", r"from torch\._inductor\.utils import"),
}

# Only base expressions that look like harness bases are reported; everything
# else in an AST base list is a class under test (nn.Module and friends).
HARNESS_BASE_RX = re.compile(r"(TestCase|TestBase|TestMixin)$|^TestCase$|^JitTestCase$")

# Directories whose empty filenames form the compiled-test sentinel ledgers.
SENTINEL_DIRS = [
    "test/dynamo_skips",
    "test/dynamo_expected_failures",
    "test/inductor_skips",
    "test/inductor_expected_failures",
    "test/compiled_autograd_skips",
]

# Families present in reference/graph/test_list.txt that this document does not cover.
OUT_OF_SCOPE = {
    "jit": r"^test/(jit/|test_jit|test_jiterator|test_ops_jit|test_cpp_extensions_jit|test_file_check)",
    "tensorexpr": r"^test/test_tensorexpr",
    "lazy": r"^test/lazy/",
    "cpython": r"^test/cpython/",
    "functorch": r"^test/functorch/",
    "higher_order_ops": r"^test/higher_order_ops/",
    "top-level graph IR": r"^test/(test_proxy_tensor|test_functionalization|test_decomp|"
    r"test_prims|test_fake_tensor|test_meta|test_dispatch|test_hop_infra|"
    r"test_dynamic_shapes|test_sympy_utils|test_model_exports_to_core_aten)",
    "top-level compile utilities": r"^test/(test_compile_benchmark_util|test_triton_utils)\.py$",
    "cpp/jit": r"^test/cpp/jit/",
}


def head_sha(repo: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def routed_tests(repo: Path) -> set[str] | None:
    """The runner's own test list, read from the checkout rather than re-derived.

    Entries are relative to ``test/`` (e.g. ``dynamo/test_compile``), matching
    ``test/run_test.py``'s prefix filters.
    """
    script = (
        "import sys; sys.path.insert(0, '.');"
        "from tools.testing.discover_tests import TESTS; print('\\n'.join(TESTS))"
    )
    try:
        out = subprocess.run(
            [sys.executable, "-c", script], cwd=repo, capture_output=True, text=True, check=True
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def is_routed(rel: str, routed: set[str] | None) -> bool | None:
    if routed is None:
        return None
    stripped = rel[len("test/") :] if rel.startswith("test/") else rel
    return stripped[:-3] in routed


def count_bases(text: str) -> dict[str, int]:
    """Base-class expressions as written, via AST."""
    counts: dict[str, int] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", SyntaxWarning)
        try:
            tree = ast.parse(text)
        except (SyntaxError, ValueError):
            return counts
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                try:
                    name = ast.unparse(base)
                except Exception:  # pragma: no cover - defensive
                    continue
                if not HARNESS_BASE_RX.search(name):
                    continue
                counts[name] = counts.get(name, 0) + 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="/root/pytorch", help="PyTorch checkout to inspect")
    ap.add_argument("--list", default=str(DEFAULT_LIST), help="field test list")
    ap.add_argument("--write", action="store_true", help="refresh the JSON census")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    list_path = Path(args.list)
    if not (repo / "torch" / "testing" / "_internal" / "common_utils.py").exists():
        print(f"error: {repo} does not look like a PyTorch checkout", file=sys.stderr)
        return 2

    raw_lines = list_path.read_text().splitlines()
    listed = [ln.strip() for ln in raw_lines if ln.strip()]
    unique = sorted(set(listed))
    missing = [p for p in unique if not (repo / p).is_file()]

    by_family: dict[str, list[str]] = {f: [] for f in FAMILY_ORDER}
    out_of_scope: dict[str, list[str]] = {k: [] for k in OUT_OF_SCOPE}
    for rel in unique:
        fam = classify(rel)
        if fam:
            by_family[fam].append(rel)
            continue
        for name, rx in OUT_OF_SCOPE.items():
            if re.search(rx, rel):
                out_of_scope[name].append(rel)
                break
        else:
            out_of_scope.setdefault("unclassified", []).append(rel)

    routed = routed_tests(repo)

    # Single pass per file: line stats for every pattern, LOC, AST bases, imports.
    combined = re.compile("|".join(f"(?P<{k}>{rx})" for k, (_, rx) in PATTERNS.items()))
    base_combined = re.compile(
        "|".join(f"(?P<{k}>{rx})" for k, (_, rx) in BASE_IMPORTS.items())
    )
    per_file: dict[str, dict] = {}
    for fam, files in by_family.items():
        for rel in files:
            path = repo / rel
            if not path.is_file():
                continue
            text = path.read_text(errors="replace")
            hits: dict[str, int] = {}
            bases: dict[str, int] = {}
            imports: dict[str, int] = {}
            for line in text.splitlines():
                for m in combined.finditer(line):
                    hits[m.lastgroup] = hits.get(m.lastgroup, 0) + 1
                for m in base_combined.finditer(line):
                    imports[m.lastgroup] = imports.get(m.lastgroup, 0) + 1
            for name, n in count_bases(text).items():
                bases[name] = bases.get(name, 0) + n
            per_file[rel] = {
                "family": fam,
                "loc": len(text.splitlines()),
                "hits": hits,
                "bases": bases,
                "imports": imports,
            }

    def agg(files: list[str], key: str) -> dict[str, tuple[int, int]]:
        total: dict[str, list[int]] = {}
        for rel in files:
            for name, n in per_file.get(rel, {}).get(key, {}).items():
                slot = total.setdefault(name, [0, 0])
                slot[0] += n
                slot[1] += 1
        return {k: (v[0], v[1]) for k, v in sorted(total.items(), key=lambda kv: -kv[1][0])}

    families = {}
    for fam, files in by_family.items():
        present = [f for f in files if f in per_file]
        families[fam] = {
            "files": len(files),
            "routed": None
            if routed is None
            else sum(1 for f in files if is_routed(f, routed)),
            "loc": sum(per_file[f]["loc"] for f in present),
            "mechanisms": agg(present, "hits"),
            "bases": agg(present, "bases"),
            "imports": agg(present, "imports"),
        }

    ledgers = {}
    for d in SENTINEL_DIRS:
        path = repo / d
        if not path.is_dir():
            ledgers[d] = {"present": False}
            continue
        names = [p.name for p in path.iterdir() if p.is_file()]
        device_keyed = [n for n in names if re.search(r"_(cpu|cuda|xpu|mps|mtia)(_|$)", n)]
        ledgers[d] = {
            "present": True,
            "entries": len(names),
            "device_keyed": len(device_keyed),
            "sample": sorted(device_keyed)[:5],
        }

    cuda_markers = [
        "torch_cuda_attr",
        "dot_cuda_call",
        "onlyCUDA",
        "skipCUDAIf",
        "cuda_is_available",
    ]
    worst = []
    for rel, info in per_file.items():
        markers = sum(info["hits"].get(p, 0) for p in cuda_markers)
        if markers:
            loc = max(info["loc"], 1)
            worst.append((markers, round(1000 * markers / loc, 1), rel, info["family"]))
    worst.sort(reverse=True)

    data = {
        "repo": str(repo),
        "head_sha": head_sha(repo),
        "list_file": str(list_path),
        "list_lines": len(raw_lines),
        "list_unique": len(unique),
        "list_missing": missing,
        "list_duplicates": sorted({p for p in listed if listed.count(p) > 1}),
        "routed_available": routed is not None,
        "routed": None if routed is None else sum(1 for f in unique if is_routed(f, routed)),
        "families": families,
        "out_of_scope": {k: len(v) for k, v in sorted(out_of_scope.items())},
        "unclassified": out_of_scope.get("unclassified", []),
        "not_routed": []
        if routed is None
        else [f for f in unique if is_routed(f, routed) is False],
        "sentinel_ledgers": ledgers,
        "worst_cuda_files": [
            {"file": r, "family": f, "markers": m, "per_1k_loc": d} for m, d, r, f in worst[:15]
        ],
    }

    if args.write:
        JSON_OUT.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        print(f"wrote {JSON_OUT}", file=sys.stderr)

    print(render(data))
    return 0


def render(data: dict) -> str:
    sha = data["head_sha"]
    out = [f"# Graph-mode test infrastructure census", "", f"- repo: `{data['repo']}` @ `{sha}`"]
    out.append(f"- field list: `{data['list_file']}`")
    out.append(
        f"- listed paths: {data['list_lines']} lines / {data['list_unique']} unique / "
        f"{len(data['list_missing'])} missing on disk"
    )
    if data["routed_available"]:
        out.append(f"- routed by `tools/testing/discover_tests.py`: {data['routed']}")
    out.append("")
    out.append("## Families in scope")
    out.append("")
    out.append("| family | listed files | routed | LOC |")
    out.append("|---|---:|---:|---:|")
    for fam, info in data["families"].items():
        routed = "-" if info["routed"] is None else info["routed"]
        out.append(f"| {fam} | {info['files']} | {routed} | {info['loc']} |")
    out.append("")
    out.append("## Out of scope")
    out.append("")
    out.append("| family | listed files |")
    out.append("|---|---:|")
    for fam, n in data["out_of_scope"].items():
        out.append(f"| {fam} | {n} |")
    out.append("")
    for fam, info in data["families"].items():
        out.append(f"## {fam} - generation and coupling mechanisms (lines, files)")
        out.append("")
        for name, (lines, files) in info["mechanisms"].items():
            label = PATTERNS[name][0] if name in PATTERNS else name
            out.append(f"- `{label}`: {lines} lines / {files} files")
        out.append("")
        out.append(f"## {fam} - base classes (AST expressions, count)")
        out.append("")
        for name, (count, _) in list(info["bases"].items())[:12]:
            out.append(f"- `{name}`: {count}")
        out.append("")
        out.append(f"## {fam} - harness imports (files)")
        out.append("")
        for name, (files, _) in info["imports"].items():
            label = BASE_IMPORTS[name][0] if name in BASE_IMPORTS else name
            out.append(f"- `{label}`: {files} files")
        out.append("")
    out.append("## Sentinel ledgers")
    out.append("")
    out.append("| ledger | entries | device-keyed |")
    out.append("|---|---:|---:|")
    for d, info in data["sentinel_ledgers"].items():
        if info.get("present"):
            out.append(f"| `{d}` | {info['entries']} | {info['device_keyed']} |")
        else:
            out.append(f"| `{d}` | absent | - |")
    out.append("")
    out.append("## Highest hardcoded-CUDA concentration")
    out.append("")
    out.append("| file | family | markers | per 1k LOC |")
    out.append("|---|---|---:|---:|")
    for row in data["worst_cuda_files"]:
        out.append(
            f"| `{row['file']}` | {row['family']} | {row['markers']} | {row['per_1k_loc']} |"
        )
    out.append("")
    if data["list_missing"]:
        out.append("## Paths in the field list that do not exist on disk")
        out.append("")
        for p in data["list_missing"]:
            out.append(f"- `{p}`")
        out.append("")
    if data["not_routed"]:
        in_scope = [p for p in data["not_routed"] if classify(p)]
        out_scope = [p for p in data["not_routed"] if not classify(p)]
        out.append("## Listed paths the runner does not execute")
        out.append("")
        out.append(
            f"Out of scope: {len(out_scope)} (see the out-of-scope table). "
            f"In scope: {len(in_scope)}."
        )
        out.append("")
        for p in in_scope:
            out.append(f"- `{p}`")
        out.append("")
    if data["unclassified"]:
        out.append("## Listed paths belonging to no known family")
        out.append("")
        for p in data["unclassified"]:
            out.append(f"- `{p}`")
        out.append("")
    if data["list_duplicates"]:
        out.append("## Duplicate entries in the field list")
        out.append("")
        for p in data["list_duplicates"]:
            out.append(f"- `{p}`")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    raise SystemExit(main())
