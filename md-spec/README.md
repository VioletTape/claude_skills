# md-spec {#root}

Formatting rules for Markdown specification, design, and requirements documents.

## 1. What it does {#what-it-does}

A prompt-only Claude Code plugin that enforces consistent conventions when creating or editing spec documents:

1. **Heading anchors** — every heading carries an explicit `{#id}` anchor. H1 is always `{#root}`, H2 is kebab-case (`{#section-name}`), H3+ extends the parent with dot notation (`{#parent.child}`). Acceptance Criteria always uses `{#ac}`.
2. **Numbered lists** — all lists must be numbered. Unordered markers (`-`, `*`, `•`) are never used unless the user explicitly requests them.
3. **Acceptance criteria format** — always a numbered list with verification method in parentheses. Never a table.
4. **Cross-file references** — use short anchors in links, not prefixed forms.
5. **Legacy cleanup** — when editing files with old-style prefixed anchors, migrate them to the canonical form.

## 2. Installation {#installation}

Install the plugin from the marketplace. No additional setup is required — this is a prompt-only plugin with no runtime dependencies.

After installation, the `/md-spec` skill is available in any Claude Code session.

## 3. Usage {#usage}

The skill activates automatically when Claude Code detects you are creating or editing a Markdown specification document. You can also invoke it explicitly:

```
/md-spec
```

## 4. Companion plugin {#companion}

For deterministic linting (CI, pre-commit hooks, automated enforcement), see the `spec-lint` plugin in this marketplace. The `/md-spec` skill satisfies `spec-lint`'s `anchor_hygiene` check by construction.
