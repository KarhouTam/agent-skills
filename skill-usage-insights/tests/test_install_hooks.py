"""install_hooks.py merges our handlers into settings without disturbing anyone else's."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import install_hooks  # noqa: E402

FOREIGN = {"type": "command", "command": "~/bin/notify.sh"}


def settings_with_foreign_hooks(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "model": "opus",
        "hooks": {"Stop": [{"hooks": [FOREIGN]}], "PreToolUse": [{"matcher": "Bash", "hooks": [FOREIGN]}]},
    }))
    return path


def test_install_is_idempotent_and_keeps_foreign_hooks(tmp_path):
    path = settings_with_foreign_hooks(tmp_path)
    assert install_hooks.main(["--settings", str(path)]) == 0
    first = json.loads(path.read_text())
    assert install_hooks.main(["--settings", str(path)]) == 0
    assert json.loads(path.read_text()) == first

    assert first["model"] == "opus"
    assert first["hooks"]["Stop"][0]["hooks"] == [FOREIGN]
    assert first["hooks"]["PreToolUse"][0] == {"matcher": "Bash", "hooks": [FOREIGN]}
    ours = [g for g in first["hooks"]["PreToolUse"] if g.get("matcher") == "Skill"]
    assert len(ours) == 1 and str(install_hooks.COLLECT) in ours[0]["hooks"][0]["command"]
    template = json.loads(install_hooks.TEMPLATE.read_text())["hooks"]
    assert set(template) <= set(first["hooks"])
    assert install_hooks.main(["--settings", str(path), "--status"]) == 0
    assert list(tmp_path.glob("settings.json.bak-*")), "the original was backed up"


def test_uninstall_restores_the_original(tmp_path):
    path = settings_with_foreign_hooks(tmp_path)
    original = json.loads(path.read_text())
    install_hooks.main(["--settings", str(path)])
    assert install_hooks.main(["--settings", str(path), "--uninstall"]) == 0
    assert json.loads(path.read_text()) == original
    assert install_hooks.main(["--settings", str(path), "--status"]) == 1


def test_refuses_invalid_json(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{ not json")
    assert install_hooks.main(["--settings", str(path)]) == 2
    assert path.read_text() == "{ not json"


@pytest.fixture
def dsh_home(tmp_path, monkeypatch):
    home = tmp_path / "dsh"
    home.mkdir()
    monkeypatch.setenv("DSH_HOME", str(home))
    return home


def test_dsh_install_mounts_the_bridge_and_is_idempotent(dsh_home, monkeypatch, capsys):
    monkeypatch.setattr(install_hooks, "dsh_verify", lambda: 0)  # no dsh in a unit test
    assert install_hooks.main(["--dsh"]) == 0
    patch = (dsh_home / "cordis.patch.yml").read_text()
    assert "id: skill-usage-insights-hooks" in patch and install_hooks.DSH_BRIDGE in patch
    config = json.loads((dsh_home / "skill-usage-insights-hooks.json").read_text())
    assert str(install_hooks.COLLECT) in config["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    assert config["hooks"]["PreToolUse"][0]["matcher"] == "skill"

    before = patch
    assert install_hooks.main(["--dsh"]) == 0
    assert (dsh_home / "cordis.patch.yml").read_text() == before, "a second install changes nothing"
    assert install_hooks.main(["--dsh", "--status"]) == 0


def test_dsh_install_keeps_other_patch_rows(dsh_home, monkeypatch):
    monkeypatch.setattr(install_hooks, "dsh_verify", lambda: 0)
    (dsh_home / "cordis.patch.yml").write_text("- insert:\n    - id: someone-elses\n      name: 'x'\n")
    install_hooks.main(["--dsh"])
    patch = (dsh_home / "cordis.patch.yml").read_text()
    assert "id: someone-elses" in patch and "id: skill-usage-insights-hooks" in patch
    install_hooks.main(["--dsh", "--uninstall"])
    assert (dsh_home / "cordis.patch.yml").read_text().strip() == "- insert:\n    - id: someone-elses\n      name: 'x'"
    assert not (dsh_home / "skill-usage-insights-hooks.json").exists()
    assert install_hooks.main(["--dsh", "--status"]) == 1


def test_dsh_dry_run_writes_nothing(dsh_home, capsys):
    assert install_hooks.main(["--dsh", "--dry-run"]) == 0
    assert not (dsh_home / "cordis.patch.yml").exists()
    assert not (dsh_home / "skill-usage-insights-hooks.json").exists()
    assert "skill-usage-insights-hooks" in capsys.readouterr().out
