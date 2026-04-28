---
name: init
description: Guided interactive setup of .spec-config.yaml for a repo or monorepo federation. Discovers git repos, analyses CLAUDE.md files to pre-populate answers, walks the user through a fixed question sequence, then writes root + child configs and optionally installs git hooks.
---

# spec-lint:init

Interactively create `.spec-config.yaml` configuration for a spec-lint federation.
One root config is always created; child configs are created for every other repo selected.

**Important:** Use the `AskUserQuestion` tool for every interactive prompt in this skill.
Never ask questions as plain text and wait for a reply — always use structured tool calls.

## 1. Determine the root directory

Use `AskUserQuestion`:

```
question: "Which directory should I scan for git repos?"
header: "Root dir"
options:
  - label: "Current working directory"
    description: <show absolute cwd path>
  - label: "Parent directory"
    description: <show parent of cwd>
```

The "Other" option lets the user type a custom path. Resolve `~` and relative paths to absolute.

## 2. Discover git repositories

```sh
find <root_dir> -name ".git" -type d -not -path "*/.git/*" | sed 's|/.git$||' | sort
```

For each repo found, check:
1. Whether `CLAUDE.md` exists at the repo root.
2. Whether `.spec-config.yaml` already exists at the repo root (will overwrite — note this).

Print a brief discovery summary before continuing.

If no git repos are found, tell the user and stop.

## 3. Analyse CLAUDE.md files (AI pass — no user interaction)

For each repo that has a `CLAUDE.md`, read it silently and infer:

- **Candidate namespace** — explicit project name in CLAUDE.md, or the repo folder name as fallback.
- **Candidate spec paths** — folder mentions: `docs/`, `specs/`, `design/`, `architecture/`, any `.md` glob patterns.
- **Candidate test paths** — folder mentions: `tests/`, `test/`, `*.test.*`, acceptance criteria locations.

Store per-repo. If nothing detected, the field stays empty and the user must type a value.

## 4. Select repos to configure

Skip this step if only one repo was found — it is automatically the root.

If multiple repos, use `AskUserQuestion` with `multiSelect: true`:

```
question: "Which repos should be included in the federation?"
header: "Repos"
options: one per discovered repo, label = short path, description = CLAUDE.md ✓/— and existing config warning
```

The repo whose path is closest to (or equal to) `<root_dir>` is the **root**. All others are **children**. Tell the user which will be root after they confirm.

## 5. Root repo interview

Ask Q1–Q6 sequentially. Each is a separate `AskUserQuestion` call — do not batch them.
Wait for the answer before proceeding to the next question.

**Q1 — Namespace**

```
question: "Namespace for the federation (used in spec:// URIs)?"
header: "Namespace"
multiSelect: false
options:
  - if detected: label = "<detected value>", description = "Detected from CLAUDE.md"
  - label: "Use repo folder name: <folder-name>", description = "Safe default"
```

The "Other" option lets the user type a custom namespace.

**Q2 — Spec paths**

```
question: "Which glob patterns point to your spec/doc Markdown files?"
header: "Spec paths"
multiSelect: true
options: one per detected glob (label = the glob, description = "Detected from CLAUDE.md")
         plus common defaults not already detected:
           "docs/**/*.md", "specs/**/*.md", "architecture/**/*.md"
```

The "Other" option lets the user add one custom glob. If they need more, they can edit the config afterward.

**Q3 — Test paths**

```
question: "Which glob patterns point to your test files? (for bidirectional coverage)"
header: "Test paths"
multiSelect: true
options:
  - if detected: one per detected path (label = the glob)
  - label: "Skip — no test path coverage", description = "bidirectional_coverage will be disabled"
  - common defaults: "tests/**/*.cs", "tests/**/*.py", "src/**/*.test.ts"
```

If the user selects "Skip", set `bidirectional_coverage.enabled: false` and omit `test_paths` and `tech_spec_paths`.

**Q4 — Tech-spec paths** *(only ask if test paths were selected in Q3)*

```
question: "Which paths contain specs with acceptance criteria directly reflected in tests?
           Only docs with '> Status: Implemented' in those paths will be checked
           for back-references."
header: "Tech-spec paths"
multiSelect: false
options:
  - detected subfolders of spec_paths that suggest implementation specs
    (e.g. "docs/specs/", "docs/modules/") — one option per candidate
  - label: "Same as all spec_paths", description = "Every Implemented doc must have back-refs"
```

The "Other" option lets the user type a glob, folder path, or free-text description.
If free text is given rather than a glob, convert it to a glob pattern before writing.
Store the result as one or more `tech_spec_paths` globs.

**Q5 — Token budget**

