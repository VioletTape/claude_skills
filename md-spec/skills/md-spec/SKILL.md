---
name: md-spec
description: Apply when creating or editing any Markdown specification, design, or requirements document. Enforces heading anchor conventions, section structure, and acceptance criteria format.
---

# Markdown Spec Formatting Rules

## Heading Anchors

Every heading must carry an explicit `{#id}` anchor (Pandoc style). Three rules:

1. The root `#` heading always gets `{#root}` — never derived from the title or filename.
2. `##` sections get a short kebab-case anchor: `{#section-name}`
3. `###` sub-sections get a hierarchical dot-notation anchor: `{#parent.child}`

The Acceptance Criteria section always uses `{#ac}` — not `{#acceptance}` or any other variant.

**Correct:**
```markdown
# Site Creation {#root}
## Geo Position {#geo}
### Address Input {#geo.address-input}
## Acceptance Criteria {#ac}
```

**Wrong — never prefix with the document slug:**
```markdown
# Site Creation {#site-creation}
## Geo Position {#site-creation.geo}
## Acceptance Criteria {#site-creation.acceptance}
```

## Lists

All lists MUST be numbered. Never use unordered lists (-, *, •) in documentation unless the user explicitly requests it.

**Correct:**
```markdown
1. First item
2. Second item
3. Third item
```

**Wrong:**
```markdown
- First item
- Second item
- Third item
```

This applies to all list contexts: feature lists, constraint lists, step lists, option enumerations, and inline prose lists.

## Acceptance Criteria Format

Always a numbered list. Never a table. Verification method in parentheses at the end of each item. No per-item anchors — the list number is enough for referencing.

**Correct:**
```markdown
## Acceptance Criteria {#ac}

1. All mandatory fields are validated before save (Unit test)
2. Created record is immediately retrievable (Integration test)
```

**Wrong:**
```markdown
| # | Criterion | Verifiable by |
|---|---|---|
| AC-1 | ... | Unit test |
```

## Cross-File Anchor References

Use the short anchor in links, not the prefixed form.

1. Correct: `[§Geo Position](site-creation.md#geo)`
2. Wrong: `[§Geo Position](site-creation.md#site-creation.geo)`

## When Editing Existing Files With Old-Style Anchors

If the file uses prefixed anchors (e.g. `{#site-creation.purpose}`):

1. Replace the root anchor with `{#root}`
2. Strip the document prefix from all other anchors
3. Update all inline links in the same file
4. Check for cross-file references in sibling files and update those too
