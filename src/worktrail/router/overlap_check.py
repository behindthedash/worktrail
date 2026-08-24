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

_CAPABILITIES_SECTION_RE = re.compile(
    r"^##[ \t]+Capabilities[ \t]*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

_WHY_SECTION_RE = re.compile(
    r"^##[ \t]+Why[ \t]*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

_PURPOSE_SECTION_RE = re.compile(
    r"^##[ \t]+Purpose[ \t]*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)


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


# --- OpenSpec extraction -------------------------------------------------------

def _is_openspec_root(specs_root: Path) -> bool:
    """True if `specs_root` looks like an OpenSpec project root (has a
    `changes/` and/or `specs/` subdirectory) rather than a devkit
    `docs/specs`-style root of `NNN-slug` folders."""
    return (specs_root / "changes").is_dir() or (specs_root / "specs").is_dir()


def _first_sentence(block: str) -> Optional[str]:
    for line in block.splitlines():
        line = line.strip()
        if line and not line.startswith("[") and not line.startswith("#"):
            sentence_end = re.search(r"[.!?]", line)
            return line[: sentence_end.end()].strip() if sentence_end else line
    return None


def _feature_summary_from_proposal(text: str) -> Optional[str]:
    m = _CAPABILITIES_SECTION_RE.search(text)
    if m:
        block = m.group(1).strip()
        if block:
            return block

    m = _WHY_SECTION_RE.search(text)
    if m:
        return _first_sentence(m.group(1).strip())

    return None


def _extract_openspec_change(change_dir: Path) -> Optional[Dict[str, Any]]:
    proposal = change_dir / "proposal.md"
    if not proposal.is_file():
        return None
    try:
        text = proposal.read_text(errors="ignore")
    except OSError:
        text = ""
    return {
        "spec_id": change_dir.name,
        "stage": "active",
        "title": _slug_to_title(change_dir.name),
        "feature_summary": _feature_summary_from_proposal(text),
        "user_request_excerpt": None,
    }


def _feature_summary_from_openspec_spec(text: str) -> Optional[str]:
    m = _PURPOSE_SECTION_RE.search(text)
    if m:
        block = m.group(1).strip()
        if block:
            return block
    return None


def _extract_openspec_spec(spec_dir: Path) -> Optional[Dict[str, Any]]:
    spec_file = spec_dir / "spec.md"
    if not spec_file.is_file():
        return None
    try:
        text = spec_file.read_text(errors="ignore")
    except OSError:
        text = ""
    return {
        "spec_id": spec_dir.name,
        "stage": "complete",
        "title": _slug_to_title(spec_dir.name),
        "feature_summary": _feature_summary_from_openspec_spec(text),
        "user_request_excerpt": None,
    }


def _scan_openspec(specs_root: Path) -> List[Dict[str, Any]]:
    """OpenSpec extraction path: `changes/*/proposal.md` and `specs/*/spec.md`,
    used in place of the devkit `NNN-slug` folder iteration below."""
    results: List[Dict[str, Any]] = []
    changes_dir = specs_root / "changes"
    if changes_dir.is_dir():
        for d in sorted(changes_dir.iterdir()):
            if not d.is_dir():
                continue
            info = _extract_openspec_change(d)
            if info:
                results.append(info)
    specs_dir = specs_root / "specs"
    if specs_dir.is_dir():
        for d in sorted(specs_dir.iterdir()):
            if not d.is_dir():
                continue
            info = _extract_openspec_spec(d)
            if info:
                results.append(info)
    return results


# --- per-task extraction (OpenSpec `tasks.md` only) ---------------------------

# Task ids across this repo's own active/archived OpenSpec changes are always
# `N.N` (phase.index), never nested `N.N.N`. A checkbox line's own text may be
# followed on later lines by word-wrapped continuation, indented `(a)`/`-`
# sub-bullets, `(Requirement: ...)` citations, and a trailing `files:` scope
# annotation — all of that belongs to the task it follows, not to the next
# `##` phase heading or the next `- [ ]`/`- [x]` line.
_TASK_LINE_RE = re.compile(r"^-\s*\[([ xX])\]\s*(\d+\.\d+)\s+(.*)$")


def _parse_openspec_tasks(tasks_text: str) -> List[Dict[str, Any]]:
    """Parse a `tasks.md` checkbox list into one `{task_id, task_text, checked}`
    entry per top-level task line, folding each task's wrapped/indented
    continuation lines into `task_text`."""
    entries: List[Dict[str, Any]] = []
    lines = tasks_text.splitlines()
    i, n = 0, len(lines)
    while i < n:
        m = _TASK_LINE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        checked = m.group(1) in ("x", "X")
        task_id = m.group(2)
        text_parts = [m.group(3).strip()]
        i += 1
        while i < n:
            line = lines[i]
            if _TASK_LINE_RE.match(line) or line.startswith("#"):
                break
            stripped = line.strip()
            if stripped:
                text_parts.append(stripped)
            i += 1
        entries.append({
            "task_id": task_id,
            "task_text": " ".join(text_parts).strip(),
            "checked": checked,
        })
    return entries


def task_candidates(specs_root: Path, target: Optional[str] = None) -> List[Dict[str, Any]]:
    """Per-task candidate enumeration, scoped to a known OpenSpec target.

    Given an explicit `target` OpenSpec change id whose `changes/<target>/tasks.md`
    is readable, returns one `{task_id, task_text, checked}` entry per UNCHECKED
    task in that change's `tasks.md` — no whole-spec/whole-change scan.

    A devkit-shaped `specs_root` (no per-task granularity exists there), a target
    that doesn't resolve to a readable OpenSpec `tasks.md`, or no target at all
    returns exactly today's whole-spec/whole-change candidates via `scan()`,
    unchanged.
    """
    specs_root = Path(specs_root)
    if target and _is_openspec_root(specs_root):
        tasks_file = specs_root / "changes" / target / "tasks.md"
        if tasks_file.is_file():
            try:
                text = tasks_file.read_text(errors="ignore")
            except OSError:
                text = None
            if text is not None:
                return [e for e in _parse_openspec_tasks(text) if not e["checked"]]
    return scan(specs_root)


# --- scan all specs -----------------------------------------------------------

_DATE_FOLDER_RE = re.compile(r"^\d{3,}-")


def scan(specs_root: Path) -> List[Dict[str, Any]]:
    specs_root = Path(specs_root)
    if not specs_root.is_dir():
        return []
    if _is_openspec_root(specs_root):
        return _scan_openspec(specs_root)
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
