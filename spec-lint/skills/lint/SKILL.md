---
name: lint
description: Runs the spec_lint linter on Markdown spec files. Use when asked to lint specs, check anchor hygiene, validate spec:// URIs, check token budgets, or run the spec linter on a file or the whole federation.
---

# spec-lint:lint

Lint Markdown spec files using the installed `spec_lint` linter.

## 1. Check the installation

The linter lives at `~/.local/lib/spec-lint/spec_lint.py`. Verify it is present:

```sh
ls ~/.local/lib/spec-lint/spec_lint.py
```

If missing, tell the user to run:

```sh
python3 /path/to/arch-linter/scripts/install.py
```

## 2. Determine the target files

Identify what to lint from the user's request:

1. **Named files** — use those paths directly.
2. **Current file** — use the path of the file just edited or discussed.
3. **"All specs" / "whole federation"** — sweep with:

   ```sh
   find . -name '*.md' | xargs python3 ~/.local/lib/spec-lint/spec_lint.py lint --format json
   ```

4. **Staged files** — use `git diff --cached --name-only -- '*.md'`.

## 3. Run the linter

```sh
python3 ~/.local/lib/spec-lint/spec_lint.py lint --format json <files>
```

Exit code `1` means at least one error-severity finding. Parse the JSON array of finding objects — each has: `severity`, `file`, `line`, `check`, `message`.

## 4. Report findings

Group by file. Within each file, list errors first, then warnings.

For each finding state:
- The check name (`anchor_hygiene`, `uri_resolution`, `token_budgets`, `bidirectional_coverage`)
- The line number
- The message

If there are no findings, confirm the files are clean.

## 5. Offer to fix

After reporting, offer targeted remediation:

1. **`anchor_hygiene` errors** — offer to apply `/md-spec` to the affected file; that satisfies the check by construction.
2. **`uri_resolution` errors** — show the broken `spec://` URI and ask the user for the correct target.
3. **`token_budgets` warnings** — report the file size vs budget and ask whether to split the file.
4. **`bidirectional_coverage` errors** — list the `{#ac.*}` anchors with no back-reference and ask where the test files live.
