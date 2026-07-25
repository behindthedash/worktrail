#!/usr/bin/env python3
"""Shared brief frontmatter parsing helpers for project-management skills."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import yaml


def _find_frontmatter_block(content: str) -> Optional[Tuple[str, str]]:
    """Return ``(raw_yaml_text, body)`` for a ``---``-fenced block, or None.

    Tolerates a UTF-8 BOM and a closing ``---`` fence without a trailing
    newline. Shared by both the lenient reader (`split_frontmatter`) and the
    strict validator (`validate_brief_text`) so fence-detection never drifts
    between the two.
    """
    if content.startswith("\ufeff"):
        content = content[1:]

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return None

    offset = len(lines[0])
    end = None
    for line in lines[1:]:
        if line.rstrip("\r\n") == "---":
            end = offset
            offset += len(line)
            break
        offset += len(line)

    if end is None:
        return None

    return content[len(lines[0]) : end], content[offset:]


def split_frontmatter(content: str) -> Tuple[Dict[str, Any], str]:
    """Return ``(frontmatter, body)`` for a brief-like markdown document.

    Lenient by design (a caller just listing/displaying briefs must not
    crash on a malformed one): a missing/unclosed fence, or YAML that fails
    to parse or isn't a mapping, is treated as empty frontmatter. Use
    `validate_brief_text` when a write path needs the opposite -- to catch a
    broken document instead of papering over it.
    """
    if content.startswith("\ufeff"):
        content = content[1:]

    found = _find_frontmatter_block(content)
    if found is None:
        return {}, content
    raw_yaml, body = found

    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        parsed = {}

    frontmatter = parsed if isinstance(parsed, dict) else {}
    return frontmatter, body


def read_frontmatter(path: Path) -> Dict[str, Any]:
    """Read and parse a brief frontmatter block, returning ``{}`` on I/O error."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    frontmatter, _ = split_frontmatter(content)
    return frontmatter


def validate_brief_text(
    content: str, required: Sequence[str] = ("status",)
) -> Tuple[bool, Optional[str]]:
    """Strict counterpart to `split_frontmatter`: confirm `content` is a
    well-formed brief instead of silently degrading a malformed one to `{}`.

    A write path needs to catch a just-produced broken document (unclosed
    fence, YAML that fails to parse, a required field silently emptied)
    before reporting success -- exactly the failure mode a lenient reader is
    designed to paper over for callers that only display briefs. Well-formed
    YAML is checked unconditionally; `required` only names which scalar
    fields must additionally be non-empty. `status` is the one field every
    brief in this system actually carries (claim/done/release all stamp it,
    and dependency resolution reads it). `id` and `focus` are deliberately
    NOT in the default: `id` is optional by design (`work_queue.py`'s own
    `resolve()` matches on filename/stem first, frontmatter `id` only as a
    fallback), and `focus` is carried by some real historical briefs only in
    the `## Focus`/`# Focus` body section, never frontmatter. Callers that
    mint a brand-new brief (where those fields are always synthesized, not
    inherited) should pass a wider `required` tuple explicitly, e.g.
    `("id", "status", "focus")`.

    Returns `(True, None)` when `content` has a `---`-fenced block parsing
    to a dict where every field in `required` is a non-empty string;
    otherwise `(False, "<reason>")`.
    """
    found = _find_frontmatter_block(content)
    if found is None:
        return False, "no '---'-fenced frontmatter block found"
    raw_yaml, _ = found

    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError as exc:
        return False, f"invalid YAML frontmatter: {exc}"

    if not isinstance(parsed, dict):
        return False, "frontmatter did not parse to a mapping"

    missing = [
        key
        for key in required
        if not isinstance(parsed.get(key), str) or not parsed[key].strip()
    ]
    if missing:
        return False, f"missing/empty required field(s): {', '.join(missing)}"

    return True, None


def validate_brief(
    path: Path, required: Sequence[str] = ("status",)
) -> Tuple[bool, Optional[str]]:
    """Re-read `path` from disk and validate it via `validate_brief_text`."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"
    return validate_brief_text(content, required)
