# spec_lint 

Deterministic linter for a Markdown spec federation. 

## 1. What it does 

Five checks, all driven by a single `.spec-config.yaml` at the federation root:

1. **anchor_hygiene** — enforces the `/md-spec` skill convention: H1 anchor must be `{#root}`; H2 must be a single kebab-case segment; H3+ must extend the parent anchor with one dot-separated kebab leaf; an `## Acceptance Criteria` heading must use `{#ac}`.
2. **uri_resolution** — every `spec://<namespace>/<logical-path>[#anchor]` reference must resolve. The path is logical: `.md` is implicit, and the resolver finds the unique file in `spec_paths` whose path equals or has-as-suffix the id on a path-segment boundary. Ambiguous matches are an error.
3. **token_budgets** — fails when a file exceeds the budget declared for its glob. Uses `tiktoken` (cl100k_base) when installed, otherwise a `len/4` heuristic.
4. **bidirectional_coverage** — for each `{#ac.*}` anchor in a doc whose status matches `implemented`, require at least one back-reference (`spec://<namespace>/<doc>#<anchor>`) anywhere in a `test_paths` file. Scoped to docs matching `tech_spec_paths`.
5. **md_link_resolution** — every `[text](path)` navigational link must point to an existing file. Links must be relative to the current file; absolute paths (starting with `/`, `\`, or a drive letter) are flagged. URL-scheme links (`https://`, `mailto:`, etc.), image links (`![...]`), and fragment-only links (`#anchor`) are silently skipped. Links inside fenced code blocks and inline code spans are excluded.

Files outside `spec_paths` are skipped for anchor/URI checks but still subject to token budgets if a budget glob matches them.

## 2. Installation

### 2.1. Prerequisites 

1. Python 3.10+.
2. `pyyaml` (required). `tiktoken` (optional — accurate token counts).

The installer handles both automatically.

### 2.2. Install 

After installing the plugin from the marketplace, run `/spec-lint:install` in Claude Code to bootstrap the runtime dependencies. This runs `install.py` which:

1. Copies `spec_lint.py` and `add_hook.py` to `~/.local/lib/spec-lint/` and writes a `version` file.
2. Installs `pyyaml` via pip.
3. Wires a `PostToolUse` hook into `~/.claude/settings.json` (matcher `Edit|Write`). Idempotent — re-running updates paths in place.

The hook activates immediately (no restart needed).

### 2.3. Options 

| Flag | Effect |
|---|---|
| `--with-tiktoken` | Also install `tiktoken` for accurate token counting |
| `--no-hook` | Skip the Claude Code hook |
| `--no-deps` | Skip pip install |
| `--uninstall` | Remove the scripts and hook |

### 2.4. Update 

Re-run `/spec-lint:install` (or `install.py` directly) — it updates scripts and hook in place.

## 3. Federation discovery 

The script walks **up from the edited file's path** (not cwd) until it finds a `.spec-config.yaml` with `kind: root`. A `kind: child` config follows its `parent:` pointer (or keeps walking up). If no root is found, the script silently does nothing in hook mode and skips the file in `lint` mode.

The companion `.spec-config.cache.json` is written next to the root config and stores per-file mtime + parsed anchors, so repeat sweeps stay fast.

## 4. Running it 

### 4.1. Manual sweep 

```sh
# Lint specific files (federation root auto-discovered per file)
python3 ~/.local/lib/spec-lint/spec_lint.py lint path/to/file.md another.md

# Sweep a whole repo
find docs -name '*.md' | xargs python3 ~/.local/lib/spec-lint/spec_lint.py lint --format json
```

Exit code: `1` if any error-severity finding was produced, otherwise `0`.

Flags:

1. `--format text|json` — defaults to `text`.
2. `--config PATH` — explicit `.spec-config.yaml`; skips walk-up discovery.

### 4.2. Claude Code hook 

After installation the hook runs automatically on every `Edit` or `Write`. When findings exist it emits `{"decision":"block","reason":"..."}` so Claude sees the violations before continuing. Exit is always `0` — a missing or malformed config never breaks Claude's flow.

### 4.3. Claude Code skills

Use `/spec-lint:lint` to run the linter interactively on named files, the current file, or the whole federation. The skill parses JSON output and offers targeted fixes per check type.

Use `/spec-lint:add-hook` to wire pre-commit hooks into every git repo found under a directory (see §4.4).

Use `/spec-lint:init` to create `.spec-config.yaml` interactively. It discovers all git repos under a chosen root, analyses each `CLAUDE.md` to pre-populate spec paths and namespace, walks through a fixed question sequence in chat, then writes a root config plus `kind: child` configs for any additional repos selected. Optionally installs pre-commit hooks in the same pass.

### 4.4. Pre-commit hook 

The pre-commit hook runs `spec_lint.py lint` on all staged `.md` files and blocks the commit on any error-severity finding.

Install into every git repo under the current directory:

```sh
python3 ~/.local/lib/spec-lint/add_hook.py
# or target a specific root
python3 ~/.local/lib/spec-lint/add_hook.py /path/to/monorepo
```

The script is idempotent: it updates managed hooks in place and skips repos that already have a custom pre-commit hook without the managed marker (those are printed so you can merge manually).

Remove managed hooks:

```sh
python3 ~/.local/lib/spec-lint/add_hook.py --uninstall
```

### 4.5. CI / full-repo sweep 

```sh
python3 ~/.local/lib/spec-lint/spec_lint.py lint $(git ls-files '*.md')
```

## 5. Config shape 

```yaml
kind: root
name: your-system
namespace: your-system          # used in spec://your-domain/... URIs

# Federation members — declare all repos even for single-repo projects.
# role: specs  = source of truth for intent
# role: service = implementation repo (language: csharp | typescript | ...)
members:
  docs:
    path: ./docs
    role: specs
  your-service:
    path: ./your-service
    role: service
    language: csharp

# Files subject to spec-grade checks (anchor hygiene, URI resolution, token budgets).
spec_paths:
  - "docs/docs/specs/modules/**/*.md"
  - "docs/docs/adr/*.md"

# Files that must never be scanned.
exclude:
  - "**/.git/**"
  - "**/node_modules/**"

uri:
  scheme: "spec://"
  require_namespace: your-system

# Subset of spec_paths containing acceptance criteria linked to code.
# Only docs with '> Status: Implemented' are enforced; others are silent.
# Glob notes: * matches within one path segment; ** crosses any number of segments.
# Example: "phases/*/*.md" = files directly in a phase folder.
#          "phases/**/design/*.md" = any design/ subfolder at any depth.
tech_spec_paths:
  - "specs/modules/**/*.md"

# Where back-references (spec:// links) must appear for each AC anchor.
test_paths:
  - "backend/tests/**/*.cs"
  - "ui/src/**/*.test.ts"

checks:
  anchor_hygiene:
    enabled: true
    require_on_headings: [h1, h2, h3, h4]

  uri_resolution:
    enabled: true
    severity: error

  token_budgets:
    enabled: true
    severity: warning
    budgets:
      "docs/specs/modules/**/*.md": 5000

  bidirectional_coverage:
    enabled: true             # gated by tech_spec_paths and test_paths above

  md_link_resolution:
    enabled: true
    severity: error           # relative [text](path) links must resolve on disk
```

## 6. Companion skills {#skills}

1. **`/md-spec`** — applies the canonical anchor convention to a doc. Running this on any spec satisfies `anchor_hygiene` by construction.
2. **`/spec-lint:install`** — bootstraps the plugin after marketplace installation: runs `install.py` to copy scripts, install deps, and wire the Claude Code hook.
3. **`/spec-lint:init`** — guided interactive setup of `.spec-config.yaml` for a repo or monorepo federation. Discovers git repos, analyses `CLAUDE.md` files to pre-populate answers, asks a fixed sequence of structured form questions, then writes root + child configs and optionally installs hooks.
4. **`/spec-lint:lint`** — interactive linter invocation with guided fix offers.
5. **`/spec-lint:add-hook`** — installs pre-commit hooks into all git repos found under a directory. Blocks commits on lint errors; idempotent; skips repos with custom hooks.
6. **`spec-gardening`** (planned) — judgment-bearing sweeps for stale REVIEW markers, orphan anchors, etc. Calls this script for the deterministic part.

## 7. Known gaps {#gaps}

1. `lint` mode silently skips a file when its config can't be found or parsed; should fail loudly. Hook mode correctly stays silent on bad config.
2. No formal child-config schema yet — `kind: child` + `parent:` is exercised in practice but there is no JSON Schema validator for the config shape.
3. `bidirectional_coverage` enforces presence of a back-reference but not its accuracy: a stale or wrong `spec://` URI in a test still counts as coverage.
