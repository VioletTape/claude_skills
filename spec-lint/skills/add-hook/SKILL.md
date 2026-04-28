---
name: add-hook
description: Install spec-lint pre-commit git hooks into all git repos under a directory. Use when asked to add spec-lint hooks, wire up pre-commit checks, or set up drift detection for a project or monorepo.
---

# spec-lint:add-hook

Install a `pre-commit` git hook that runs `spec_lint` on every staged Markdown file
before a commit is accepted. Walks the target directory and installs into every
subfolder that contains a `.git/` directory.

## 1. Check the installation

The helper script lives at `~/.local/lib/spec-lint/add_hook.py`. Verify it is present:

```sh
ls ~/.local/lib/spec-lint/add_hook.py
```

If missing, tell the user to run:

```sh
python3 /path/to/arch-linter/scripts/install.py
```

## 2. Determine the root directory

Use the directory the user names, or default to the current working directory.

## 3. Run the hook installer

```sh
python3 ~/.local/lib/spec-lint/add_hook.py [root_dir]
```

The script will:
1. Walk `root_dir` (non-recursively into `.git/` itself) looking for `.git/` subdirectories.
2. For each repo found, write or update `.git/hooks/pre-commit` with a managed block that
   calls `spec_lint.py lint` on all staged `.md` files.
3. Skip repos where a pre-commit hook already exists with custom (unmanaged) content,
   and report them so the user can decide.
4. Print a summary: repos updated, repos skipped, repos already up to date.

## 4. Uninstall

To remove managed hooks from all repos under a directory:

```sh
python3 ~/.local/lib/spec-lint/add_hook.py --uninstall [root_dir]
```

## 5. Report to the user

After running, report:
- How many repos received the hook.
- Any repos that were skipped (existing custom hook) — list their paths and explain
  the user must merge manually.
- Confirm what the hook does: blocks commits when staged `.md` files have lint errors;
  passes through when there are no staged `.md` files.
