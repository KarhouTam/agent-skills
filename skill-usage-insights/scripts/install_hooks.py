"""Install, inspect, or remove the skill-usage-insights hooks in a Claude Code settings file.

The hook set itself lives in hooks/hooks.json; this script only merges it into
settings. Our handlers are recognised by the collector path they run, so
re-running is idempotent and other hooks in the file are never touched. The
settings file is backed up next to itself before every write.

    python3 install_hooks.py                 # install into ~/.claude/settings.json
    python3 install_hooks.py --status        # which events are wired, and to which path
    python3 install_hooks.py --dry-run       # print the merged settings, write nothing
    python3 install_hooks.py --uninstall     # remove only our handlers
    python3 install_hooks.py --settings .claude/settings.local.json   # project-local instead
"""

import argparse
import json
import shlex
import shutil
import sys
import time
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATE = SKILL_DIR / "hooks" / "hooks.json"
COLLECT = SKILL_DIR / "hooks" / "collect.py"
MARKER = "skill-usage-insights/hooks/collect.py"
DEFAULT_SETTINGS = Path("~/.claude/settings.json").expanduser()


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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS, help="settings file (default: %(default)s)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--uninstall", action="store_true", help="remove our handlers")
    group.add_argument("--status", action="store_true", help="report what is wired; exit 1 unless fully installed")
    parser.add_argument("--dry-run", action="store_true", help="print the result instead of writing it")
    args = parser.parse_args(argv)

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
