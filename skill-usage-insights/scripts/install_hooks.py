"""Install, inspect, or remove the skill-usage-insights hooks for every harness here.

One command, no harness flag. It looks for each harness on this machine and wires
the one it finds, skipping the rest:

- **Claude Code** — merges `hooks/hooks.json` into `~/.claude/settings.json`
  (`$CLAUDE_CONFIG_DIR` moves that root).
- **Codex** — merges `hooks/hooks_codex.json` into `~/.codex/hooks.json`
  (`$CODEX_HOME`). Codex only runs a handler after you trust it once with
  `/hooks`; the installer says so, and nothing is collected until then.
- **dsh** — mounts the live bridge (`@deepseek-ai/dsh-hooks-claude-code`) in
  `$DSH_HOME/cordis.patch.yml`, the user-owned overlay the loader applies over
  every profile's own layer. dsh needs no hooks to be measured — its session logs
  already carry skill loads, prompts and per-step tokens — so the bridge is only
  mounted when dsh and that plugin are both present, and it is skipped otherwise.

Our handlers are recognised by the collector path they run, so re-running is
idempotent and other hooks are never touched. Every file is backed up next to
itself before a write.

    python3 install_hooks.py                 # install every harness found here
    python3 install_hooks.py --status        # per harness: installed, missing or skipped
    python3 install_hooks.py --dry-run       # print what would be written, write nothing
    python3 install_hooks.py --uninstall     # remove only our handlers
    python3 install_hooks.py --settings .claude/settings.local.json   # Claude Code elsewhere
"""

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "hooks" / "hooks.json"
COLLECT = SKILL_DIR / "hooks" / "collect.py"
MARKER = "skill-usage-insights/hooks/collect.py"

# Codex keeps its hook set in its own hooks.json and needs `[features].hooks` on (the default).
CODEX_TEMPLATE = SKILL_DIR / "hooks" / "hooks_codex.json"
CODEX_COLLECT = SKILL_DIR / "hooks" / "collect_codex.py"
CODEX_MARKER = "skill-usage-insights/hooks/collect_codex.py"

# The optional dsh side. `DSH_HOME` defaults to ~/.dsh; the home patch layer is the
# user-owned overlay the loader applies over every profile's own layer.
DSH_TEMPLATE = SKILL_DIR / "hooks" / "dsh-hooks.json"
DSH_BRIDGE = "@deepseek-ai/dsh-hooks-claude-code"
DSH_ROW = "skill-usage-insights-hooks"


def claude_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def claude_settings():
    return claude_dir() / "settings.json"


def dsh_home():
    return Path(os.environ.get("DSH_HOME") or "~/.dsh").expanduser()


def dsh_patch():
    return dsh_home() / "cordis.patch.yml"


def dsh_config():
    return dsh_home() / "skill-usage-insights-hooks.json"


def dsh_collect():
    return SKILL_DIR / "hooks" / "collect.py"


def dsh_bridge_available():
    """dsh is on PATH and the hook bridge sits in its global node_modules."""
    dsh = shutil.which("dsh")
    if not dsh:
        return False
    prefix = Path(os.path.realpath(dsh)).parent.parent
    return (prefix / "lib" / "node_modules" / DSH_BRIDGE).is_dir()


def codex_home():
    return Path(os.environ.get("CODEX_HOME") or "~/.codex").expanduser()


def codex_hooks_path():
    return codex_home() / "hooks.json"


def ours(handler, marker=MARKER):
    return isinstance(handler, dict) and marker in str(handler.get("command", "")) + " ".join(handler.get("args") or [])


def strip_ours(hooks, marker=MARKER):
    """Remove our handlers (and any matcher group left empty) from a settings `hooks` object."""
    cleaned = {}
    for event, groups in (hooks or {}).items():
        kept = []
        for group in groups or []:
            handlers = [h for h in group.get("hooks", []) if not ours(h, marker)]
            if handlers:
                kept.append(dict(group, hooks=handlers))
        if kept:
            cleaned[event] = kept
    return cleaned


