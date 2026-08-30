#!/usr/bin/env python3
"""Shared brief frontmatter parsing helpers for project-management skills."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml


class LiteralStr(str):
    """Marker subclass so the YAML dumper renders this value as a literal
    block scalar (``|``) instead of a quoted flow scalar.

    Free-text fields like ``focus`` routinely contain a colon+space or a
    space+``#`` (both of which force PyYAML to quote a plain scalar), an
    apostrophe (which single-quote style then escapes by doubling), or an
    embedded newline (which a default-width double-quoted scalar folds at
    ~80 columns with backslash-continuation lines a line-oriented reader
    truncates). A literal block scalar needs none of that escaping/folding.

    The single canonical marker for every writer into ``queue/``/``picked/``
    — do not define a second copy of this class elsewhere; two independently
    registered representers for the "same" marker is exactly the kind of
    per-writer drift `serialize_frontmatter` exists to prevent.
    """


def _represent_literal_str(dumper: yaml.SafeDumper, data: str) -> yaml.ScalarNode:
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style="|")


yaml.SafeDumper.add_representer(LiteralStr, _represent_literal_str)


def serialize_frontmatter(
    frontmatter: dict[str, Any], *, literal_keys: Sequence[str] = ("focus",)
) -> str:
    """Canonically serialize a brief frontmatter dict to YAML text (no ``---`` fences).

    Every writer into ``queue/``/``picked/`` should route its frontmatter through
    this function instead of hand-rolling its own `yaml.safe_dump` call, so the
    exact YAML *style* (scalar style, quoting, unicode handling) stays identical
    across writers regardless of which tool produced the brief. String-valued
    keys named in `literal_keys` are stripped of leading/trailing whitespace and
    wrapped in `LiteralStr` so they render as a clean ``|-`` block scalar; every
    other value uses PyYAML's normal styling.

    Trailing whitespace on a literal_keys value is stripped, not just cosmetic
    trimming: PyYAML's emitter cannot represent trailing whitespace on a block
    scalar's final line unambiguously and silently falls back to a folded,
    double-quoted scalar for such a value even though `LiteralStr` requested
    ``|`` style -- exactly the non-canonical shape this function exists to rule
    out.
    """
    wrapped = dict(frontmatter)
    for key in literal_keys:
        value = wrapped.get(key)
        if isinstance(value, str):
            wrapped[key] = LiteralStr(value.strip())
    return yaml.safe_dump(
        wrapped, sort_keys=False, default_flow_style=False, allow_unicode=True
    )


def is_canonical_style(
    content: str, *, literal_keys: Sequence[str] = ("focus",)
) -> bool:
    """Return whether `content`'s frontmatter block matches what
    `serialize_frontmatter` would produce for the same parsed values.

    Re-serializes the parsed frontmatter and compares it against the raw YAML
    block actually on disk. `False` means the file was not written through
    `serialize_frontmatter` — wrong scalar style, wrong quoting, line-folding —
    even though it may still parse fine; this is a style check, not a parse
    check (`validate_brief_text` already covers parseability).
    """
    found = _find_frontmatter_block(content)
    if found is None:
        return False
    raw_yaml, _ = found
    try:
        parsed = yaml.safe_load(raw_yaml)
    except yaml.YAMLError:
        return False
    if not isinstance(parsed, dict):
        return False
    canonical = serialize_frontmatter(parsed, literal_keys=literal_keys)
    return raw_yaml.strip("\n") == canonical.strip("\n")


def _find_frontmatter_block(content: str) -> tuple[str, str] | None:
    """Return ``(raw_yaml_text, body)`` for a ``---``-fenced block, or None.

    Tolerates a UTF-8 BOM and a closing ``---`` fence without a trailing
    newline. Shared by both the lenient reader (`split_frontmatter`) and the
    strict validator (`validate_brief_text`) so fence-detection never drifts
    between the two.
    """
    content = content.removeprefix("\ufeff")

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


def split_frontmatter(content: str) -> tuple[dict[str, Any], str]:
    """Return ``(frontmatter, body)`` for a brief-like markdown document.

    Lenient by design (a caller just listing/displaying briefs must not
    crash on a malformed one): a missing/unclosed fence, or YAML that fails
    to parse or isn't a mapping, is treated as empty frontmatter. Use
    `validate_brief_text` when a write path needs the opposite -- to catch a
    broken document instead of papering over it.
    """
    content = content.removeprefix("\ufeff")

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


def read_frontmatter(path: Path) -> dict[str, Any]:
    """Read and parse a brief frontmatter block, returning ``{}`` on I/O error."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    frontmatter, _ = split_frontmatter(content)
    return frontmatter


def validate_brief_text(
    content: str, required: Sequence[str] = ("status",)
) -> tuple[bool, str | None]:
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
) -> tuple[bool, str | None]:
    """Re-read `path` from disk and validate it via `validate_brief_text`."""
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"cannot read {path}: {exc}"
    return validate_brief_text(content, required)
