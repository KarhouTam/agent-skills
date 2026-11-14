from __future__ import annotations

import importlib.util

from conftest import BASE


def load_checker():
    spec = importlib.util.spec_from_file_location("check_run", BASE / "evals" / "check_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verdicts(answer: str, mode: str = "standard") -> dict[str, bool]:
    checker = load_checker()
    checker.results.clear()
    checker.check_code_citations(answer, mode)
    return {label: ok for ok, label in checker.results}


INLINED = """```python
# a/b.py:1-2 · f() —— 为什么这么写
x = 1  # 这条必须是整数
```

## 来源
- a/b.py::f — pkg@1
"""

COORDINATE = """看 `a/b.py:12-14` 就行。

## 来源
- a/b.py::f — pkg@1
"""

CAPTION_ONLY = """```python
# a/b.py:1-2 · f() —— 为什么这么写
x = 1
```

## 来源
- a/b.py::f — pkg@1
"""


def test_inlined_snippet_passes() -> None:
    assert all(verdicts(INLINED).values())


def test_bare_coordinate_fails() -> None:
    assert not verdicts(COORDINATE)["正文 cites code by inlining it, not by `path:line`"]


def test_caption_only_snippet_fails() -> None:
    assert not verdicts(CAPTION_ONLY)["every inlined snippet comments on why, not just on where it came from"]