```
question: "Max token budget per spec file?"
header: "Token budget"
multiSelect: false
options:
  - label: "5000 (default)", description = "~3750 words"
  - label: "2000", description = "Short focused specs"
  - label: "8000", description = "Large design documents"
  - label: "Unlimited", description = "Disable token_budgets check"
```

The "Other" option lets the user type a custom number.

**Q6 — Exclusions**

```
question: "Which path patterns should be excluded from linting?"
header: "Exclusions"
multiSelect: true
options:
  - label: "**/.git/**", description = "Git internals (recommended)"
  - label: "**/node_modules/**", description = "Node dependencies"
  - label: "**/bin/**", description = "Build output"
  - label: "**/.build/**", description = "Build artifacts"
```

Pre-select all four as defaults. The "Other" option lets the user add a custom pattern.

## 6. Child repo interview (repeat per child)

For each child repo, ask two questions sequentially using `AskUserQuestion`:

**Child Q1 — Spec paths** (same format as root Q2, but for this child repo)

**Child Q2 — Namespace**

```
question: "Does <repo-name> use the same namespace as the root, or its own?"
header: "Namespace"
multiSelect: false
options:
  - label: "Same as root: <root-ns>", description = "Recommended — one shared namespace"
  - label: "Own namespace: <folder-name>", description = "Separate namespace for this repo"
```

The "Other" option lets the user type a custom namespace.

## 7. Git hook installation

Use `AskUserQuestion` with `multiSelect: true`:

```
question: "Install the spec-lint pre-commit hook in which repos?"
header: "Git hooks"
options: one per selected repo (label = short path)
         plus label: "None — skip hook installation"
```

## 8. Write configs

**Root `.spec-config.yaml`** — write to `<root-repo>/.spec-config.yaml`.
If the file already exists, use `AskUserQuestion` to confirm overwrite before writing.

```yaml
kind: root
name: <namespace>
namespace: <namespace>          # used in spec://<namespace>/... URIs

# Federation members — declare all repos even for single-repo projects.
# role: specs  = source of truth for intent
# role: service = implementation repo (language: csharp | typescript | python | ...)
members:
  <- for each selected repo: ->
  <repo-name>:
    path: <relative path from root config to repo>
    role: <specs | service>
    <- language: <lang>  — omit for specs repos ->

# Files subject to spec-grade checks (anchor hygiene, URI resolution, token budgets).
spec_paths:
<- one entry per selected glob ->

# Files that must never be scanned.
exclude:
<- one entry per selected exclusion ->

uri:
  scheme: "spec://"
  require_namespace: <namespace>

<- if test paths selected: ->
# Subset of spec_paths containing acceptance criteria linked to code.
# Only docs with '> Status: Implemented' are enforced; others are silent.
tech_spec_paths:
<- one entry per tech_spec glob ->

# Where back-references (spec:// links) must appear for each AC anchor.
test_paths:
<- one entry per test glob ->

<- end if ->
checks:
  anchor_hygiene:
    enabled: true
    require_on_headings: [h1, h2, h3]

  uri_resolution:
    enabled: true
    severity: error

  md_link_resolution:
    enabled: true
    severity: error

  token_budgets:
    enabled: <false if Unlimited selected, true otherwise>
    severity: warning
    budgets:
<- one entry per spec glob: <budget> — omit budgets block if Unlimited ->

  bidirectional_coverage:
    enabled: <true if test paths selected, false otherwise>
```

**Child `.spec-config.yaml`** — write to `<child-repo>/.spec-config.yaml` for each child:

```yaml
kind: child
parent: <relative path from child repo root to root .spec-config.yaml>

# Files in this repo subject to spec-grade checks.
spec_paths:
<- child spec globs ->

namespace: <child namespace — omit if same as root>
```

Tell the user the path of each file written.

## 9. Install git hooks (if requested)

For each selected repo run:

```sh
python3 ~/.local/lib/spec-lint/add_hook.py <repo-path>
```

Report: repos updated, repos skipped, reason for any skip.

## 10. Post-setup recommendations

Print a numbered list. Include only items that apply:

1. Any repo with no `CLAUDE.md` — suggest adding one so future runs can auto-detect spec paths.
2. Any spec path glob that matched zero files at write time — flag it as possibly wrong.
3. Any child repo where namespace was left as root default — confirm this is intentional.
4. If `bidirectional_coverage` is disabled — mention how to enable it later.
5. Suggest running `/spec-lint:lint` immediately to baseline the current state.

## Error handling

- If `~/.local/lib/spec-lint/spec_lint.py` is missing, tell the user to run `python3 /path/to/arch-linter/scripts/install.py` and stop.
- If no git repos are found under the chosen directory, say so and stop.
- If a `.spec-config.yaml` already exists and will be overwritten, confirm via `AskUserQuestion` before writing.
