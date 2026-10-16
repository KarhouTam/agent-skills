"""Install, inspect, or remove the skill-usage-insights hooks.

Claude Code needs a hook set in a settings file; dsh needs none, because its own
session logs already carry skill loads, prompts and per-step tokens
(`hooks/collect_dsh.py` reads them). What dsh can optionally have is the live
bridge: `--dsh` writes `hooks/dsh-hooks.json` and mounts
`@deepseek-ai/dsh-hooks-claude-code` in the user's own patch layer, which is the
only file a user may edit.

Our handlers are recognised by the collector path they run, so re-running is
idempotent and other hooks are never touched. Every file is backed up next to
itself before a write.

    python3 install_hooks.py                 # install into ~/.claude/settings.json
    python3 install_hooks.py --status        # which events are wired, and to which path
    python3 install_hooks.py --dry-run       # print the merged settings, write nothing
    python3 install_hooks.py --uninstall     # remove only our handlers
    python3 install_hooks.py --settings .claude/settings.local.json   # project-local instead

    python3 install_hooks.py --dsh           # optional: mount the dsh hook bridge
    python3 install_hooks.py --dsh --status  # is the bridge mounted?
    python3 install_hooks.py --dsh --uninstall
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
DEFAULT_SETTINGS = Path("~/.claude/settings.json").expanduser()

# The optional dsh side. `DSH_HOME` defaults to ~/.dsh; the home patch layer is the
# user-owned overlay the loader applies over every profile's own layer.
DSH_TEMPLATE = SKILL_DIR / "hooks" / "dsh-hooks.json"
DSH_BRIDGE = "@deepseek-ai/dsh-hooks-claude-code"
DSH_ROW = "skill-usage-insights-hooks"


def dsh_home():
    return Path(os.environ.get("DSH_HOME") or "~/.dsh").expanduser()


def dsh_patch():
    return dsh_home() / "cordis.patch.yml"


def dsh_config():
    return dsh_home() / "skill-usage-insights-hooks.json"


def dsh_collect():
    return SKILL_DIR / "hooks" / "collect.py"


def ours(handler):
    return isinstance(handler, dict) and MARKER in str(handler.get("command", "")) + " ".join(handler.get("args") or [])


def strip_ours(hooks):
    """Remove our handlers (and any matcher group left empty) from a settings `hooks` object."""
    cleaned = {}
    for event, groups in (hooks or {}).items():
        kept = []
        for group in groups or []:
            handlers = [h for h in group.get("hooks", []) if not ours(h)]
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


def status(settings):
    wired = {}
    for event, groups in (settings.get("hooks") or {}).items():
        for group in groups or []:
            for handler in group.get("hooks", []):
                if ours(handler):
                    wired[event] = handler.get("command")
    expected = set(rendered_template())
    for event in sorted(expected | set(wired)):
        state = "installed" if event in wired else "missing"
        print(f"{event:<20} {state:<9} {wired.get(event, '')}")
    if wired and set(wired) == expected and all(str(COLLECT) in c for c in wired.values()):
        return 0
    return 1


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS, help="settings file (default: %(default)s)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--uninstall", action="store_true", help="remove our handlers")
    group.add_argument("--status", action="store_true", help="report what is wired; exit 1 unless fully installed")
    parser.add_argument("--dry-run", action="store_true", help="print the result instead of writing it")
    parser.add_argument("--dsh", action="store_true",
                        help="optional live capture: mount the dsh hook bridge in $DSH_HOME/cordis.patch.yml")
    args = parser.parse_args(argv)

    if args.dsh:
        return dsh_status() if args.status else dsh_install(dry_run=args.dry_run, uninstall=args.uninstall)

    path = args.settings.expanduser()
    try:
        settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except ValueError as exc:
        print(f"refusing to touch {path}: not valid JSON ({exc})", file=sys.stderr)
        return 2

    if args.status:
        return status(settings)

    result = merged(settings, uninstall=args.uninstall)
    if args.dry_run:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if result == settings:
        print(f"{path}: already {'clean' if args.uninstall else 'up to date'}")
        return 0

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        backup = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d%H%M%S')}")
        shutil.copy2(path, backup)
        print(f"backup: {backup}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    print(f"{path}: {'removed' if args.uninstall else 'installed'} skill-usage-insights hooks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