def rendered_template():
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))["hooks"]
    for groups in template.values():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = handler["command"].replace("${COLLECT}", shlex.quote(str(COLLECT)))
    return template


def merged(settings, uninstall=False):
    result = dict(settings)
    hooks = strip_ours(settings.get("hooks"))
    if not uninstall:
        for event, groups in rendered_template().items():
            hooks.setdefault(event, []).extend(groups)
    if hooks:
        result["hooks"] = hooks
    else:
        result.pop("hooks", None)
    return result


def claude_status(settings):
    wired = {}
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups or []:
            for handler in group.get("hooks", []):
                if ours(handler):
                    wired[event] = handler.get("command")
    expected = set(rendered_template())
    for event in sorted(expected | set(wired)):
        state = "installed" if event in wired else "missing"
        print(f"  {event:<20} {state:<9} {wired.get(event, '')}")
    if wired and set(wired) == expected and all(str(COLLECT) in c for c in wired.values()):
        return 0
    return 1


# --------------------------------------------------------------------------- codex


def rendered_codex_template():
    """Our Codex hook set with the collector's absolute path in it."""
    doc = json.loads(CODEX_TEMPLATE.read_text(encoding="utf-8"))
    for groups in doc["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = handler["command"].replace("${COLLECT}", shlex.quote(str(CODEX_COLLECT)))
    return doc


def codex_merged(doc, uninstall=False):
    result = dict(doc)
    hooks = strip_ours(doc.get("hooks"), CODEX_MARKER)
    if not uninstall:
        for event, groups in rendered_codex_template()["hooks"].items():
            hooks.setdefault(event, []).extend(groups)
    if hooks:
        result["hooks"] = hooks
    else:
        result.pop("hooks", None)
    return result


def codex_status(doc):
    wired = {}
    for event, groups in (doc.get("hooks") or {}).items():
        for group in groups or []:
            for handler in group.get("hooks", []):
                if ours(handler, CODEX_MARKER):
                    wired[event] = handler.get("command")
    expected = set(rendered_codex_template()["hooks"])
    for event in sorted(expected | set(wired)):
        print(f"{event:<20} {'installed' if event in wired else 'missing':<9} {wired.get(event, '')}")
    ok = bool(wired) and set(wired) == expected and all(str(CODEX_COLLECT) in c for c in wired.values())
    if not ok:
        print("note: Codex skips untrusted hooks; run /hooks once to review and trust them")
    else:
        print("note: if a session records nothing, its handlers are not trusted yet — run /hooks")
    return 0 if ok else 1


def codex_install(dry_run=False, uninstall=False):
    path = codex_hooks_path()
    try:
        doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError as exc:
        print(f"refusing to touch {path}: not valid JSON ({exc})", file=sys.stderr)
        return 2
    result = codex_merged(doc, uninstall=uninstall)
    if dry_run:
        print(f"--- {path} (hooks only; every other key is left as it is)")
        print(json.dumps({"hooks": result.get("hooks", {})}, indent=2, ensure_ascii=False))
        return 0
    if result == doc:
        print(f"{path}: already {'clean' if uninstall else 'up to date'}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        backup(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"{path}: {'removed' if uninstall else 'installed'} skill-usage-insights hooks")
    if not uninstall:
        print("next: run /hooks in Codex once and trust the handlers, or they never run")
    return 0


# --------------------------------------------------------------------------- dsh


def rendered_dsh_template():
    """The bridge hook config with our collector's absolute path in it."""
    template = json.loads(DSH_TEMPLATE.read_text(encoding="utf-8"))
    for groups in template["hooks"].values():
        for group in groups:
            for handler in group["hooks"]:
                handler["command"] = handler["command"].replace("${COLLECT}", shlex.quote(str(dsh_collect())))
    return template


def patch_rows(text):
    """The `- ` rows of a cordis patch list: this file is small, hand-written YAML."""
    rows, current = [], None
    for line in text.splitlines():
        if re.match(r"^- ", line):
            if current is not None:
                rows.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        rows.append(current)
    return rows


def patch_has_row(text, row_id):
    return any(re.search(rf"^\s*-?\s*id:\s*['\"]?{re.escape(row_id)}['\"]?\s*$", line)
               for row in patch_rows(text) for line in row)


def dsh_patch_with(patch_text, uninstall=False):
    """The patch text with our bridge row present (or removed), keeping every other row."""
    if uninstall:
        kept = [row for row in patch_rows(patch_text) if not patch_has_row("\n".join(row), DSH_ROW)]
        if not kept:
            return "[]\n"
        return "\n".join("\n".join(row).rstrip() for row in kept) + "\n"
    if patch_has_row(patch_text, DSH_ROW):
        return patch_text
    row = ["- insert:",
           f"    - id: {DSH_ROW}",
           f"      name: '{DSH_BRIDGE}'",
           "      config:",
           f"        configPath: {dsh_config()}"]
    body = "\n".join("\n".join(r).rstrip() for r in patch_rows(patch_text) if "\n".join(r).strip() not in ("[]",))
    return (body + "\n" if body else "") + "\n".join(row) + "\n"


def dsh_installed():
    """(config written, row mounted) for the optional bridge."""
    return dsh_config().is_file(), dsh_patch().is_file() and patch_has_row(
        dsh_patch().read_text(encoding="utf-8"), DSH_ROW)


def dsh_status():
    config, mounted = dsh_installed()
    print(f"collector config   {'installed' if config else 'missing'}  {dsh_config()}")
    print(f"bridge mounted     {'yes' if mounted else 'no'}  {dsh_patch()}")
    print("note: dsh session logs are collected without this bridge; it only adds live capture")
    return 0 if config and mounted else 1


def backup(path):
    keep = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(path, keep)
    print(f"backup: {keep}")


def dsh_install(dry_run=False, uninstall=False):
    patch = dsh_patch()
    if dry_run:
        current = patch.read_text(encoding="utf-8") if patch.is_file() else "[]\n"
        print(f"--- {dsh_config()}")
        print(json.dumps(rendered_dsh_template(), indent=2, ensure_ascii=False))
        print(f"--- {patch}")
        print(dsh_patch_with(current, uninstall=uninstall), end="")
        return 0
    if uninstall:
        changed = False
        if patch.is_file():
            updated = dsh_patch_with(patch.read_text(encoding="utf-8"), uninstall=True)
            if updated != patch.read_text(encoding="utf-8"):
                backup(patch)
                patch.write_text(updated, encoding="utf-8")
                changed = True
        if dsh_config().is_file():
            dsh_config().unlink()
            changed = True
        print(f"{patch}: {'bridge removed' if changed else 'nothing to remove'}")
        return 0

    dsh_home().mkdir(parents=True, exist_ok=True)
    config = dsh_config()
    rendered = json.dumps(rendered_dsh_template(), indent=2, ensure_ascii=False) + "\n"
    if config.is_file() and config.read_text(encoding="utf-8") == rendered:
        print(f"{config}: up to date")
    else:
        if config.is_file():
            backup(config)
        config.write_text(rendered, encoding="utf-8")
        print(f"{config}: written")
    current = patch.read_text(encoding="utf-8") if patch.is_file() else "[]\n"
    updated = dsh_patch_with(current)
    if updated == current:
        print(f"{patch}: bridge already mounted")
    else:
        if patch.is_file():
            backup(patch)
        patch.write_text(updated, encoding="utf-8")
        print(f"{patch}: bridge mounted")
    return dsh_verify()


def dsh_verify():
    """Ask dsh to compose the profile: a patch it cannot load is worse than no bridge."""
    dsh = shutil.which("dsh")
    if not dsh:
        print("dsh not on PATH: patch not verified")
        return 0
    try:
        done = subprocess.run([dsh, "--profile", "web", "--dump-config"], capture_output=True, text=True, timeout=180)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"could not verify the patch with `dsh --dump-config`: {exc}", file=sys.stderr)
        return 1
    if done.returncode != 0:
        print(f"`dsh --dump-config` failed ({done.returncode}); the patch is probably invalid:\n"
              f"{(done.stderr or done.stdout).strip()[:800]}", file=sys.stderr)
        return 1
    mounted = DSH_BRIDGE in done.stdout
    print(f"dsh composed the profile with the bridge {'mounted' if mounted else 'NOT mounted'}")
    return 0 if mounted else 1


def claude_install(path, dry_run=False, uninstall=False):
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError as exc:
        print(f"refusing to touch {path}: not valid JSON ({exc})", file=sys.stderr)
        return 2
    result = merged(settings, uninstall=uninstall)
    if dry_run:
        print(f"--- {path} (hooks only; every other key is left as it is)")
        print(json.dumps({"hooks": result.get("hooks", {})}, indent=2, ensure_ascii=False))
        return 0
    if result == settings:
        print(f"{path}: already {'clean' if uninstall else 'up to date'}")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"{path}: {'removed' if uninstall else 'installed'} skill-usage-insights hooks")
    return 0


