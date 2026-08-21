#!/usr/bin/env python3
"""
Backfill `focus:` frontmatter scalar style on existing handoff briefs.

`serialize_frontmatter` (see `..shared.brief_frontmatter`) canonicalized
every writer into `queue/`/`picked/` to render `focus:` as a `|-` literal
block scalar (worktrail#582), but that fix only changes what *new* writes
produce -- briefs already on disk before it landed can still carry
`focus:` in whatever style PyYAML's default (non-`LiteralStr`) resolver
happened to pick for that particular string: an unquoted plain scalar, a
single-quoted scalar, a double-quoted-and-folded scalar, or a folded `>`
scalar. This backfills those legacy files' `focus:` value to the
canonical `|-` style without touching any other frontmatter field, key
order, or body content.

Two phases, mirroring backfill_created_quoting.py's preview/execute split
so a batch rewrite of already-authored briefs is never silent:

  preview -- scan queue/*.md and picked/*.md. For each file's frontmatter
             block, use `yaml.compose()` to locate the `focus:` value
             node's exact character span and compare its on-disk
             rendering against what `serialize_frontmatter` would produce
             for the same parsed value. Files with no frontmatter block,
             unparseable YAML, no `focus:` key, or a non-string `focus:`
             value are skipped, not proposed.
  execute -- (implemented alongside the preview's span-splice rewrite in
             a later task) given a preview's JSON, re-locate each
             proposal's `focus:` span in the file's *current* content and
             replace only that span.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

from . import work_queue as wq
from ..shared.brief_frontmatter import serialize_frontmatter

_FOCUS_KEY_PREFIX = "focus: "


def _find_focus_span(raw_yaml: str) -> Optional[Tuple[Any, str]]:
    """Return `(parsed_value, span_text)` for the frontmatter block's
    `focus:` key, or None when the block doesn't compose to a mapping with
    a `focus:` key.

    `span_text` is the value node's exact on-disk text (via
    `yaml.compose()`'s node marks), independent of every other key's
    style -- so a still-non-canonical `created:` line elsewhere in the
    same block is never touched by a `focus:`-only proposal.

    Raises `yaml.YAMLError` when `raw_yaml` itself doesn't parse; callers
    distinguish that from "no focus: key" explicitly.
    """
    node = yaml.compose(raw_yaml)
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "focus":
            span_text = raw_yaml[value_node.start_mark.index : value_node.end_mark.index]
            parsed = yaml.safe_load(raw_yaml)
            return parsed["focus"], span_text
    return None


def _canonical_focus_span(value: str) -> str:
    """The value-only portion of `serialize_frontmatter({"focus": value})`,
    in the same span shape `_find_focus_span` extracts from disk (no
    trailing newline, no `focus: ` key prefix)."""
    full = serialize_frontmatter({"focus": value})
    assert full.startswith(_FOCUS_KEY_PREFIX)
    return full[len(_FOCUS_KEY_PREFIX):].rstrip("\n")


def build_preview(queue_base: Path) -> Dict[str, Any]:
    proposals: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for subdir in ("queue", "picked"):
        directory = queue_base / subdir
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                content = path.read_text(encoding="utf-8")
            except OSError as exc:
                skipped.append({"id": path.stem, "reason": f"unreadable: {exc}"})
                continue

            m = wq._FM_RE.match(content)
            if not m:
                skipped.append({"id": path.stem, "reason": "no frontmatter block found"})
                continue
            raw_yaml = m.group(1)

            try:
                found = _find_focus_span(raw_yaml)
            except yaml.YAMLError as exc:
                skipped.append({"id": path.stem, "reason": f"unparseable YAML: {exc}"})
                continue
            if found is None:
                skipped.append({"id": path.stem, "reason": "no focus: key found"})
                continue
            value, span_text = found

            if not isinstance(value, str):
                skipped.append({"id": path.stem, "reason": "focus: value is not a string"})
                continue

            if span_text.rstrip("\n") == _canonical_focus_span(value):
                continue  # already canonical -- nothing to backfill

            proposals.append({
                "id": path.stem,
                "path": str(path),
                "focus_raw": span_text,
                "focus_value": value,
            })
    return {"proposals": proposals, "skipped": skipped}
