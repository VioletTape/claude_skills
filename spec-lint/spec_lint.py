#!/usr/bin/env python3
"""spec_lint v0 — anchor hygiene, URI resolution, token budgets for a spec federation."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable

import yaml

__version__ = "0.2.5"

# -- table of contents -------------------------------------------------------
# L40   SpecLinkRef          dataclass for a single // spec:// hit in a test file
# L63   FEDERATION DISCOVERY find_config, ConfigError, config validation helpers
# L226  GLOB MATCHING        _glob_to_regex, glob_match, in_scope
# L286  FILE ENUMERATION     AnchorCache, TestRefIndex, enumerate_*
# L472  URI + ANCHOR PARSING resolve_logical_uri, parse_anchors, parse_status_value,
#                            extract_spec_uris
# L562  TEST LINK COLLECTION _extract_test_name, collect_spec_link_refs,
#                            build_resolved_link_index
# L671  COVERAGE             count_ac_lines, normalize_rel_prefix, match_member_name,
#                            compute_coverage_summary, compute_coverage_v2
# L857  CHECKS               check_anchor_hygiene, check_uri_resolution,
#                            check_token_budgets, check_bidirectional_coverage,
#                            check_md_links
# L1143 PIPELINE             FederationContext, lint_file
# L1275 RENDERING            render_text, aggregate_coverage, render_coverage_text
# L1341 CLI                  cmd_lint, cmd_post_tool_use, cmd_pre_commit_impact, main
# ----------------------------------------------------------------------------


@dataclass
class SpecLinkRef:
    uri: str
    file: str
    line: int
    test_name: str | None


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*\{#([a-z0-9._-]+)\}\s*$")
HEADING_NO_ANCHOR_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
URI_RE = re.compile(r"spec://([a-z0-9_-]+)/([^\s#)\]\"'`<>]+)(?:#([a-z0-9._-]+))?")
SPEC_COMMENT_RE = re.compile(r"//\s*(spec://\S+)", re.IGNORECASE)
FENCE_RE = re.compile(r"^(```|~~~)")
STATUS_LINE_RE = re.compile(r"^>\s*Status:\s*(.*?)\s*$", re.IGNORECASE)
STATUS_SCAN_WINDOW = 20

MD_LINK_RE = re.compile(r"(?<!!)\[([^\]]*)\]\(([^)]+)\)")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+\-.]*://", re.IGNORECASE)
_ABS_PATH_RE = re.compile(r"^(?:/|\\|[a-zA-Z]:)")

# Directories pruned during file enumeration. exclude-globs still get the
# final say, but pruning here keeps the walk fast on large repos.
_PRUNE_DIRS = frozenset({
    ".git", ".idea", ".vs", ".claude", ".context",
    "node_modules", "bin", "obj", "dist",
})


# -- federation discovery ---------------------------------------------------

class ConfigError(Exception):
    """A .spec-config.yaml exists but is unreadable or malformed."""

    def __init__(self, path: Path, message: str):
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


def _require_mapping(value: object, config_path: Path, field: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(config_path, f"'{field}' must be a mapping")
    return value


def _normalize_budget_map(value: object, config_path: Path,
                          field: str) -> dict[str, int]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(
            config_path,
            f"'{field}' must be a mapping of glob -> integer budget",
        )

    normalized: dict[str, int] = {}
    for pattern, raw_budget in value.items():
        if not isinstance(pattern, str):
            raise ConfigError(
                config_path,
                f"'{field}' keys must be strings; got {type(pattern).__name__}",
            )
        if isinstance(raw_budget, bool):
            raise ConfigError(
                config_path,
                f"'{field}.{pattern}' must be an integer; got {raw_budget!r}",
            )
        try:
            normalized[pattern] = int(raw_budget)
        except (TypeError, ValueError) as e:
            raise ConfigError(
                config_path,
                f"'{field}.{pattern}' must be an integer; got {raw_budget!r}",
            ) from e

    return normalized


def _normalize_members_map(value: object, config_path: Path,
                           field: str) -> dict[str, dict]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(config_path, f"'{field}' must be a mapping")

    normalized: dict[str, dict] = {}
    for member_name, raw_member in value.items():
        if not isinstance(member_name, str):
            raise ConfigError(
                config_path,
                f"'{field}' keys must be strings; got {type(member_name).__name__}",
            )
        if not isinstance(raw_member, dict):
            raise ConfigError(
                config_path,
                f"'{field}.{member_name}' must be a mapping",
            )

        path = raw_member.get("path")
        if not isinstance(path, str):
            raise ConfigError(
                config_path,
                f"'{field}.{member_name}.path' must be a string",
            )

        normalized_member = dict(raw_member)
        normalized_member["path"] = path
        normalized[member_name] = normalized_member

    return normalized


def validate_config_shape(cfg: dict, config_path: Path) -> dict:
    """Validate nested config shapes that would otherwise crash during lint."""
    normalized = dict(cfg)
    normalized["members"] = _normalize_members_map(
        cfg.get("members"),
        config_path,
        "members",
    )

    checks = _require_mapping(cfg.get("checks"), config_path, "checks")
    if not checks:
        return normalized

    normalized_checks = dict(checks)
    tb = _require_mapping(
        checks.get("token_budgets"),
        config_path,
        "checks.token_budgets",
    )
    if tb:
        normalized_tb = dict(tb)
        normalized_tb["budgets"] = _normalize_budget_map(
            tb.get("budgets"),
            config_path,
            "checks.token_budgets.budgets",
        )
        normalized_checks["token_budgets"] = normalized_tb

    normalized["checks"] = normalized_checks
    return normalized


def find_config(start: Path) -> tuple[Path, dict] | None:
    """Walk up from start until a kind:root .spec-config.yaml is found.

    Returns (root_dir, config_dict), or None when no config exists anywhere on
    the path. Raises ConfigError when a config file is found but cannot be
    parsed — callers decide whether to surface that to the user.
    """
    visited: set[Path] = set()
    current = start.resolve()
    if current.is_file():
        current = current.parent

    while True:
        if current in visited:
            return None
        visited.add(current)

        cfg_path = current / ".spec-config.yaml"
        if cfg_path.is_file():
            try:
                with cfg_path.open("r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}
            except OSError as e:
                raise ConfigError(cfg_path, f"could not read file: {e}") from e
            except yaml.YAMLError as e:
                raise ConfigError(cfg_path, f"YAML parse error: {e}") from e
            if not isinstance(cfg, dict):
                raise ConfigError(cfg_path, "top-level YAML must be a mapping")
            cfg = validate_config_shape(cfg, cfg_path)

            kind = cfg.get("kind")
            if kind == "root":
                return current, cfg
            if kind == "child":
                parent = cfg.get("parent")
                if parent:
                    parent_path = (current / parent).resolve()
                    return find_config(parent_path)
                # else fall through and keep walking up

        if current.parent == current:
            return None
        current = current.parent


# -- glob matching with ** support -----------------------------------------

def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a glob with ** support to a regex matching forward-slash paths."""
    i = 0
    out = ["^"]
    while i < len(pattern):
        c = pattern[i]
        if c == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                # ** — match across path separators
                # Consume optional trailing /
                j = i + 2
                if j < len(pattern) and pattern[j] == "/":
                    out.append("(?:.*/)?")
                    i = j + 1
                    continue
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = pattern.find("]", i + 1)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                out.append(pattern[i:j + 1])
                i = j + 1
        elif c == "/":
            out.append("/")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


