---
name: install
description: Bootstrap the spec-lint plugin by running install.py from the marketplace source. Copies scripts to ~/.local/lib/spec-lint/, installs dependencies, and wires the Claude Code PostToolUse hook. Run this once after installing the plugin from the marketplace.
---

# spec-lint:install

Bootstrap the spec-lint plugin after marketplace installation.

## 1. Locate install.py

Find the installer in the marketplace source:

```sh
ls ~/.claude/plugins/marketplaces/*/plugins/arch-linter/install.py
```

If multiple matches exist, use the first one. If no match is found, tell the user the plugin source directory could not be located and stop.

## 2. Run the installer

```sh
python3 <path-to-install.py>
```

Pass through any flags the user specified (e.g. `--with-tiktoken`, `--no-hook`, `--no-deps`).

If the user did not specify any flags, run with no flags (defaults install everything).

## 3. Report

Tell the user to run `/reload-plugins` in Claude Code to activate the skills.