def targets(settings=None):
    """Every supported harness, whether it is present here, and why it is not.

    A harness whose config directory is missing has never run on this machine, so it
    is skipped rather than created. dsh additionally needs its hook bridge plugin:
    without it the patch row would be one dsh cannot load.
    """
    claude = settings or claude_settings()
    codex = codex_hooks_path()
    home = dsh_home()
    return [
        {"name": "claude-code", "path": claude,
         "present": bool(settings) or claude.parent.is_dir(),
         "skip": f"no {claude.parent}"},
        {"name": "codex", "path": codex,
         "present": codex.parent.is_dir(),
         "skip": f"no {codex.parent}"},
        {"name": "dsh", "path": dsh_patch(),
         "present": home.is_dir() and dsh_bridge_available(),
         "skip": (f"no {DSH_BRIDGE} in the dsh install" if home.is_dir() else f"no {home}")},
    ]


def install_one(name, settings, dry_run, uninstall):
    if name == "claude-code":
        return claude_install(settings or claude_settings(), dry_run, uninstall)
    if name == "codex":
        return codex_install(dry_run, uninstall)
    return dsh_install(dry_run, uninstall)


def status_one(name, settings):
    if name == "dsh":
        return dsh_status()
    path = codex_hooks_path() if name == "codex" else (settings or claude_settings())
    try:
        doc = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError as exc:
        print(f"{path}: not valid JSON ({exc})", file=sys.stderr)
        return 2
    return codex_status(doc) if name == "codex" else claude_status(doc)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", type=Path, default=None,
                        help="Claude Code settings file (default: <claude dir>/settings.json)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--uninstall", action="store_true", help="remove our handlers everywhere")
    group.add_argument("--status", action="store_true", help="report what is wired; exit 1 unless fully installed")
    parser.add_argument("--dry-run", action="store_true", help="print the result instead of writing it")
    args = parser.parse_args(argv)

    settings = args.settings.expanduser() if args.settings else None
    failed, checked = [], 0
    for target in targets(settings):
        name = target["name"]
        if not target["present"]:
            print(f"{name}: skipped — {target['skip']}")
            continue
        checked += 1
        print(f"{name}: {target['path']}")
        code = status_one(name, settings) if args.status else install_one(
            name, settings, args.dry_run, args.uninstall)
        if code:
            failed.append(name)
    if not checked and not args.uninstall:
        print("no harness found: looked for $CLAUDE_CONFIG_DIR, $CODEX_HOME and $DSH_HOME",
              file=sys.stderr)
        return 1
    if failed:
        print(f"\nnot ok: {', '.join(failed)} (see above)", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