_GLOB_CACHE: dict[str, re.Pattern[str]] = {}


def glob_match(pattern: str, path: str) -> bool:
    rx = _GLOB_CACHE.get(pattern)
    if rx is None:
        rx = _glob_to_regex(pattern)
        _GLOB_CACHE[pattern] = rx
    return rx.match(path.replace("\\", "/")) is not None


def in_scope(rel_path: str, spec_paths: list[str], exclude: list[str]) -> bool:
    if any(glob_match(p, rel_path) for p in exclude):
        return False
    return any(glob_match(p, rel_path) for p in spec_paths)


# -- anchor index cache ----------------------------------------------------

class AnchorCache:
    def __init__(self, root: Path):
        self.root = root
        self.path = root / ".spec-config.cache.json"
        self.data: dict[str, dict] = {}
        self.test_ref_data: dict[str, dict] = {}
        self.dirty = False
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                raw = json.load(f)
            if raw.get("version") != 1:
                return
            self.data = raw.get("anchors", {}) or {}
            self.test_ref_data = raw.get("test_refs", {}) or {}
        except (OSError, json.JSONDecodeError, ValueError):
            # Corrupt cache — start fresh, never fail because of it.
            self.data = {}
            self.test_ref_data = {}

    def get_anchors(self, rel_path: str) -> list[str] | None:
        """Return anchors for the file at rel_path, parsing if needed.

        Returns None if the file does not exist.
        """
        abs_path = self.root / rel_path
        if not abs_path.is_file():
            self.data.pop(rel_path, None)
            self.dirty = True
            return None

        try:
            mtime = abs_path.stat().st_mtime
        except OSError:
            return None

        entry = self.data.get(rel_path)
        if entry and entry.get("mtime") == mtime:
            return entry.get("anchors", [])

        anchors = parse_anchors(abs_path)
        self.data[rel_path] = {"mtime": mtime, "anchors": anchors}
        self.dirty = True
        return anchors

    def save(self) -> None:
        if not self.dirty:
            return
        try:
            tmp = self.path.with_suffix(".json.tmp")
            with tmp.open("w", encoding="utf-8", newline="\n") as f:
                json.dump({
                    "version": 1,
                    "anchors": self.data,
                    "test_refs": self.test_ref_data,
                }, f, indent=2)
            os.replace(tmp, self.path)
        except OSError as e:
            print(f"spec_lint: warning: could not persist cache: {e}", file=sys.stderr)


def enumerate_matching_files(root: Path, include_paths: list[str],
                             exclude: list[str]) -> list[str]:
    """List relative paths under root matching include_paths and not exclude."""
    if not include_paths:
        return []
    results: list[str] = []
    root_str = str(root)
    for dirpath, dirnames, filenames in os.walk(root_str):
        dirnames[:] = [d for d in dirnames if d not in _PRUNE_DIRS]
        rel_dir = os.path.relpath(dirpath, root_str).replace("\\", "/")
        if rel_dir == ".":
            rel_dir = ""
        for fn in filenames:
            rel = f"{rel_dir}/{fn}" if rel_dir else fn
            if any(glob_match(p, rel) for p in exclude):
                continue
            if any(glob_match(p, rel) for p in include_paths):
                results.append(rel)
    results.sort()
    return results


def enumerate_spec_files(root: Path, spec_paths: list[str],
                         exclude: list[str]) -> list[str]:
    """List relative paths under root matching spec_paths and not exclude."""
    return enumerate_matching_files(root, spec_paths, exclude)


class TestRefIndex:
    def __init__(self, root: Path, cfg: dict, cache: AnchorCache,
                 spec_files: list[str]):
        self.root = root
        self.cfg = cfg
        self.cache = cache
        self.spec_files = spec_files
        self._refs: set[str] | None = None
        self._file_refs: dict[str, list[str]] | None = None

    def referenced_anchors(self) -> set[str]:
        if self._refs is None:
            self._refs = self._build_index()
        return self._refs

    def file_references(self) -> dict[str, list[str]]:
        if self._refs is None:
            self._refs = self._build_index()
        return self._file_refs or {}

    def _build_index(self) -> set[str]:
        test_paths = self.cfg.get("test_paths") or []
        if not test_paths:
            self._file_refs = {}
            return set()

        current_files = enumerate_matching_files(
            self.root,
            test_paths,
            self.cfg.get("exclude") or [],
        )
        current_set = set(current_files)

        stale = [rel for rel in self.cache.test_ref_data if rel not in current_set]
        for rel_path in stale:
            self.cache.test_ref_data.pop(rel_path, None)
            self.cache.dirty = True

        refs: set[str] = set()
        file_refs_index: dict[str, list[str]] = {}
        for rel_path in current_files:
            abs_path = self.root / rel_path
            try:
                mtime = abs_path.stat().st_mtime
            except OSError:
                continue

            entry = self.cache.test_ref_data.get(rel_path)
            if entry and entry.get("mtime") == mtime:
                file_refs = entry.get("refs", [])
            else:
                file_refs = self._parse_test_refs(abs_path)
                self.cache.test_ref_data[rel_path] = {
                    "mtime": mtime,
                    "refs": file_refs,
                }
                self.cache.dirty = True

            file_refs_index[rel_path] = list(file_refs)
            refs.update(file_refs)

        self._file_refs = file_refs_index
        return refs

    def _parse_test_refs(self, path: Path) -> list[str]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return []

        require_ns = ((self.cfg.get("uri") or {}).get("require_namespace")
                      or self.cfg.get("namespace"))
        refs: set[str] = set()
        for m in URI_RE.finditer(text):
            ns, target_path, anchor = m.group(1), m.group(2), m.group(3)
            if not anchor or (require_ns and ns != require_ns):
                continue

            resolved, _ = resolve_logical_uri(target_path, self.spec_files)
            if resolved is None:
                continue

            anchors = self.cache.get_anchors(resolved)
            if anchors is None or anchor not in anchors:
                continue

            refs.add(f"{resolved}#{anchor}")

        return sorted(refs)


