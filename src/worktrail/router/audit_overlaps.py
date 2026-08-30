#!/usr/bin/env python3
"""
Scan all specs and produce a ranked list of potentially overlapping pairs.

Pure file inspection + token-based scoring — no LLM, no network. The LLM
running `specs.consolidate` receives this JSON and applies semantic judgment
to decide which pairs are true duplicates worth merging.

Algorithm:
  1. Extract feature summaries via overlap_check.scan() -- from `--root` and,
     when given, every `--extra-root` (e.g. an OpenSpec `openspec` tree
     alongside a devkit `docs/specs`), merged into one corpus
  2. Tokenise each summary (lowercase words, stop-word removal)
  3. Compute pairwise Jaccard similarity on the token sets
  4. Return pairs above MIN_SCORE, ranked highest first
  5. Flag specs with no extractable summary as `unscored`

Output:
  {
    "pairs": [
      {
        "spec_a": {spec_id, stage, title, feature_summary},
        "spec_b": {spec_id, stage, title, feature_summary},
        "token_score": 0.45,   // Jaccard on word tokens (0–1)
        "score_label": "high"  // high ≥0.4, medium ≥0.25, low ≥MIN_SCORE
      }, ...
    ],
    "unscored": [
      {"spec_id": "...", "reason": "no_feature_summary"}
    ],
    "total_specs": N
  }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .overlap_check import scan as _scan_specs

# Minimum Jaccard score to include a pair in output (avoids noise from
# pairs that share only stopwords after filtering).
MIN_SCORE = 0.15

_STOPWORDS: set[str] = {
    "a",
    "an",
    "the",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "as",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "shall",
    "that",
    "this",
    "it",
    "its",
    "their",
    "they",
    "we",
    "our",
    "can",
    "not",
    "no",
    "so",
    "up",
    "out",
    "into",
    "than",
    "then",
    "when",
    "where",
    "which",
    "who",
    "what",
    "how",
    "all",
    "each",
    "both",
    "more",
    "also",
    "user",
    "users",
    "system",
    "feature",
    "allows",
    "enables",
    "provides",
    "support",
    "supports",
    "new",
    "via",
    "per",
    "any",
}


def _tokenise(text: str) -> set[str]:
    """Lowercase word tokens with stop-word removal."""
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in _STOPWORDS and len(w) > 2}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _score_label(score: float) -> str:
    if score >= 0.4:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def _summary_text(spec: dict[str, Any]) -> str | None:
    """Combine feature_summary + user_request_excerpt for a richer token set."""
    parts = []
    if spec.get("feature_summary"):
        parts.append(spec["feature_summary"])
    if spec.get("user_request_excerpt"):
        parts.append(spec["user_request_excerpt"])
    return " ".join(parts) if parts else None


def _strip_for_output(spec: dict[str, Any]) -> dict[str, Any]:
    """Return only the fields useful for the LLM comparison."""
    return {
        "spec_id": spec["spec_id"],
        "stage": spec["stage"],
        "title": spec["title"],
        "feature_summary": spec.get("feature_summary"),
    }


def audit(
    specs_root: Path,
    min_score: float = MIN_SCORE,
    extra_roots: Iterable[Path] | None = None,
) -> dict[str, Any]:
    """Audit the spec corpus under `specs_root` (and any `extra_roots`, e.g.
    an OpenSpec `openspec` tree alongside a devkit `docs/specs`) for
    overlapping pairs. Candidates from every root are merged into one corpus
    before pairwise scoring, so a spec under one root can be flagged as
    overlapping a spec under another. `extra_roots` defaults to none,
    reproducing prior single-root behavior exactly."""
    specs: list[dict[str, Any]] = list(_scan_specs(specs_root))
    seen_ids = {s["spec_id"] for s in specs}
    for extra_root in extra_roots or []:
        for spec in _scan_specs(Path(extra_root)):
            if spec["spec_id"] not in seen_ids:
                seen_ids.add(spec["spec_id"])
                specs.append(spec)

    scored: list[tuple[float, dict[str, Any], dict[str, Any]]] = []
    unscored: list[dict[str, str]] = []

    # Pre-compute token sets; mark specs with no text as unscored
    token_sets: dict[str, set[str] | None] = {}
    for spec in specs:
        text = _summary_text(spec)
        if text:
            token_sets[spec["spec_id"]] = _tokenise(text)
        else:
            token_sets[spec["spec_id"]] = None
            unscored.append(
                {"spec_id": spec["spec_id"], "reason": "no_feature_summary"}
            )

    # Pairwise Jaccard
    spec_map = {s["spec_id"]: s for s in specs}
    ids = list(token_sets.keys())
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a_id, b_id = ids[i], ids[j]
            a_tokens, b_tokens = token_sets[a_id], token_sets[b_id]
            if a_tokens is None or b_tokens is None:
                continue
            score = _jaccard(a_tokens, b_tokens)
            if score >= min_score:
                scored.append((score, spec_map[a_id], spec_map[b_id]))

    scored.sort(key=lambda x: x[0], reverse=True)

    pairs = [
        {
            "spec_a": _strip_for_output(a),
            "spec_b": _strip_for_output(b),
            "token_score": round(score, 3),
            "score_label": _score_label(score),
        }
        for score, a, b in scored
    ]

    return {
        "pairs": pairs,
        "unscored": unscored,
        "total_specs": len(specs),
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Audit spec corpus for overlapping pairs")
    p.add_argument(
        "--root", default="docs/specs", help="specs root to scan (default: docs/specs)"
    )
    p.add_argument(
        "--extra-root",
        action="append",
        default=None,
        metavar="ROOT",
        help="additional root to scan alongside --root and merge into the same "
        "corpus, e.g. 'openspec'; repeatable",
    )
    p.add_argument(
        "--min-score",
        type=float,
        default=MIN_SCORE,
        help=f"minimum Jaccard score to include a pair (default: {MIN_SCORE})",
    )
    args = p.parse_args(argv)

    extra_roots = [Path(r) for r in args.extra_root] if args.extra_root else None
    result = audit(Path(args.root), min_score=args.min_score, extra_roots=extra_roots)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
