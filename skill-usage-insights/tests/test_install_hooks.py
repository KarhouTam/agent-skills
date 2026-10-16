"""install_hooks.py merges our handlers into settings without disturbing anyone else's."""

import json
import sys
from pathlib import Path

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