def resolve_logical_uri(logical_path: str,
                        spec_files: list[str]) -> tuple[str | None, list[str]]:
    """Resolve a spec:// logical path against the in-scope spec file set.

    The path is treated as a logical id: `.md` is appended if absent, and the
    target is the unique spec file whose path equals or has-as-suffix the id
    on a path-segment boundary. Returns (resolved, all_matches).
    """
    target = logical_path if logical_path.endswith(".md") else logical_path + ".md"
    matches: list[str] = []
    suffix = "/" + target
    for f in spec_files:
        if f == target or f.endswith(suffix):
            matches.append(f)
    return (matches[0] if len(matches) == 1 else None), matches


def parse_anchors(path: Path) -> list[str]:
    anchors: list[str] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            in_fence = False
            for line in f:
                if FENCE_RE.match(line):
                    in_fence = not in_fence
                    continue
                if in_fence:
                    continue
                m = HEADING_RE.match(line.rstrip("\n"))
                if m:
                    anchors.append(m.group(3))
    except OSError:
        return []
    return anchors


def parse_status_value(text: str) -> str | None:
    lines = text.splitlines()
    in_fence = False
    h1_index: int | None = None

    for idx, line in enumerate(lines):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        m = HEADING_RE.match(line)
        if not m:
            m = HEADING_NO_ANCHOR_RE.match(line)
        if not m or len(m.group(1)) != 1:
            continue
        h1_index = idx
        break

    if h1_index is None:
        return None

    in_fence = False
    for line in lines[h1_index + 1:h1_index + 1 + STATUS_SCAN_WINDOW]:
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        status_match = STATUS_LINE_RE.match(line)
        if not status_match:
            continue

        value = status_match.group(1).strip()
        return value or None

    return None


def extract_spec_uris(text: str, require_namespace: str | None = None) -> list[str]:
    uris: set[str] = set()
    for m in URI_RE.finditer(text):
        ns, target_path, anchor = m.group(1), m.group(2), m.group(3)
        if require_namespace and ns != require_namespace:
            continue
        uri = f"spec://{ns}/{target_path}"
        if anchor:
            uri += f"#{anchor}"
        uris.add(uri)
    return sorted(uris)


def _extract_test_name(line: str, file_ext: str) -> str | None:
    if file_ext == ".cs":
        match = re.search(
            r"(?:public\s+)?(?:class|async\s+Task|Task|void)\s+(\w+)",
            line,
        )
        return match.group(1) if match else None

    if file_ext in {".ts", ".tsx"}:
        match = re.search(r"\b(?:it|test|describe)\s*\(\s*['\"`]([^'\"`]+)", line)
        if match:
            return match.group(1)

        match = re.search(r"class\s+(\w+)", line)
        return match.group(1) if match else None

    return None


def collect_spec_link_refs(path: Path, rel_path: str) -> list[SpecLinkRef]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    refs: list[SpecLinkRef] = []
    block: list[tuple[int, str]] = []
    file_ext = path.suffix.lower()

    for line_no, raw_line in enumerate(lines, start=1):
        match = SPEC_COMMENT_RE.search(raw_line)
        if match:
            block.append((line_no, match.group(1)))
            continue

        if block:
            declaration_line = raw_line.strip()
            if file_ext == ".cs":
                candidate_index = line_no - 1
                skipped = 0
                while (
                    candidate_index < len(lines)
                    and skipped < 3
                    and re.match(r"^\s*\[", lines[candidate_index])
                ):
                    candidate_index += 1
                    skipped += 1
                if candidate_index < len(lines):
                    declaration_line = lines[candidate_index].strip()

            test_name = _extract_test_name(declaration_line, file_ext)
            for ref_line, uri in block:
                refs.append(SpecLinkRef(
                    uri=uri,
                    file=rel_path,
                    line=ref_line,
                    test_name=test_name,
                ))
            block.clear()

    if block:
        for ref_line, uri in block:
            refs.append(SpecLinkRef(
                uri=uri,
                file=rel_path,
                line=ref_line,
                test_name=None,
            ))

    return refs


def build_resolved_link_index(
    refs: list[SpecLinkRef],
    spec_files: list[str],
    tech_spec_files: list[str] | None = None,
) -> tuple[dict[str, list[SpecLinkRef]], list[SpecLinkRef]]:
    """Returns (covered_docs, orphan_refs). Mutually exclusive by construction."""
    covered_docs: dict[str, list[SpecLinkRef]] = {}
    orphan_refs: list[SpecLinkRef] = []

    for ref in refs:
        raw = ref.uri
        if "://" in raw:
            raw = raw.split("://", 1)[1]
        logical_path = raw.split("#")[0]
        resolved, _ = resolve_logical_uri(logical_path, spec_files)
        if resolved is None:
            # Stem fallback — prefer tech_spec_files to resolve ambiguity
            stem = logical_path.rstrip("/").split("/")[-1]
            if not stem.endswith(".md"):
                stem += ".md"
            for candidate_pool in ([tech_spec_files] if tech_spec_files else []) + [spec_files]:
                stem_matches = [f for f in candidate_pool if f == stem or f.endswith("/" + stem)]
                if len(stem_matches) == 1:
                    resolved = stem_matches[0]
                    break
        if resolved is not None:
            covered_docs.setdefault(resolved, []).append(ref)
        else:
            orphan_refs.append(ref)

    return covered_docs, orphan_refs


def is_ac_anchor(anchor: str) -> bool:
    return anchor == "ac" or anchor.startswith("ac.")


