"""Shared slug normalizer for work queue records."""

from __future__ import annotations

import re

_SLUG_MAX_CHARS = 60
_SLUG_MAX_WORDS = 5


def fallback_slugify(text: str, *, default: str) -> str:
    """Deterministic word-truncation slug used when no semantic summary is available."""
    cleaned = re.sub(r"(?<=[a-z0-9])'s(?=\s|$)", "", text.lower())
    words = [w for w in re.findall(r"[a-z0-9]+", cleaned) if len(w) > 1][: _SLUG_MAX_WORDS]
    slug = "-".join(words) or default
    return slug[:_SLUG_MAX_CHARS].rstrip("-") or default
