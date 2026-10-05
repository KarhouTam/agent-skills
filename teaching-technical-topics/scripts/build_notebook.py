#!/usr/bin/env python3
"""Build a valid, *executed* Jupyter notebook from a percent-format Python script.

Hand-writing .ipynb JSON is error-prone (cell ids, nbformat version, escaping) and a
notebook with empty outputs teaches nothing. Write the demo as an ordinary .py that
stays readable and diffable, then convert and run it here.

Percent format (jupytext-compatible subset):
    # %% [markdown]      start a markdown cell; leading "# " is stripped
    # %%                 start a code cell

Usage:
    python3 build_notebook.py <notes>/<project>/notebooks/<topic>.py [--link-dir DIR]
    python3 build_notebook.py <notes>/<project>/notebooks/<topic>.py --no-execute
    python3 build_notebook.py --check <notes>/<project>/notebooks/<topic>.ipynb

Exit codes:
    0  built (and executed, unless --no-execute / no executor available)
    1  bad usage, parse error, or the demo raised while executing
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

MARKER = "# %%"


def split_cells(text: str) -> list[tuple[str, str]]:
    """Split percent-format source into (cell_type, source) pairs."""
    cells: list[tuple[str, str]] = []
    kind: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if kind is None:
            return
        body = "\n".join(buf).strip("\n")
        if kind == "markdown":
            body = "\n".join(
                line[2:] if line.startswith("# ") else line[1:] if line == "#" else line
                for line in body.split("\n")
            )
        cells.append((kind, body))

    for line in text.split("\n"):
        if line.startswith(MARKER) and line[len(MARKER):].strip() in ("", "[markdown]"):
            flush()
            kind = "markdown" if "markdown" in line else "code"
            buf = []
            continue
        if kind is None:
            if line.strip():
                raise SystemExit(f"error: content before the first '{MARKER}' marker: {line!r}")
            continue
        buf.append(line)
    flush()
    return cells


def to_notebook(cells: list[tuple[str, str]]) -> dict:
    return {
        "cells": [
            {
                "cell_type": kind,
                "id": f"cell-{i}",
                "metadata": {},
                "source": source.splitlines(keepends=True),
                **({"outputs": [], "execution_count": None} if kind == "code" else {}),
            }
            for i, (kind, source) in enumerate(cells)
        ],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def fail(path: str, why: str) -> None:
    raise SystemExit(f"error: {path}: {why}")


def check(path: str) -> tuple[int, int]:
    """Validate an .ipynb. Returns (cell_count, executed_code_cell_count)."""
    try:
        with open(path) as fh:
            nb = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        fail(path, f"not readable as JSON ({exc})")
    if nb.get("nbformat") != 4:
        fail(path, f"nbformat is {nb.get('nbformat')!r}, expected 4")
    if not nb.get("cells"):
        fail(path, "no cells")
    executed = 0
    for i, cell in enumerate(nb["cells"]):
        if cell.get("cell_type") not in ("code", "markdown"):
            fail(path, f"cell {i} has bad cell_type {cell.get('cell_type')!r}")
        if not cell.get("id"):
            fail(path, f"cell {i} is missing an id")
        if not isinstance(cell.get("source"), list):
            fail(path, f"cell {i} source must be a list of lines")
        if cell["cell_type"] == "code" and cell.get("execution_count") is not None:
            executed += 1
    return len(nb["cells"]), executed


def execute(path: str, timeout: int) -> str:
    """Run the notebook in place, embedding outputs. Returns a status string."""
    try:
        import nbformat
        from nbclient import NotebookClient
    except ImportError as exc:
        return f"NOT EXECUTED ({exc.name} not installed) — the demo is unverified"
    try:
        nb = nbformat.read(path, as_version=4)
        NotebookClient(nb, timeout=timeout, kernel_name="python3", allow_errors=False).execute()
    except Exception as exc:  # CellExecutionError, NoSuchKernel, ...
        first = str(exc).strip().splitlines()
        detail = "\n".join(first[:12]) if first else exc.__class__.__name__
        print(f"error: demo failed while executing:\n{detail}", file=sys.stderr)
        raise SystemExit(1)
    nbformat.write(nb, path)
    return "executed"


def link(target: str, link_dir: str, force: bool) -> str:
    os.makedirs(link_dir, exist_ok=True)
    dest = os.path.join(link_dir, os.path.basename(target))
    if os.path.islink(dest) and os.path.realpath(dest) == os.path.realpath(target):
        return dest
    if os.path.lexists(dest):
        if not force:
            raise SystemExit(f"error: {dest} exists; pass --force to replace it")
        os.remove(dest)
    try:
        os.symlink(os.path.relpath(os.path.realpath(target), os.path.realpath(link_dir)), dest)
    except OSError:
        shutil.copy2(target, dest)  # filesystems without symlinks
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help=".py source to convert, or .ipynb to verify with --check")
    ap.add_argument("--check", action="store_true", help="validate an existing .ipynb instead of building")
    ap.add_argument("--link-dir", help="directory to symlink the notebook into (e.g. the workspace)")
    ap.add_argument("--force", action="store_true", help="replace an existing file at the symlink path")
    ap.add_argument("--no-execute", action="store_true", help="build only; leave outputs empty")
    ap.add_argument("--timeout", type=int, default=600, help="per-cell execution timeout in seconds")
    args = ap.parse_args()

    if args.check:
        cells, executed = check(args.path)
        suffix = f"{executed} executed" if executed else "NO OUTPUTS — not executed"
        print(f"OK {args.path}: {cells} cells, nbformat 4.5, {suffix}")
        return
    if not args.path.endswith(".py"):
        raise SystemExit("error: source must be a .py file in percent format")

    with open(args.path) as fh:
        cells = split_cells(fh.read())
    if not cells:
        raise SystemExit(f"error: {args.path} has no '{MARKER}' cells")

    out = args.path[: -len(".py")] + ".ipynb"
    with open(out, "w") as fh:
        json.dump(to_notebook(cells), fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"built {out} ({len(cells)} cells)")

    status = "not executed (--no-execute)" if args.no_execute else execute(out, args.timeout)
    if args.link_dir:
        print(f"linked {link(out, args.link_dir, args.force)}")
    _, executed = check(out)
    print(f"{status}; {executed} code cells with outputs")
    if not executed:
        print("WARNING: the demo has no outputs — say so in the answer rather than implying it ran.")


if __name__ == "__main__":
    sys.exit(main())
