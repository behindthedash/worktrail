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
from ..shared.brief_frontmatter import serialize_frontmatter, validate_brief

_FOCUS_KEY_PREFIX = "focus: "


def _find_focus_span(raw_yaml: str) -> Optional[Tuple[Any, int, int]]:
    """Return `(parsed_value, start, end)` for the frontmatter block's
    `focus:` key, where `raw_yaml[start:end]` is the value node's exact
    on-disk text (via `yaml.compose()`'s node marks) -- or None when the
    block doesn't compose to a mapping with a `focus:` key.

    Independent of every other key's style -- so a still-non-canonical
    `created:` line elsewhere in the same block is never touched by a
    `focus:`-only proposal.

    Raises `yaml.YAMLError` when `raw_yaml` itself doesn't parse; callers
    distinguish that from "no focus: key" explicitly.
    """
    node = yaml.compose(raw_yaml)
    if not isinstance(node, yaml.MappingNode):
        return None
    for key_node, value_node in node.value:
        if isinstance(key_node, yaml.ScalarNode) and key_node.value == "focus":
            parsed = yaml.safe_load(raw_yaml)
            return parsed["focus"], value_node.start_mark.index, value_node.end_mark.index
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
            value, start, end = found
            span_text = raw_yaml[start:end]

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


def _locate_current_focus(content: str) -> Optional[Tuple[Any, Any, str, int, int]]:
    """Re-locate the `focus:` value node's span in `content`'s *current*
    on-disk state (never a cached preview span). Returns
    `(fm_match, parsed_value, raw_yaml, start, end)`, or None when
    `content` no longer has a frontmatter block, a `focus:` key, or when
    the frontmatter no longer parses -- any of which means the file
    changed shape since preview and must be skipped, not clobbered."""
    m = wq._FM_RE.match(content)
    if not m:
        return None
    raw_yaml = m.group(1)
    try:
        found = _find_focus_span(raw_yaml)
    except yaml.YAMLError:
        return None
    if found is None:
        return None
    value, start, end = found
    return m, value, raw_yaml, start, end


def _splice_focus(content: str, m: Any, raw_yaml: str, start: int, end: int, value: str) -> str:
    """Replace only the `focus:` value node's `[start:end)` span within
    `raw_yaml` with `serialize_frontmatter`'s canonical rendering of
    `value`, leaving every other frontmatter line, the closing fence, and
    the body untouched.

    A block scalar's (`|`/`>`) node span, per `yaml.compose()`'s marks,
    extends through its trailing newline(s) -- the boundary that
    separates it from the next key -- while a flow scalar's span (plain,
    single-quoted, double-quoted) does not: that same newline is left
    outside `[start:end)`, in `raw_yaml[end:]`. The replacement must
    preserve whichever of those the original span carried, or the
    rewrite either swallows the separator before `created:`/EOF (folded
    `>` -> canonical `|-`) or leaves the newline doubled.
    """
    original_span = raw_yaml[start:end]
    trailing_newlines = original_span[len(original_span.rstrip("\n")):]
    new_raw_yaml = raw_yaml[:start] + _canonical_focus_span(value) + trailing_newlines + raw_yaml[end:]
    return content[: m.start(1)] + new_raw_yaml + content[m.end(1) :]


def execute_apply(preview: Dict[str, Any], queue_base: Path, confirm: bool) -> Dict[str, Any]:
    """Mirror `backfill_created_quoting.execute_apply`'s contract: `confirm=False`
    performs zero writes (every proposal reported skipped/"declined"); `confirm=True`
    re-locates each proposal's `focus:` span in the file's *current* content, skips
    (without writing) any file that's gone or whose span no longer matches what
    preview observed, splices in the canonical rendering, then rolls the write back
    to the pre-splice content if either `validate_brief` or an exact re-parsed-value
    check fails."""
    stamped: List[str] = []
    skipped: List[Dict[str, Any]] = []

    if not confirm:
        return {
            "stamped": stamped,
            "skipped": [{"id": p["id"], "reason": "declined"} for p in preview["proposals"]],
        }

    for item in preview["proposals"]:
        path = Path(item["path"])
        if not path.is_file():
            skipped.append({"id": item["id"], "reason": "file no longer present (claimed/moved/removed since preview)"})
            continue

        content = path.read_text(encoding="utf-8")
        located = _locate_current_focus(content)
        if located is None:
            skipped.append({"id": item["id"], "reason": "frontmatter block or focus: key missing since preview"})
            continue
        m, value, raw_yaml, start, end = located
        if raw_yaml[start:end] != item["focus_raw"]:
            skipped.append({"id": item["id"], "reason": "focus: span changed since preview"})
            continue

        new_content = _splice_focus(content, m, raw_yaml, start, end, value)
        path.write_text(new_content, encoding="utf-8")

        ok, verr = validate_brief(path)
        reparsed_matches = False
        if ok:
            new_m = wq._FM_RE.match(new_content)
            try:
                reparsed_matches = new_m is not None and yaml.safe_load(new_m.group(1))["focus"] == value
            except yaml.YAMLError:
                reparsed_matches = False

        if not ok or not reparsed_matches:
            path.write_text(content, encoding="utf-8")  # roll back
            reason = (
                f"post-write validation failed, rolled back: {verr}"
                if not ok
                else "post-write re-parsed focus: value mismatch, rolled back"
            )
            skipped.append({"id": item["id"], "reason": reason})
            continue

        stamped.append(item["id"])

    return {"stamped": stamped, "skipped": skipped}