def count_ac_lines(text: str) -> int:
    in_ac_section = False
    pre_fence = False
    in_fence = False
    count = 0

    for raw_line in text.splitlines():
        if not in_ac_section:
            if FENCE_RE.match(raw_line):
                pre_fence = not pre_fence
                continue
            if not pre_fence and re.match(r"^##\s+.*\{#ac\}", raw_line):
                in_ac_section = True
            continue

        if FENCE_RE.match(raw_line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        if re.match(r"^##\s+", raw_line):
            break

        stripped = raw_line.strip()
        if not stripped:
            continue
        if raw_line.startswith(">") or raw_line.startswith("#"):
            continue

        if re.match(r"^\d+[.)]\s*", raw_line):
            count += 1
            continue
        if re.match(r"^[-*]\s+", raw_line):
            count += 1

    return count


def normalize_rel_prefix(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    if normalized in {"", "."}:
        return ""
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def match_member_name(rel_path: str, members: dict[str, dict]) -> str | None:
    best_name: str | None = None
    best_prefix_len = -1
    normalized_rel_path = rel_path.replace("\\", "/").strip("/")

    for member_name, member_cfg in members.items():
        prefix = normalize_rel_prefix(str(member_cfg.get("path", "")))
        if prefix:
            matched = (
                normalized_rel_path == prefix
                or normalized_rel_path.startswith(prefix + "/")
            )
        else:
            matched = True

        if matched and len(prefix) > best_prefix_len:
            best_name = member_name
            best_prefix_len = len(prefix)

    return best_name


def compute_coverage_summary(ctx: "FederationContext") -> dict:
    implemented_ac_refs: set[str] = set()
    implemented_doc_count = 0
    implemented_ac_count = 0

    for rel_path in ctx.spec_files:
        abs_path = ctx.root / rel_path
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue

        status = parse_status_value(text)
        if status is None or status.casefold() != "implemented":
            continue

        implemented_doc_count += 1
        implemented_ac_count += count_ac_lines(text)

        anchors = ctx.cache.get_anchors(rel_path)
        if anchors is None:
            continue

        for anchor in anchors:
            if is_ac_anchor(anchor):
                implemented_ac_refs.add(f"{rel_path}#{anchor}")

    test_backlinks: dict[str, int] = {}
    members = ctx.cfg.get("members") or {}
    if members and implemented_ac_refs:
        for rel_path, refs in ctx.test_ref_index.file_references().items():
            if not any(ref in implemented_ac_refs for ref in refs):
                continue

            member_name = match_member_name(rel_path, members)
            if member_name is None:
                continue

            test_backlinks[member_name] = test_backlinks.get(member_name, 0) + 1

    return {
        "implemented_doc_count": implemented_doc_count,
        "implemented_ac_count": implemented_ac_count,
        "test_backlinks": dict(sorted(test_backlinks.items())),
    }


def compute_coverage_v2(ctx: "FederationContext") -> dict:
    implemented_docs: dict[str, int] = {}
    tech_spec_paths = ctx.cfg.get("tech_spec_paths") or []

    for rel_path in ctx.spec_files:
        abs_path = ctx.root / rel_path
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        status = parse_status_value(text)
        if status is None or status.casefold() != "implemented":
            continue
        if not any(glob_match(p, rel_path) for p in tech_spec_paths):
            continue
        implemented_docs[rel_path] = count_ac_lines(text)

    tech_spec_files = [
        f for f in ctx.spec_files
        if any(glob_match(p, f) for p in tech_spec_paths)
    ]

    refs = ctx.collect_test_link_refs()
    covered_docs, orphan_refs = build_resolved_link_index(
        refs, ctx.spec_files, tech_spec_files=tech_spec_files
    )

    members = ctx.cfg.get("members") or {}
    test_backlinks: dict[str, int] = {}
    if members:
        seen_files: set[str] = set()
        for doc_refs in covered_docs.values():
            for ref in doc_refs:
                if ref.file not in seen_files:
                    seen_files.add(ref.file)
                    member = match_member_name(ref.file, members)
                    if member:
                        test_backlinks[member] = test_backlinks.get(member, 0) + 1

    gap_findings: list[dict] = []
    for rel_path, ac_count in implemented_docs.items():
        if rel_path not in covered_docs:
            gap_findings.append(make_finding(
                "coverage_gap", "warning", str(ctx.root / rel_path), 0,
                f"implemented spec has {ac_count} AC(s) but no test references it",
                "add '// spec://<path>' comments in test files that exercise this spec",
            ))

    orphan_findings: list[dict] = []
    for ref in orphan_refs:
        detail = f" (test: {ref.test_name})" if ref.test_name else ""
        orphan_findings.append(make_finding(
            "orphan_link", "warning",
            str(ctx.root / ref.file), ref.line,
            f"'// {ref.uri}' does not resolve to any spec file{detail}",
            "update the URI to match a known spec file path or remove the comment",
        ))

    return {
        "implemented_doc_count": len(implemented_docs),
        "implemented_ac_count": sum(implemented_docs.values()),
        "test_backlinks": dict(sorted(test_backlinks.items())),
        "coverage_gap_count": len(gap_findings),
        "orphan_link_count": len(orphan_refs),
        "gap_findings": gap_findings,
        "orphan_findings": orphan_findings,
    }


# -- checks ----------------------------------------------------------------

def make_finding(rule: str, severity: str, file: str, line: int,
                 message: str, fix: str) -> dict:
    return {
        "rule": rule,
        "severity": severity,
        "file": file,
        "line": line,
        "message": message,
        "fix": fix,
    }


KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
AC_TEXT_RE = re.compile(r"^\s*acceptance\s+criteria\s*$", re.IGNORECASE)


def check_anchor_hygiene(file_path: Path, lines: list[str], cfg: dict) -> list[dict]:
    findings: list[dict] = []
    require = set(cfg.get("require_on_headings") or [])
    levels_required = {int(h[1:]) for h in require if h.startswith("h")}

    seen: dict[str, int] = {}
    stack: list[tuple[int, str]] = []
    in_fence = False

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            text = m.group(2)
            anchor = m.group(3)

            if level not in levels_required:
                # Still track on stack so deeper headings can find parents.
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, anchor))
                continue

            if anchor in seen:
                findings.append(make_finding(
                    "anchor_hygiene", "error", str(file_path), idx,
                    f"anchor '{anchor}' is duplicated (also at line {seen[anchor]})",
                    "rename one of them",
                ))
            else:
                seen[anchor] = idx

            if level == 1:
                if anchor != "root":
                    findings.append(make_finding(
                        "anchor_hygiene", "error", str(file_path), idx,
                        f"H1 anchor '{anchor}' must be exactly 'root'",
                        "change to '{#root}'",
                    ))
            elif level == 2:
                if AC_TEXT_RE.match(text):
                    if anchor != "ac":
                        findings.append(make_finding(
                            "anchor_hygiene", "error", str(file_path), idx,
                            f"H2 heading 'Acceptance Criteria' must use anchor '{{#ac}}'",
                            "change anchor to '{#ac}'",
                        ))
                elif not KEBAB_RE.match(anchor):
                    if "." in anchor:
                        msg = (f"H2 anchor '{anchor}' contains a dot; "
                               "H2 must be a single kebab-case segment")
                        fix = (f"use '{{#{anchor.replace('.', '-')}}}' or similar; "
                               "dots are reserved for H3+ hierarchy")
                    else:
                        msg = (f"H2 anchor '{anchor}' is not kebab-case "
                               "(lowercase alphanumerics with single hyphens)")
                        fix = "rename to a single kebab-case segment, e.g. '{#feature-list}'"
                    findings.append(make_finding(
                        "anchor_hygiene", "error", str(file_path), idx, msg, fix,
                    ))
            else:
                while stack and stack[-1][0] >= level:
                    stack.pop()
                if not stack or stack[-1][0] != level - 1:
                    findings.append(make_finding(
                        "anchor_hygiene", "error", str(file_path), idx,
                        f"H{level} has no enclosing H{level - 1} heading",
                        f"add an H{level - 1} above this heading or change this heading's level",
                    ))
                else:
                    parent_anchor = stack[-1][1]
                    prefix = parent_anchor + "."
                    if not anchor.startswith(prefix):
                        findings.append(make_finding(
                            "anchor_hygiene", "error", str(file_path), idx,
                            f"H{level} anchor '{anchor}' does not extend parent "
                            f"H{level - 1} anchor '{parent_anchor}'",
                            f"rename to '{{#{parent_anchor}.<leaf>}}' to nest under parent",
                        ))
                    else:
                        leaf = anchor[len(prefix):]
                        if "." in leaf:
                            findings.append(make_finding(
                                "anchor_hygiene", "error", str(file_path), idx,
                                f"H{level} anchor '{anchor}' has multi-dot leaf; "
                                "only the leaf segment is allowed after parent",
                                f"use '{{#{parent_anchor}.{leaf.replace('.', '-')}}}'",
                            ))
                        elif not leaf or not KEBAB_RE.match(leaf):
                            findings.append(make_finding(
                                "anchor_hygiene", "error", str(file_path), idx,
                                f"H{level} anchor '{anchor}' leaf '{leaf}' is not kebab-case",
                                f"use '{{#{parent_anchor}.<kebab-leaf>}}'",
                            ))

            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, anchor))
            continue

        m2 = HEADING_NO_ANCHOR_RE.match(line)
        if m2 and line.lstrip().startswith("#"):
            level = len(m2.group(1))
            if level in levels_required:
                findings.append(make_finding(
                    "anchor_hygiene", "error", str(file_path), idx,
                    f"H{level} heading missing '{{#anchor}}' suffix",
                    "append '{#kebab-name}' to the heading",
                ))

    return findings


