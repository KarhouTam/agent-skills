from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

BASE = Path(__file__).resolve().parents[1]
NOTE = BASE / "scripts" / "note.py"
SYNC = BASE / "scripts" / "sync.py"


def run(script: Path, *args: object, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise AssertionError(
            f"{script.name} {' '.join(map(str, args))} failed:\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


def note_text(project: str, topic: str, nutshell: str = "一句话。", related: str = "[]") -> str:
    return (
        "---\n"
        f"project: {project}\n"
        f"topic: {topic}\n"
        "date: 2026-11-01\n"
        f"nutshell: {nutshell}\n"
        f"related: {related}\n"
        "verified_against: test@1\n"
        "confidence: verified\n"
        "---\n"
        f"# {topic}\n\n"
        "## 待深入\n"
        "- thread one\n\n"
        "## 已建立的连接\n"
    )


def write_note(root: Path, nid: str, **kwargs: str) -> Path:
    project, topic = nid.split("/")
    topic_dir = root / project / topic
    topic_dir.mkdir(parents=True)
    path = topic_dir / "note.md"
    path.write_text(note_text(project, topic, **kwargs), encoding="utf-8")
    return path


def write_draft(root: Path, nid: str, **kwargs: str) -> Path:
    project, topic = nid.split("/")
    draft_dir = root / ".drafts" / project / topic
    draft_dir.mkdir(parents=True)
    path = draft_dir / "note.md"
    path.write_text(note_text(project, topic, **kwargs), encoding="utf-8")
    return path


def write_material(root: Path, nid: str, name: str, content: str = "demo\n") -> Path:
    project, topic = nid.split("/")
    topic_dir = root / project / topic
    topic_dir.mkdir(parents=True, exist_ok=True)
    path = topic_dir / name
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_AUTHOR_NAME", "tt")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "tt@example.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "tt")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "tt@example.com")
