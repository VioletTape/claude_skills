#!/usr/bin/env python3
"""install — distribute spec_lint to a stable location and wire up Claude Code.

Usage:
  python3 install.py              # install or update in place
  python3 install.py --uninstall  # remove everything

Installs to:
  ~/.local/lib/spec-lint/spec_lint.py
  ~/.local/lib/spec-lint/add_hook.py
  ~/.claude/settings.json                    (PostToolUse hook on Edit|Write)

Options:
  --no-hook        skip Claude Code hook
  --no-deps        skip pip install
  --with-tiktoken  also install tiktoken for accurate token counts
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent.resolve()

INSTALL_DIR = Path.home() / ".local" / "lib" / "spec-lint"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"

SCRIPT_SOURCE = HERE / "spec_lint.py"
ADD_HOOK_SCRIPT_SOURCE = HERE / "add_hook.py"

HOOK_MATCHER = "Edit|Write"
HOOK_SUBCOMMAND = "post-tool-use"
HOOK_MARKER = f"spec_lint.py {HOOK_SUBCOMMAND}"


# ---------------------------------------------------------------------------
# Install steps
# ---------------------------------------------------------------------------


def install_script() -> Path:
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    dest = INSTALL_DIR / "spec_lint.py"
    shutil.copy2(SCRIPT_SOURCE, dest)
    version = _read_version(SCRIPT_SOURCE)
    (INSTALL_DIR / "version").write_text(version + "\n", encoding="utf-8")
    print(f"install: spec_lint.py → {dest}  (v{version})")

    add_hook_dest = INSTALL_DIR / "add_hook.py"
    shutil.copy2(ADD_HOOK_SCRIPT_SOURCE, add_hook_dest)
    print(f"install: add_hook.py  → {add_hook_dest}")

    return dest


def install_deps(with_tiktoken: bool) -> None:
    pkgs = ["pyyaml"]
    if with_tiktoken:
        pkgs.append("tiktoken")
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet"] + pkgs
    )
    print(f"install: deps installed ({', '.join(pkgs)})")


def install_hook(script_path: Path) -> None:
    command = f"python3 {script_path} {HOOK_SUBCOMMAND}"
    settings = _load_json(SETTINGS_PATH, {})
    ptu = settings.setdefault("hooks", {}).setdefault("PostToolUse", [])
    _upsert_hook(ptu, command)
    _save_json(SETTINGS_PATH, settings)
    print(f"install: hook wired in {SETTINGS_PATH}")


# ---------------------------------------------------------------------------
# Uninstall
# ---------------------------------------------------------------------------


def uninstall() -> None:
    if INSTALL_DIR.is_dir():
        shutil.rmtree(INSTALL_DIR)
        print(f"install: removed {INSTALL_DIR}")

    settings = _load_json(SETTINGS_PATH, {})
    ptu = settings.get("hooks", {}).get("PostToolUse", [])
    if _remove_hook(ptu):
        _save_json(SETTINGS_PATH, settings)
        print("install: hook removed from settings.json")
    else:
        print("install: no hook entry found in settings.json")


# ---------------------------------------------------------------------------
# JSON / settings helpers
# ---------------------------------------------------------------------------


def _load_json(path: Path, default) -> dict:
    if not path.is_file():
        return default
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        print(f"install: {path.name} is not valid JSON: {e}", file=sys.stderr)
        sys.exit(2)
    return data if isinstance(data, dict) else default


def _save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        shutil.copy2(path, path.with_suffix(".json.bak"))
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def _upsert_hook(ptu: list, command: str) -> None:
    for entry in ptu:
        if not isinstance(entry, dict) or entry.get("matcher") != HOOK_MATCHER:
            continue
        for hook in entry.get("hooks", []) or []:
            if isinstance(hook, dict) and HOOK_MARKER in hook.get("command", ""):
                hook["command"] = command
                return
    ptu.append({
        "matcher": HOOK_MATCHER,
        "hooks": [{"type": "command", "command": command}],
    })


def _remove_hook(ptu: list) -> bool:
    changed = False
    for entry in ptu:
        if not isinstance(entry, dict):
            continue
        hooks = entry.get("hooks", [])
        before = len(hooks)
        entry["hooks"] = [
            h for h in hooks
            if not (isinstance(h, dict) and HOOK_MARKER in h.get("command", ""))
        ]
        if len(entry["hooks"]) < before:
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def _read_version(script: Path) -> str:
    for line in script.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "unknown"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install or update spec_lint for Claude Code.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Remove spec_lint, plugin, and the hook.",
    )
    parser.add_argument(
        "--no-hook", action="store_true",
        help="Skip Claude Code PostToolUse hook installation.",
    )
    parser.add_argument(
        "--no-deps", action="store_true",
        help="Skip pip dependency installation.",
    )
    parser.add_argument(
        "--with-tiktoken", action="store_true",
        help="Also install tiktoken for accurate token counting.",
    )
    args = parser.parse_args()

    if args.uninstall:
        uninstall()
        return 0

    script_path = install_script()
    if not args.no_deps:
        install_deps(args.with_tiktoken)
    if not args.no_hook:
        install_hook(script_path)

    print("\ninstall: done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