def check_uri_resolution(file_path: Path, lines: list[str], cfg: dict,
                         uri_cfg: dict, spec_files: list[str],
                         cache: AnchorCache) -> list[dict]:
    findings: list[dict] = []
    require_ns = uri_cfg.get("require_namespace")
    severity = cfg.get("severity", "error")

    for idx, raw in enumerate(lines, start=1):
        for m in URI_RE.finditer(raw):
            ns, target_path, anchor = m.group(1), m.group(2), m.group(3)

            if require_ns and ns != require_ns:
                findings.append(make_finding(
                    "uri_resolution", severity, str(file_path), idx,
                    f"namespace '{ns}' does not match required '{require_ns}'",
                    f"change 'spec://{ns}/...' to 'spec://{require_ns}/...'",
                ))
                continue

            resolved, matches = resolve_logical_uri(target_path, spec_files)
            if resolved is None:
                if not matches:
                    findings.append(make_finding(
                        "uri_resolution", severity, str(file_path), idx,
                        f"logical path '{target_path}' did not match any spec file",
                        "use a logical id whose path-suffix matches an existing "
                        "spec file (the '.md' extension is implicit)",
                    ))
                else:
                    listed = ", ".join(matches)
                    findings.append(make_finding(
                        "uri_resolution", severity, str(file_path), idx,
                        f"logical path '{target_path}' is ambiguous: matches {listed}",
                        "make the logical id more specific so exactly one spec "
                        "file matches its path-suffix",
                    ))
                continue

            anchors = cache.get_anchors(resolved)
            if anchors is None:
                findings.append(make_finding(
                    "uri_resolution", severity, str(file_path), idx,
                    f"resolved file '{resolved}' could not be read",
                    "verify the file exists and is readable",
                ))
                continue

            if anchor and anchor not in anchors:
                findings.append(make_finding(
                    "uri_resolution", severity, str(file_path), idx,
                    f"anchor '#{anchor}' not found in '{resolved}'",
                    f"use an existing anchor in '{resolved}' or add "
                    f"'{{#{anchor}}}' to a heading there",
                ))

    return findings


