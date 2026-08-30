#!/usr/bin/env python3
"""Classify a claimed handoff brief against the current specs tree.

This helper is deterministic and stdlib-only apart from PyYAML, matching the
queue tooling style. It does not choose the final GO route; it emits evidence
that the sdd-workflow conductor feeds into its normal classifier.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import (
    read_frontmatter,
    split_frontmatter,
    validate_brief_path,
)

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,}", re.IGNORECASE)
_PATH_RE = re.compile(r"(?:[\w.-]+/)+[\w.-]+")
_CHANGE_KIND_TO_ROUTE = {"new": "C", "delta": "G", "bugfix": "F"}


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _sections_text(path: Path) -> str:
    content = _read_text(path)
    _, body = split_frontmatter(content)
    return body


def _tokens(text: str) -> set[str]:
    stop = {
        "the",
        "and",
        "for",
        "with",
        "from",
        "this",
        "that",
        "brief",
        "work",
        "spec",
        "docs",
        "task",
        "route",
        "skill",
    }
    return {t.lower() for t in _WORD_RE.findall(text) if t.lower() not in stop}


def _paths(text: str) -> set[str]:
    return {p.lower() for p in _PATH_RE.findall(text)}


def _title(spec_dir: Path) -> str:
    for candidate in [spec_dir / "spec.md", *sorted(spec_dir.glob("*.md"))]:
        if not candidate.is_file():
            continue
        for line in _read_text(candidate).splitlines():
            if line.startswith("# "):
                return line[2:].strip()
    return spec_dir.name


def _spec_blob(spec_dir: Path) -> str:
    parts = [spec_dir.name, _title(spec_dir)]
    for pattern in ("spec.md", "*.md", "contracts/*.md", "data-model.md"):
        for path in sorted(spec_dir.glob(pattern)):
            if path.is_file():
                parts.append(_read_text(path))
    return "\n".join(parts)


def _brief_blob(brief: Path) -> str:
    fm = read_frontmatter(brief)
    fields = [
        str(fm.get("focus") or ""),
        str(fm.get("change-kind") or ""),
        str(fm.get("target-spec") or ""),
    ]
    return "\n".join(fields + [_sections_text(brief)])


def _candidate_specs(
    brief: Path, specs_root: Path, target_spec: str | None
) -> list[dict[str, Any]]:
    if not specs_root.is_dir():
        return []

    brief_text = _brief_blob(brief)
    brief_tokens = _tokens(brief_text)
    brief_paths = _paths(brief_text)
    candidates: list[dict[str, Any]] = []

    for spec_dir in sorted(p for p in specs_root.iterdir() if p.is_dir()):
        spec_text = _spec_blob(spec_dir)
        token_overlap = sorted(brief_tokens & _tokens(spec_text))
        path_overlap = sorted(brief_paths & _paths(spec_text))
        score = len(token_overlap) + (3 * len(path_overlap))
        signals: list[str] = []

        if target_spec and (
            spec_dir.name == target_spec or spec_dir.name.endswith(target_spec)
        ):
            score += 10
            signals.append("target-spec")
        if token_overlap:
            signals.append("token-overlap")
        if path_overlap:
            signals.append("path-overlap")

        if score:
            candidates.append(
                {
                    "spec_id": spec_dir.name,
                    "title": _title(spec_dir),
                    "score": score,
                    "signals": signals,
                    "token_overlap": token_overlap[:8],
                    "path_overlap": path_overlap[:5],
                }
            )

    return sorted(candidates, key=lambda c: (-int(c["score"]), str(c["spec_id"])))[:5]


def classify_handoff(brief: Path, specs_root: Path) -> dict[str, Any]:
    # A malformed brief path (a picked-brief directory, or a path missing its
    # `.md` suffix) used to fall through silently: `read_frontmatter` and
    # `_sections_text` both swallow the resulting OSError into `{}`/"", so
    # this returned a normal-looking, all-empty result with no signal that
    # the input itself was wrong. Checked first so the caller gets an
    # explicit, actionable error instead.
    valid, shape_error = validate_brief_path(brief)
    if not valid:
        return {
            "hint": None,
            "change_kind": None,
            "target_spec": None,
            "recommended_route": None,
            "candidate_specs": [],
            "signals": [],
            "error": shape_error,
        }

    fm = read_frontmatter(brief)
    raw_kind = str(fm.get("change-kind") or "").strip().lower()
    change_kind = raw_kind if raw_kind in _CHANGE_KIND_TO_ROUTE else None
    target_spec = str(fm.get("target-spec") or "").strip() or None
    recommended_route = str(fm.get("recommended-route") or "").strip().upper() or None
    if recommended_route not in set("ABCDEFGHIJ"):
        recommended_route = None

    candidates = _candidate_specs(brief, specs_root, target_spec)
    signals: list[str] = []
    if change_kind:
        signals.append(f"change-kind:{change_kind}")
    if target_spec:
        signals.append("target-spec")
    if candidates:
        signals.append("matching-spec")

    hint = recommended_route
    if change_kind:
        hint = _CHANGE_KIND_TO_ROUTE[change_kind]
    elif candidates and not recommended_route:
        text = _brief_blob(brief).lower()
        if re.search(r"\b(bug|defect|fix|wrong|broken|regression)\b", text):
            hint = "F"
        elif re.search(
            r"\b(change|modify|replace|upgrade|migrate|switch|delta)\b", text
        ):
            hint = "G"

    return {
        "hint": hint,
        "change_kind": change_kind,
        "target_spec": target_spec,
        "recommended_route": recommended_route,
        "candidate_specs": candidates,
        "signals": signals,
        "error": None,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="classify a handoff brief against docs/specs"
    )
    parser.add_argument("brief", help="claimed handoff brief path")
    parser.add_argument("--specs-root", required=True, help="path to docs/specs")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    result = classify_handoff(Path(args.brief), Path(args.specs_root))
    if args.json:
        print(json.dumps(result))
    elif result["error"]:
        print(f"error: {result['error']}", file=sys.stderr)
    else:
        print(f"hint: {result['hint']}")
        for candidate in result["candidate_specs"]:
            print(
                f"{candidate['spec_id']}: score={candidate['score']} {candidate['title']}"
            )
    return 1 if result["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
