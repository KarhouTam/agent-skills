"""install_hooks.py merges our handlers into every harness it finds, and nothing else.

The installer has no harness flag: it looks for Claude Code, Codex and dsh under
their own config roots and skips whatever is not there. Every path here is
redirected into tmp_path, so a test can never touch the real ones.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import install_hooks  # noqa: E402

FOREIGN = {"type": "command", "command": "~/bin/notify.sh"}


@pytest.fixture(autouse=True)
def roots(tmp_path, monkeypatch):
    """One throwaway root per harness; nothing exists until a test creates it."""
    dirs = {name: tmp_path / name for name in ("claude", "codex", "dsh")}
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(dirs["claude"]))
    monkeypatch.setenv("CODEX_HOME", str(dirs["codex"]))
    monkeypatch.setenv("DSH_HOME", str(dirs["dsh"]))
    for path in dirs.values():
        path.mkdir()
    return dirs


def claude_settings_with_foreign_hooks(roots):
    path = roots["claude"] / "settings.json"
    path.write_text(json.dumps({
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [FOREIGN]}], "PreToolUse": [{"matcher": "Bash", "hooks": [FOREIGN]}]},
    }))
    return path


def test_one_run_wires_every_harness_it_finds(roots):
    path = claude_settings_with_foreign_hooks(roots)
    # Codex and dsh roots exist, so both are targets; dsh has no bridge plugin here.
    assert install_hooks.main([]) == 0

    first = json.loads(path.read_text())
    assert first["model"] == "opus"
    assert first["hooks"]["Stop"][0]["hooks"] == [FOREIGN]
    assert first["hooks"]["PreToolUse"][0] == {"matcher": "Bash", "hooks": [FOREIGN]}
    ours = [g for g in first["hooks"]["PreToolUse"] if g.get("matcher") == "Skill"]
    assert len(ours) == 1 and str(install_hooks.COLLECT) in ours[0]["hooks"][0]["command"]
    assert set(json.loads(install_hooks.TEMPLATE.read_text())["hooks"]) <= set(first["hooks"])

    claude, codex = roots["claude"] / "settings.json", roots["codex"] / "hooks.json"
    assert claude.exists() and codex.exists(), "both harnesses were wired by one run"
    assert str(install_hooks.CODEX_COLLECT) in codex.read_text()
    assert not (roots["dsh"] / "cordis.patch.yml").exists(), "no bridge plugin, so no patch"
    assert list(roots["claude"].glob("settings.json.bak-*")), "the original was backed up"


def test_second_run_changes_nothing(roots):
    claude_settings_with_foreign_hooks(roots)
    assert install_hooks.main([]) == 0
    snapshot = {p: p.read_text() for p in roots["claude"].glob("*.json")}
    snapshot.update({p: p.read_text() for p in roots["codex"].glob("*.json")})
    assert install_hooks.main([]) == 0
    for path, text in snapshot.items():
        assert path.read_text() == text, path


def test_status_reports_every_harness(roots, capsys):
    claude_settings_with_foreign_hooks(roots)
    assert install_hooks.main(["--status"]) == 1, "not wired yet"
    assert install_hooks.main([]) == 0
    assert install_hooks.main(["--status"]) == 0
    out = capsys.readouterr().out
    assert "claude-code" in out and "codex" in out and "dsh: skipped" in out


def test_no_harness_found_is_not_success(tmp_path, monkeypatch, capsys):
    for var in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "DSH_HOME"):
        monkeypatch.setenv(var, str(tmp_path / "missing" / var))
    assert install_hooks.main(["--status"]) == 1
    assert "no harness found" in capsys.readouterr().err


def test_uninstall_restores_the_original(roots):
    path = claude_settings_with_foreign_hooks(roots)
    original = json.loads(path.read_text())
    install_hooks.main([])
    assert (roots["codex"] / "hooks.json").exists()
    assert install_hooks.main(["--uninstall"]) == 0
    assert json.loads(path.read_text()) == original
    assert not json.loads((roots["codex"] / "hooks.json").read_text()).get("hooks")
    assert install_hooks.main(["--status"]) == 1


def test_refuses_invalid_json(roots):
    path = roots["claude"] / "settings.json"
    path.write_text("{ not json")
    assert install_hooks.main([]) == 1
    assert path.read_text() == "{ not json"


def test_settings_flag_moves_only_the_claude_target(roots, tmp_path):
    other = tmp_path / "elsewhere" / "settings.local.json"
    assert install_hooks.main(["--settings", str(other)]) == 0
    assert json.loads(other.read_text())["hooks"], "written where it was asked for"
    assert not (roots["claude"] / "settings.json").exists()


@pytest.fixture
def dsh_bridge(monkeypatch):
    monkeypatch.setattr(install_hooks, "dsh_bridge_available", lambda: True)
    monkeypatch.setattr(install_hooks, "dsh_verify", lambda: 0)  # no dsh in a unit test


def test_dsh_bridge_is_mounted_when_dsh_can_load_it(roots, dsh_bridge):
    assert install_hooks.main([]) == 0
    patch = (roots["dsh"] / "cordis.patch.yml").read_text()
    assert "id: skill-usage-insights-hooks" in patch and install_hooks.DSH_BRIDGE in patch
    config = json.loads((roots["dsh"] / "skill-usage-insights-hooks.json").read_text())
    assert str(install_hooks.COLLECT) in config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    before = patch
    assert install_hooks.main([]) == 0
    assert (roots["dsh"] / "cordis.patch.yml").read_text() == before, "a second install changes nothing"
    assert install_hooks.main(["--status"]) == 0


def test_dsh_install_keeps_other_patch_rows(roots, dsh_bridge):
    (roots["dsh"] / "cordis.patch.yml").write_text("- insert:\n    - id: someone-elses\n      name: 'x'\n")
    install_hooks.main([])
    patch = (roots["dsh"] / "cordis.patch.yml").read_text()
    assert "id: someone-elses" in patch and "id: skill-usage-insights-hooks" in patch
    install_hooks.main(["--uninstall"])
    assert (roots["dsh"] / "cordis.patch.yml").read_text().strip() == \
        "- insert:\n    - id: someone-elses\n      name: 'x'"
    assert not (roots["dsh"] / "skill-usage-insights-hooks.json").exists()


def test_dry_run_writes_nothing_and_reveals_no_settings(roots, dsh_bridge, capsys):
    path = roots["claude"] / "settings.json"
    path.write_text(json.dumps({"apiKey": "sk-must-not-be-printed", "hooks": {}}))
    assert install_hooks.main(["--dry-run"]) == 0
    assert not (roots["dsh"] / "cordis.patch.yml").exists()
    assert not (roots["codex"] / "hooks.json").exists()
    out = capsys.readouterr().out
    assert "Skill" in out and "sk-must-not-be-printed" not in out
