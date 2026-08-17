"""Deterministic local test runner for the post-review gate.

Runs the refactored test file in one whole-file invocation and parses the
JUnit XML report for per-test outcomes. The runner resolves a working Python
interpreter before falling back to the current one: conda/venv environments
that can import this repo's source torch are preferred (e.g. pytorch-dev-cpu).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from state import LocalTestFailure, LocalTestResult


DEFAULT_TIMEOUT_SECONDS = 20 * 60

_DEVICE_SUFFIX_RE = re.compile(r"(CPU|CUDA|MPS|XPU|HPU)$")
_INTERPRETER_OVERRIDE = "PYTORCH_TEST_REFACTOR_PYTHON"


def _find_repo_root(file_path: str) -> Path:
    """Resolve the PyTorch repo root for the test file, best-effort."""
    path = Path(file_path).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(path.parent),
            timeout=5,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip())
    except Exception:
        pass

    for parent in (path.parent, *path.parents):
        if (parent / ".git").exists() or (parent / "torch" / "__init__.py").exists():
            return parent
    return path.parent.parent


def _candidate_pythons(repo_root: Path) -> list[str]:
    """Order candidate interpreters: override, conda envs, venvs, sys.executable."""
    candidates: list[str] = []

    override = os.environ.get(_INTERPRETER_OVERRIDE, "").strip()
    if override:
        candidates.append(override)

    home = Path.home()
    conda_env_roots = [
        home / "miniconda3" / "envs",
        home / "anaconda3" / "envs",
        home / "mambaforge" / "envs",
        home / "miniforge3" / "envs",
        Path("/opt/conda/envs"),
    ]
    env_dirs: list[Path] = []
    for root in conda_env_roots:
        if root.is_dir():
            env_dirs.extend(p for p in root.iterdir() if p.is_dir())
    env_dirs.sort(
        key=lambda p: (
            not any(tok in p.name.lower() for tok in ("pytorch", "torch", "dev")),
            p.name,
        )
    )
    for env_dir in env_dirs:
        py = env_dir / "bin" / "python"
        if py.exists():
            candidates.append(str(py))

    for rel in (".venv", "venv", "env"):
        py = repo_root / rel / "bin" / "python"
        if py.exists():
            candidates.append(str(py))

    candidates.append(sys.executable)

    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            ordered.append(candidate)
    return ordered


def _torch_imports(py: str, repo_root: Path) -> bool:
    """Return True if `py` can import torch (the source checkout)."""
    code = "import torch; print(torch.__file__)"
    try:
        proc = subprocess.run(
            [py, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30,
        )
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return False


def resolve_interpreter(file_path: str, repo_root: Path) -> str:
    """Return the first candidate Python that imports torch, else sys.executable."""
    for py in _candidate_pythons(repo_root):
        if _torch_imports(py, repo_root):
            return py
    return sys.executable


def _probe_accelerator(py: str, repo_root: Path) -> bool:
    code = (
        "import torch; "
        "cuda = torch.cuda.is_available(); "
        "xpu = getattr(getattr(torch, 'xpu', None), 'is_available', lambda: False)(); "
        "mps = getattr(getattr(torch.backends, 'mps', None), 'is_available', lambda: False)(); "
        "print('1' if (cuda or xpu or mps) else '0')"
    )
    try:
        proc = subprocess.run(
            [py, "-c", code],
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=30,
        )
        return proc.returncode == 0 and proc.stdout.strip() == "1"
    except Exception:
        return False


def _device_from(classname: str) -> str:
    match = _DEVICE_SUFFIX_RE.search(classname or "")
    return match.group(1).lower() if match else ""


def _classify_whole_run(exit_code: int, stderr: str) -> str:
    lowered = (stderr or "").lower()
    if "killed" in lowered or "out of memory" in lowered:
        return "oom"
    if "segmentation fault" in lowered or "segfault" in lowered:
        return "segfault"
    return "import" if exit_code != 0 else ""


def _parse_junit(
    xml_path: Path, exit_code: int
) -> tuple[dict[str, int], list[LocalTestFailure], str]:
    """Parse JUnit XML into counts + failures; return whole-run failure text."""
    counts = {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "errored": 0,
        "skipped": 0,
        "expected_failures": 0,
        "unexpected_successes": 0,
    }
    failures: list[LocalTestFailure] = []

    try:
        tree = ET.parse(xml_path)
    except Exception:
        return counts, failures, _classify_whole_run(exit_code, "")

    for testcase in tree.iter("testcase"):
        counts["total"] += 1
        classname = testcase.get("classname", "")
        name = testcase.get("name", "")
        node_id = f"{classname}.{name}" if classname else name
        device_type = _device_from(classname)

        failure_el = testcase.find("failure")
        error_el = testcase.find("error")
        skipped_el = testcase.find("skipped")

        if failure_el is not None:
            message = (failure_el.get("message") or "") + "\n" + (failure_el.text or "")
            if "XPASS" in (failure_el.get("message") or "").upper():
                counts["unexpected_successes"] += 1
            else:
                counts["failed"] += 1
                failures.append(
                    LocalTestFailure(
                        test_name=node_id,
                        outcome="FAIL",
                        message=message[:2000],
                        device_type=device_type,
                    )
                )
        elif error_el is not None:
            message = (error_el.get("message") or "") + "\n" + (error_el.text or "")
            counts["errored"] += 1
            failures.append(
                LocalTestFailure(
                    test_name=node_id,
                    outcome="ERROR",
                    message=message[:2000],
                    device_type=device_type,
                )
            )
        elif skipped_el is not None:
            counts["skipped"] += 1
            if "xfail" in (skipped_el.get("type") or "").lower():
                counts["expected_failures"] += 1
        else:
            counts["passed"] += 1

    whole_run_failure = ""
    if counts["total"] == 0 and exit_code != 0:
        whole_run_failure = "import"
    return counts, failures, whole_run_failure


def _run_once(
    py: str,
    test_file: str,
    xml_path: Path,
    repo_root: Path,
    timeout: int,
) -> tuple[int, str, float, bool]:
    """Run one invocation. Returns (exit_code, stderr, duration, timed_out)."""
    cmd = [py, test_file, "--use-pytest", f"--junitxml={xml_path}"]
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(repo_root),
            timeout=timeout,
        )
        return proc.returncode, proc.stderr, time.monotonic() - start, False
    except subprocess.TimeoutExpired:
        return 0, "", time.monotonic() - start, True


def run_local_tests(
    file_path: str,
    report_dir: Path,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> LocalTestResult:
    """Run the file, retry once on whole-run failure, and return a parsed result."""
    repo_root = _find_repo_root(file_path)
    interpreter = resolve_interpreter(file_path, repo_root)
    accelerator_available = _probe_accelerator(interpreter, repo_root)

    test_file_abs = Path(file_path).resolve()
    try:
        test_file = str(test_file_abs.relative_to(repo_root))
    except ValueError:
        test_file = str(test_file_abs)
    xml_path = report_dir / "local_test_results.xml"

    def one_run() -> LocalTestResult:
        xml_path.unlink(missing_ok=True)
        exit_code, stderr, duration, timed_out = _run_once(
            interpreter, test_file, xml_path, repo_root, timeout
        )
        command = " ".join(
            [
                interpreter,
                test_file,
                "--use-pytest",
                f"--junitxml={xml_path}",
            ]
        )
        if timed_out:
            return LocalTestResult(
                file_path=file_path,
                command=command,
                interpreter=interpreter,
                timeout=timeout,
                duration=duration,
                whole_run_failure="timeout",
                accelerator_available=accelerator_available,
            )

        counts, failures, whole_run_failure = _parse_junit(xml_path, exit_code)
        if whole_run_failure == "" and not xml_path.exists():
            whole_run_failure = _classify_whole_run(exit_code, stderr)

        return LocalTestResult(
            file_path=file_path,
            command=command,
            interpreter=interpreter,
            timeout=timeout,
            duration=duration,
            exit_code=exit_code,
            whole_run_failure=whole_run_failure,
            accelerator_available=accelerator_available,
            total=counts["total"],
            passed=counts["passed"],
            failed=counts["failed"],
            errored=counts["errored"],
            skipped=counts["skipped"],
            expected_failures=counts["expected_failures"],
            unexpected_successes=counts["unexpected_successes"],
            failures=failures,
        )

    result = one_run()
    if result.whole_run_failure:
        result = one_run()
    return result
