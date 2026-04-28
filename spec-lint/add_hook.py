#!/usr/bin/env python3
"""add_hook — install spec-lint pre-commit hooks into git repos.

Usage:
  python3 add_hook.py [root_dir]             # install into all repos under root_dir
  python3 add_hook.py --uninstall [root_dir] # remove managed hooks

The script walks root_dir (default: cwd) for subdirectories containing .git/,
then writes or updates a managed pre-commit hook in each one.

A hook that already exists without the managed marker is left untouched and
reported so the user can merge manually.
"""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

MANAGED_MARKER = "# spec-lint managed"

HOOK_BODY = """\
#!/bin/sh
# spec-lint managed — do not remove this line
STAGED=$(git diff --cached --name-only --diff-filter=ACM | grep '\\.md$')
[ -z "$STAGED" ] && exit 0
python3 ~/.local/lib/spec-lint/spec_lint.py lint $STAGED
"""


def find_git_repos(root: Path) -> list[Path]:
    repos = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        if (entry / ".git").is_dir():
            repos.append(entry)
        elif entry.is_dir():
            repos.extend(find_git_repos(entry))
    # also check root itself
    if (root / ".git").is_dir() and root not in repos:
        repos.insert(0, root)
    return repos


def install_hook(repo: Path) -> str:
    """Return 'installed', 'updated', 'skipped', or 'unchanged'."""
    hook_path = repo / ".git" / "hooks" / "pre-commit"
    hook_path.parent.mkdir(parents=True, exist_ok=True)

    if hook_path.exists():
        existing = hook_path.read_text(encoding="utf-8")
        if MANAGED_MARKER not in existing:
            return "skipped"
        if existing == HOOK_BODY:
            return "unchanged"
        hook_path.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
        _make_executable(hook_path)
        return "updated"

    hook_path.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
    _make_executable(hook_path)
    return "installed"


def uninstall_hook(repo: Path) -> str:
    """Return 'removed' or 'skipped'."""
    hook_path = repo / ".git" / "hooks" / "pre-commit"
    if not hook_path.exists():
        return "skipped"
    existing = hook_path.read_text(encoding="utf-8")
    if MANAGED_MARKER not in existing:
        return "skipped"
    hook_path.unlink()
    return "removed"


def _make_executable(path: Path) -> None:
    current = path.stat().st_mode
    path.chmod(current | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run(root: Path, uninstall: bool) -> int:
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 1

    repos = find_git_repos(root)
    if not repos:
        print(f"No git repositories found under {root}")
        return 0

    counts: dict[str, list[Path]] = {
        "installed": [], "updated": [], "unchanged": [], "removed": [], "skipped": [],
    }

    for repo in repos:
        result = uninstall_hook(repo) if uninstall else install_hook(repo)
        counts[result].append(repo)
        label = {
            "installed": "hook installed",
            "updated": "hook updated",
            "unchanged": "already up to date",
            "removed": "hook removed",
            "skipped": "skipped (custom hook exists)",
        }[result]
        print(f"  {repo}  [{label}]")

    print()
    if uninstall:
        print(f"done: {len(counts['removed'])} removed, {len(counts['skipped'])} skipped")
    else:
        print(
            f"done: {len(counts['installed'])} installed, "
            f"{len(counts['updated'])} updated, "
            f"{len(counts['unchanged'])} unchanged, "
            f"{len(counts['skipped'])} skipped"
        )

    if counts["skipped"]:
        print("\nSkipped repos have an existing pre-commit hook without the spec-lint")
        print("marker. Merge the hook manually or remove it first and re-run.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install or remove spec-lint pre-commit hooks.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--uninstall", action="store_true",
        help="Remove managed spec-lint hooks from all repos found.",
    )
    parser.add_argument(
        "root", nargs="?", default=".",
        help="Root directory to search (default: current directory).",
    )
    args = parser.parse_args()
    return run(Path(args.root).resolve(), args.uninstall)


if __name__ == "__main__":
    sys.exit(main())
