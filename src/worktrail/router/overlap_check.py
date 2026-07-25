#!/usr/bin/env python3
"""
Extract feature summaries from existing specs for overlap detection.

Pure file inspection — no LLM, no network. The LLM running `go` receives
the JSON output and performs the semantic comparison against `feature_idea`.

Extraction priority per spec:
  1. `**Feature Summary**:` line in the spec file (new mandatory field)
  2. First non-empty sentence of `### Problem Statement` section
  3. First line of `user-request.md` (original input captured by brainstorm)

Output: {"specs": [{spec_id, stage, title, feature_summary, user_request_excerpt}]}
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse dashboard's state detection so overlap output includes lifecycle stage.
from .dashboard import find_spec_file, detect_stage


# --- extraction helpers -------------------------------------------------------

_FEATURE_SUMMARY_RE = re.compile(
    r"^\*{0,2}Feature Summary\*{0,2}\s*:\s*(.+)$",
    re.IGNORECASE | re.MULTILINE,
)

_PROBLEM_STMT_RE = re.compile(
    r"###\s+Problem Statement\s*\n+(.*?)(?=\n#{1,6}\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}--(.+)\.md$")


def _slug_to_title(spec_id: str) -> str:
    """'003-donor-search' → 'Donor Search'"""
    parts = spec_id.split("-", 1)
    name = parts[1] if len(parts) == 2 else spec_id
    return name.replace("-", " ").title()


def _spec_title(spec_file: Optional[Path], spec_id: str) -> str:
    if spec_file:
        m = _DATE_PREFIX.match(spec_file.name)
        if m:
            return m.group(1).replace("-", " ").title()
    return _slug_to_title(spec_id)


def _feature_summary_from_spec(text: str) -> Optional[str]:
    m = _FEATURE_SUMMARY_RE.search(text)
    if m:
        val = m.group(1).strip()
        if val and val != "${FEATURE_SUMMARY}":
            return val
    return None


def _summary_from_problem_statement(text: str) -> Optional[str]:
    m = _PROBLEM_STMT_RE.search(text)
    if not m:
        return None
    block = m.group(1).strip()
    for line in block.splitlines():
        line = line.strip()
        if line and not line.startswith("[") and not line.startswith("#"):
            # First real sentence
            sentence_end = re.search(r"[.!?]", line)
            return line[: sentence_end.end()].strip() if sentence_end else line
    return None


def _user_request_excerpt(spec_dir: Path, max_chars: int = 200) -> Optional[str]:
    ur = spec_dir / "user-request.md"
    if not ur.is_file():
        return None
    try:
        text = ur.read_text(errors="ignore").strip()
    except OSError:
        return None
    # Skip the `# User Request` heading and frontmatter-style lines
    lines = [l for l in text.splitlines()
             if l.strip() and not l.startswith("#") and not l.startswith("**")]
    excerpt = " ".join(lines).strip()
    return excerpt[:max_chars] if excerpt else None


# --- per-spec extraction ------------------------------------------------------

def extract_spec_summary(spec_dir: Path) -> Optional[Dict[str, Any]]:
    spec_dir = Path(spec_dir)
    spec_id = spec_dir.name

    # Real spec folders include user-request-only stubs: the unspec'd backlog is
    # the easiest category to duplicate with a fresh brainstorm, so the overlap
    # gate must see it too.
    spec_file = find_spec_file(spec_dir)

    has_tasks = (spec_dir / "tasks").is_dir()
    has_changes = (spec_dir / "changes").is_dir()
    has_request = (spec_dir / "user-request.md").is_file()
    if not spec_file and not has_tasks and not has_changes and not has_request:
        return None  # not a real spec folder

    try:
        spec_text = spec_file.read_text(errors="ignore") if spec_file else ""
    except OSError:
        spec_text = ""

    excerpt = _user_request_excerpt(spec_dir)  # read user-request.md once

    feature_summary = (
        _feature_summary_from_spec(spec_text)
        or _summary_from_problem_statement(spec_text)
        or (excerpt[:150] if excerpt else None)
    )

    try:
        # probe_stale=False: stage here is display-only; skip the per-spec
        # `git ls-files` probe the dashboard needs for dispatch decisions.
        stage = detect_stage(spec_dir, probe_stale=False).get("stage", "unknown")
    except Exception:
        stage = "unknown"

    return {
        "spec_id": spec_id,
        "stage": stage,
        "title": _spec_title(spec_file, spec_id),
        "feature_summary": feature_summary,
        "user_request_excerpt": excerpt,
    }


# --- scan all specs -----------------------------------------------------------

_DATE_FOLDER_RE = re.compile(r"^\d{3,}-")


def scan(specs_root: Path) -> List[Dict[str, Any]]:
    specs_root = Path(specs_root)
    if not specs_root.is_dir():
        return []
    results = []
    for d in sorted(specs_root.iterdir()):
        if not d.is_dir():
            continue
        # Skip shared files (architecture.md, ontology.md live as files, not dirs)
        if not _DATE_FOLDER_RE.match(d.name):
            continue
        info = extract_spec_summary(d)
        if info:
            results.append(info)
    return results


# --- CLI ----------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Extract spec feature summaries for overlap detection")
    p.add_argument("--root", default="docs/specs",
                   help="specs root to scan (default: docs/specs)")
    p.add_argument("--json", action="store_true", default=True,
                   help="emit JSON (default: true)")
    args = p.parse_args(argv)

    specs = scan(Path(args.root))
    print(json.dumps({"specs": specs}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