def count_tokens(text: str) -> tuple[int, str]:
    try:
        import tiktoken  # type: ignore
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text)), "tiktoken/cl100k_base"
    except Exception:
        return max(1, len(text) // 4), "heuristic(len/4); install tiktoken for accuracy"


def check_token_budgets(file_path: Path, rel_path: str, text: str,
                        cfg: dict) -> list[dict]:
    findings: list[dict] = []
    budgets: dict[str, int] = cfg.get("budgets") or {}
    if not budgets:
        return findings

    matches: list[tuple[str, int]] = []
    for pattern, budget in budgets.items():
        if glob_match(pattern, rel_path):
            matches.append((pattern, int(budget)))
    if not matches:
        return findings

    # Most specific = longest pattern; ties broken by declaration order.
    pattern_order = list(budgets.keys())
    matches.sort(key=lambda pb: (-len(pb[0]), pattern_order.index(pb[0])))
    pattern, budget = matches[0]

    tokens, counter = count_tokens(text)
    if tokens > budget:
        findings.append(make_finding(
            "token_budgets", cfg.get("severity", "warning"),
            str(file_path), 0,
            f"file is {tokens} tokens (counter: {counter}); "
            f"budget for '{pattern}' is {budget}",
            f"split the file or trim content to fit under {budget} tokens",
        ))
    return findings


def check_bidirectional_coverage(file_path: Path, content: str, cfg: dict,
                                 anchor_cache: AnchorCache,
                                 test_ref_index: TestRefIndex) -> list[dict]:
    findings: list[dict] = []

    rel_path = str(file_path.resolve().relative_to(anchor_cache.root)).replace("\\", "/")
    tech_spec_paths = cfg.get("tech_spec_paths") or []
    if not any(glob_match(p, rel_path) for p in tech_spec_paths):
        return findings

    status = parse_status_value(content)
    if status is None or status.casefold() != "implemented":
        return findings

    referenced = test_ref_index.referenced_anchors()
    namespace = ((cfg.get("uri") or {}).get("require_namespace")
                 or cfg.get("namespace")
                 or "spec")
    severity = cfg.get("severity", "error")

    lines = content.splitlines(keepends=True)
    in_fence = False
    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        m = HEADING_RE.match(line)
        if not m:
            continue

        anchor = m.group(3)
        if not anchor.startswith("ac."):
            continue

        if f"{rel_path}#{anchor}" in referenced:
            continue

        findings.append(make_finding(
            "bidirectional_coverage", severity, str(file_path), idx,
            f"acceptance-criteria anchor '#{anchor}' has no back-reference in test_paths",
            f"add spec://{namespace}/{rel_path}#{anchor} to a test exercising this AC",
        ))

    return findings


def check_md_links(file_path: Path, lines: list[str], cfg: dict) -> list[dict]:
    """Check that [text](path) links resolve to existing files on disk.

    Skips: image links (![...]), URL-scheme links, fragment-only links (#anchor).
    Flags: absolute paths (must be relative) and relative paths that don't exist.
    Inline code spans and fenced code blocks are excluded from scanning.
    """
    findings: list[dict] = []
    severity = cfg.get("severity", "error")
    file_dir = file_path.resolve().parent
    in_fence = False

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        stripped = INLINE_CODE_RE.sub("", line)

        for m in MD_LINK_RE.finditer(stripped):
            raw_target = m.group(2).strip()
            # Strip optional title: [text](path "title") or [text](path 'title')
            path_part = re.split(r'\s+["\']', raw_target)[0].strip()

            if not path_part or path_part.startswith("#"):
                continue
            if _URL_SCHEME_RE.match(path_part):
                continue

            # Strip inline fragment
            path_part = path_part.split("#")[0]
            if not path_part:
                continue

            if _ABS_PATH_RE.match(path_part):
                findings.append(make_finding(
                    "md_link_resolution", severity, str(file_path), idx,
                    f"link target '{path_part}' is an absolute path; use a relative path",
                    "rewrite as a relative path from the current file's directory",
                ))
                continue

            target = (file_dir / path_part).resolve()
            if not target.exists():
                findings.append(make_finding(
                    "md_link_resolution", severity, str(file_path), idx,
                    f"link target '{path_part}' does not exist",
                    "fix the path or remove the link",
                ))

    return findings


# -- pipeline --------------------------------------------------------------

class FederationContext:
    """Per-root state: config, anchor cache, lazily-built spec-file index."""

    def __init__(self, root: Path, cfg: dict):
        self.root = root
        self.cfg = cfg
        self.cache = AnchorCache(root)
        self._spec_files: list[str] | None = None
        self._test_ref_index: TestRefIndex | None = None
        self._coverage_summary: dict | None = None

    @property
    def spec_files(self) -> list[str]:
        if self._spec_files is None:
            self._spec_files = enumerate_spec_files(
                self.root,
                self.cfg.get("spec_paths") or [],
                self.cfg.get("exclude") or [],
            )
        return self._spec_files

    @property
    def test_ref_index(self) -> TestRefIndex:
        if self._test_ref_index is None:
            self._test_ref_index = TestRefIndex(
                self.root,
                self.cfg,
                self.cache,
                self.spec_files,
            )
        return self._test_ref_index

    @property
    def coverage_summary(self) -> dict:
        if self._coverage_summary is None:
            self._coverage_summary = compute_coverage_v2(self)
        return self._coverage_summary

    def collect_test_link_refs(self) -> list[SpecLinkRef]:
        test_paths = self.cfg.get("test_paths") or []
        exclude = self.cfg.get("exclude") or []
        files = enumerate_matching_files(self.root, test_paths, exclude)
        refs: list[SpecLinkRef] = []
        for rel_path in files:
            refs.extend(collect_spec_link_refs(self.root / rel_path, rel_path))
        return refs

    def save(self) -> None:
        self.cache.save()


def lint_file(file_path: Path, ctx: FederationContext) -> list[dict] | None:
    """Lint a single file. Returns None if file is wholly out of scope.

    A file is out of scope when it lives outside the federation root or matches
    any `exclude` glob. Beyond that, each check decides for itself whether the
    file is in its scope (e.g. token_budgets applies to any file with a budget
    entry, regardless of `spec_paths`).
    """
    cfg = ctx.cfg
    spec_paths = cfg.get("spec_paths") or []
    exclude = cfg.get("exclude") or []

    try:
        rel_path = str(file_path.resolve().relative_to(ctx.root)).replace("\\", "/")
    except ValueError:
        return None

    if any(glob_match(p, rel_path) for p in exclude):
        return None

    checks = cfg.get("checks") or {}
    in_spec_paths = any(glob_match(p, rel_path) for p in spec_paths)
    tech_spec_paths = cfg.get("tech_spec_paths") or []
    in_tech_spec_paths = any(glob_match(p, rel_path) for p in tech_spec_paths)

    ah = checks.get("anchor_hygiene") or {}
    ur = checks.get("uri_resolution") or {}
    tb = checks.get("token_budgets") or {}
    ml = checks.get("md_link_resolution") or {}

    tb_budgets: dict[str, int] = (tb.get("budgets") or {}) if tb.get("enabled") else {}
    tb_applies = any(glob_match(p, rel_path) for p in tb_budgets)

    will_run_anchor = bool(ah.get("enabled")) and in_spec_paths
    will_run_uri = bool(ur.get("enabled")) and in_spec_paths
    will_run_bidirectional = in_spec_paths and in_tech_spec_paths
    will_run_md_links = bool(ml.get("enabled")) and in_spec_paths
    findings: list[dict] = []

    if in_tech_spec_paths and not in_spec_paths:
        findings.append(make_finding(
            "config_error", "error", str(file_path), 0,
            "tech_spec_paths matched this file but spec_paths did not",
            "narrow tech_spec_paths or add a covering spec_paths glob",
        ))

    if not (will_run_anchor or will_run_uri or tb_applies or will_run_bidirectional
            or will_run_md_links):
        return findings or None

    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as e:
        # `io_error`: any I/O failure during lint (read, stat, etc.).
        findings.append(make_finding(
            "io_error", "error", str(file_path), 0,
            f"could not read file: {e}", "fix file permissions or path",
        ))
        return findings

    lines = text.splitlines(keepends=True)

    if will_run_anchor:
        findings.extend(check_anchor_hygiene(file_path, lines, ah))

    if will_run_uri:
        uri_cfg = cfg.get("uri") or {}
        findings.extend(check_uri_resolution(
            file_path, lines, ur, uri_cfg, ctx.spec_files, ctx.cache,
        ))

    if tb_applies:
        findings.extend(check_token_budgets(file_path, rel_path, text, tb))

    if will_run_bidirectional:
        findings.extend(check_bidirectional_coverage(
            file_path, text, cfg, ctx.cache, ctx.test_ref_index,
        ))

    if will_run_md_links:
        findings.extend(check_md_links(file_path, lines, ml))

    return findings


# -- rendering -------------------------------------------------------------

def render_text(findings: Iterable[dict]) -> str:
    out: list[str] = []
    for f in findings:
        out.append(
            f"{f['file']}:{f['line']}: {f['severity']} {f['rule']}: {f['message']}"
        )
        out.append(f"  fix: {f['fix']}")
    return "\n".join(out)


def aggregate_coverage(contexts: Iterable[FederationContext]) -> dict:
    implemented_doc_count = 0
    implemented_ac_count = 0
    test_backlinks: dict[str, int] = {}
    coverage_gap_count = 0
    orphan_link_count = 0
    gap_findings: list[dict] = []
    orphan_findings: list[dict] = []

    for ctx in contexts:
        c = ctx.coverage_summary
        implemented_doc_count += c.get("implemented_doc_count", 0)
        implemented_ac_count += c.get("implemented_ac_count", 0)
        for k, v in (c.get("test_backlinks") or {}).items():
            test_backlinks[k] = test_backlinks.get(k, 0) + v
        coverage_gap_count += c.get("coverage_gap_count", 0)
        orphan_link_count += c.get("orphan_link_count", 0)
        gap_findings.extend(c.get("gap_findings") or [])
        orphan_findings.extend(c.get("orphan_findings") or [])

    return {
        "implemented_doc_count": implemented_doc_count,
        "implemented_ac_count": implemented_ac_count,
        "test_backlinks": dict(sorted(test_backlinks.items())),
        "coverage_gap_count": coverage_gap_count,
        "orphan_link_count": orphan_link_count,
        "gap_findings": gap_findings,
        "orphan_findings": orphan_findings,
    }


def render_coverage_text(coverage: dict) -> str:
    out = [
        "Coverage summary",
        "  Implemented specs: "
        f"{coverage.get('implemented_doc_count', 0)} docs, "
        f"{coverage.get('implemented_ac_count', 0)} ACs",
        "  Test backlinks:",
    ]

    test_backlinks = coverage.get("test_backlinks") or {}
    if test_backlinks:
        width = max(len(k) for k in test_backlinks)
        for member, count in test_backlinks.items():
            out.append(f"    {member:<{width}}: {count} tests")
    else:
        out.append("    (none)")

    out.append(f"  Coverage gaps:  {coverage.get('coverage_gap_count', 0)} docs")
    out.append(f"  Orphan links:   {coverage.get('orphan_link_count', 0)}")

    return "\n".join(out)


# -- CLI -------------------------------------------------------------------

def warn_pre_commit(message: str) -> None:
    print(f"spec_lint: warning: {message}", file=sys.stderr)


def run_git(cwd: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            check=True,
            capture_output=True,
            text=True,
        )
    except OSError as e:
        raise RuntimeError(f"could not run git: {e}") from e
    except subprocess.CalledProcessError as e:
        detail = (e.stderr or e.stdout or str(e)).strip()
        raise RuntimeError(detail or "git command failed") from e
    return proc.stdout


def cmd_pre_commit_impact(args: argparse.Namespace) -> int:
    del args

    cwd = Path.cwd()
    try:
        discovered = find_config(cwd)
    except ConfigError as e:
        warn_pre_commit(str(e))
        return 0

    if discovered is None:
        return 0

    federation_root, cfg = discovered

    try:
        git_root = Path(run_git(cwd, "rev-parse", "--show-toplevel").strip()).resolve()
    except RuntimeError as e:
        warn_pre_commit(str(e))
        return 0

    try:
        subrepo_prefix = str(git_root.relative_to(federation_root)).replace("\\", "/")
    except ValueError:
        warn_pre_commit(
            f"git root '{git_root}' is outside federation root '{federation_root}'"
        )
        return 0

    test_paths = cfg.get("test_paths") or []
    exclude = cfg.get("exclude") or []
    if not test_paths:
        return 0

    impacted: set[str] = set()

    for diff_filter, revision_prefix in (("M", ":"), ("D", "HEAD:")):
        try:
            changed = run_git(
                git_root,
                "diff",
                "--cached",
                "--name-only",
                f"--diff-filter={diff_filter}",
            )
        except RuntimeError as e:
            warn_pre_commit(str(e))
            return 0

        for raw_path in changed.splitlines():
            repo_rel_path = raw_path.strip().replace("\\", "/")
            if not repo_rel_path:
                continue

            federation_rel_path = (
                f"{subrepo_prefix}/{repo_rel_path}" if subrepo_prefix else repo_rel_path
            )
            if not in_scope(federation_rel_path, test_paths, exclude):
                continue

            try:
                content = run_git(git_root, "show", f"{revision_prefix}{repo_rel_path}")
            except RuntimeError as e:
                warn_pre_commit(f"could not read {repo_rel_path}: {e}")
                return 0

            impacted.update(extract_spec_uris(content))

    if impacted:
        print("spec_lint: changed tests reference these specs (re-review recommended):")
        for uri in sorted(impacted):
            print(f"  {uri}")

    return 0


def load_explicit_config(config_path: Path) -> tuple[Path, dict]:
    """Load an explicit config; treat its parent as the federation root.

    Raises ConfigError when the file cannot be parsed.
    """
    try:
        with config_path.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except OSError as e:
        raise ConfigError(config_path, f"could not read file: {e}") from e
    except yaml.YAMLError as e:
        raise ConfigError(config_path, f"YAML parse error: {e}") from e
    if not isinstance(cfg, dict):
        raise ConfigError(config_path, "top-level YAML must be a mapping")
    cfg = validate_config_shape(cfg, config_path)
    return config_path.parent.resolve(), cfg


def cmd_lint(args: argparse.Namespace) -> int:
    all_findings: list[dict] = []
    contexts: dict[Path, FederationContext] = {}

    if not args.paths or args.paths == ["-"]:
        import sys as _sys
        paths = [line.strip() for line in _sys.stdin if line.strip()]
    else:
        paths = list(args.paths)

    explicit: tuple[Path, dict] | None = None
    if args.config is not None:
        cfg_path = Path(args.config)
        if not cfg_path.is_file():
            print(f"spec_lint: config not found or unreadable: {cfg_path}",
                  file=sys.stderr)
            return 1
        try:
            explicit = load_explicit_config(cfg_path)
        except ConfigError as e:
            print(f"spec_lint: {e}", file=sys.stderr)
            return 1

    for path_str in paths:
        path = Path(path_str)

        if explicit is not None:
            root, cfg = explicit
        else:
            start = path if path.exists() else Path.cwd()
            try:
                discovered = find_config(start)
            except ConfigError as e:
                print(f"spec_lint: {e}", file=sys.stderr)
                return 1
            if discovered is None:
                print(
                    f"spec_lint: warning: no .spec-config.yaml found for {path}, skipping",
                    file=sys.stderr,
                )
                if args.require_config:
                    all_findings.append(make_finding(
                        "config_missing", "error", str(path), 0,
                        "no .spec-config.yaml found for this path",
                        "add --config or place the file under a federation root",
                    ))
                continue
            root, cfg = discovered

        ctx = contexts.get(root)
        if ctx is None:
            ctx = FederationContext(root, cfg)
            contexts[root] = ctx

        result = lint_file(path, ctx)
        if result is None:
            continue
        all_findings.extend(result)

    coverage = aggregate_coverage(contexts.values())
    all_findings = (
        coverage.get("gap_findings", [])
        + coverage.get("orphan_findings", [])
        + all_findings
    )
    for ctx in contexts.values():
        ctx.save()

    if args.format == "json":
        coverage_json = {
            k: v for k, v in coverage.items()
            if k not in ("gap_findings", "orphan_findings")
        }
        print(json.dumps({
            "findings": all_findings,
            "coverage": coverage_json,
        }, indent=2))
    else:
        text = render_text(all_findings)
        coverage_text = render_coverage_text(coverage)
        if text:
            print(f"{text}\n\n{coverage_text}")
        elif contexts:
            print(coverage_text)

    has_error = any(f["severity"] == "error" for f in all_findings)
    return 1 if has_error else 0


def cmd_post_tool_use(args: argparse.Namespace) -> int:
    # Always exit 0 in this mode: a malformed hook payload or missing config
    # must never break Claude's flow. Findings are surfaced as decision:block.
    try:
        payload_text = sys.stdin.read()
        if not payload_text.strip():
            return 0
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, OSError) as e:
        print(f"spec_lint: invalid hook payload: {e}", file=sys.stderr)
        return 0

    tool_input = payload.get("tool_input") or {}
    file_path_str = tool_input.get("file_path")
    if not file_path_str:
        return 0

    file_path = Path(file_path_str)
    if not file_path.is_absolute():
        cwd = payload.get("cwd")
        if cwd:
            file_path = (Path(cwd) / file_path).resolve()

    try:
        if getattr(args, "config", None):
            cfg_path = Path(args.config)
            if not cfg_path.is_file():
                return 0
            root, cfg = load_explicit_config(cfg_path)
        else:
            discovered = find_config(file_path)
            if discovered is None:
                return 0
            root, cfg = discovered
    except ConfigError as e:
        reason = (
            f"spec_lint: cannot load {e.path}\n"
            f"  {e.message}\n"
            f"  fix: repair the config file (or remove it to disable spec checks "
            f"in this tree)"
        )
        print(json.dumps({"decision": "block", "reason": reason}))
        return 0

    ctx = FederationContext(root, cfg)
    findings = lint_file(file_path, ctx)

    if not findings:
        coverage = ctx.coverage_summary
        ctx.save()
        print(render_coverage_text(coverage), file=sys.stderr)
        return 0

    coverage = ctx.coverage_summary
    ctx.save()
    reason = f"{render_text(findings)}\n\n{render_coverage_text(coverage)}"
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="spec_lint", description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_lint = sub.add_parser("lint", help="Lint files manually.")
    p_lint.add_argument("paths", nargs="*")
    p_lint.add_argument("--format", choices=["text", "json"], default="text")
    p_lint.add_argument("--config", default=None,
                        help="Explicit .spec-config.yaml; skips walk-up discovery.")
    p_lint.add_argument(
        "--require-config",
        action="store_true",
        default=False,
        help="Treat missing .spec-config.yaml as an error instead of a warning.",
    )
    p_lint.set_defaults(func=cmd_lint)

    p_hook = sub.add_parser("post-tool-use",
                            help="Run as a Claude Code PostToolUse hook (stdin = JSON).")
    p_hook.add_argument("--config", default=None,
                        help="Explicit .spec-config.yaml; skips walk-up discovery.")
    p_hook.set_defaults(func=cmd_post_tool_use)

    p_pre_commit = sub.add_parser(
        "pre-commit-impact",
        help="Annotate staged test changes with referenced spec URIs.",
    )
    p_pre_commit.set_defaults(func=cmd_pre_commit_impact)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
