#!/usr/bin/env python3
"""
`/go`'s pre-dispatch spec-collision guard (Spec-Collision Guard, per
`docs/specs/ontology.md`).

Before dispatching a fresh brief/spec, `/go` should be able to ask: "does an
existing spec in `docs/specs/` already claim to cover this?" This module
answers that in two separate, best-effort-only steps, mirroring
`check_repo_freshness.py`'s shape:

  1. `check(repo, root)` -- pure extraction. Delegates to `overlap_check.scan()`
     for the candidate index (spec_id + title + feature_summary per spec) and
     performs NO semantic judgment of its own: deciding whether a candidate is
     a strong match (same actor + capability + primary domain, or a clear
     sub-set/extension) is the calling agent's job, applying the same
     comparison rule `overlap_check.py` already documents for the brainstorm
     overlap gate.

  2. `verify(repo, spec_id, root)` -- artifact verification for a single
     candidate the calling agent has already judged a semantic match
     (Collision Candidate -> Confirmed Collision). Checks the candidate's
     `**Status**:` header and whether its task `files:` are git-tracked at
     the repo's base branch, reusing `dashboard.py`'s own stale-bookkeeping
     helpers (`_git_tracked`, `_task_files_are_shipped`, `_load_tasks`)
     rather than reimplementing them.

Both functions are best-effort and never raise to their caller: any internal
failure (unreadable spec file, sibling-module import failure, malformed
frontmatter, git failure) degrades to `checked: false` / `confirmed: false`
plus a non-null `warning`, never an exception, never a block.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# overlap_check is a sibling module -- reused for the candidate index rather
# than re-implementing its extraction logic.
from .overlap_check import scan as _scan

# dashboard.py is a sibling module -- its `_git_tracked`/
# `_task_files_are_shipped` stale-bookkeeping helpers and `_load_tasks`
# task-frontmatter loader are reused verbatim for REQ-002's artifact
# verification (no fresh `git ls-files` call, no reimplemented frontmatter
# parsing). `find_spec_file` is reused to locate the candidate's spec doc
# for its `**Status**:` header.
from .dashboard import (
    find_spec_file as _find_spec_file,
    _git_tracked,
    _task_files_are_shipped,
    _load_tasks,
)


# --- header / prose probes ----------------------------------------------------

# Mirrors dashboard.py's own `_STATUS_RE`: accepts bare `Status:` and both
# `**Status**:` / `**Status:**` bolding styles.
_STATUS_RE = re.compile(
    r"^\**\s*status\s*\**\s*:\s*\**\s*([A-Za-z][\w -]*)", re.IGNORECASE | re.MULTILINE
)

# Non-file artifact claims (e.g. "**Artifacts**: migrated the donor table")
# surfaced as a note, never folded into `confirmed` (AC-007) -- confirmation
# is decided purely by the `files:` git-tracking check below.
_ARTIFACT_CLAIM_RE = re.compile(
    r"^\*{0,2}Artifacts?\*{0,2}\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE
)

# Backtick-quoted, path-shaped tokens in a traceability-matrix.md table --
# the pre-task-split fallback for candidates with no tasks/ dir (REQ-002).
_TRACE_FILE_RE = re.compile(r"`([\w./-]+\.\w{1,10})`")

_DATE_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}--")
_AUX_SUFFIX = ("--tasks", "--review", "-review", "technical-plan")


def _fallback_find_spec_file(spec_dir: Path) -> Optional[Path]:
    cands = [
        f for f in spec_dir.glob("*.md")
        if not any(f.stem.lower().endswith(s) for s in _AUX_SUFFIX)
    ]
    if not cands:
        return None
    dated = sorted(f for f in cands if _DATE_PREFIX.match(f.name))
    return dated[-1] if dated else sorted(cands)[0]


def _status_header(spec_text: str) -> Optional[str]:
    m = _STATUS_RE.search(spec_text)
    return m.group(1).strip() if m else None


def _non_file_artifact_note(spec_text: str) -> Optional[str]:
    m = _ARTIFACT_CLAIM_RE.search(spec_text)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def _files_from_traceability_matrix(spec_dir: Path) -> List[str]:
    """Fallback file-path extraction for a pre-task-split spec (no tasks/
    dir): backtick-quoted, path-shaped tokens from traceability-matrix.md."""
    tm = spec_dir / "traceability-matrix.md"
    if not tm.is_file():
        return []
    try:
        text = tm.read_text(errors="ignore")
    except OSError:
        return []
    return sorted({
        m.group(1) for m in _TRACE_FILE_RE.finditer(text) if "/" in m.group(1)
    })


def _collect_task_files(spec_dir: Path) -> List[str]:
    if _load_tasks is not None:
        try:
            tasks = _load_tasks(spec_dir)
        except Exception:
            tasks = None
        if tasks:
            files = sorted({f for t in tasks for f in (t.get("files") or [])})
            if files:
                return files
    return _files_from_traceability_matrix(spec_dir)


# --- check(): pure extraction --------------------------------------------------

def check(repo: Path, root: str = "docs/specs") -> Dict[str, object]:
    """Enumerate `docs/specs/` candidates for the calling agent to judge.

    Returns `{"checked": bool, "candidates": [{"spec_id", "stage", "title",
    "feature_summary"}], "warning": str|None}`. `checked=False` means the
    index could not be built (no `docs/specs/` dir, or an internal failure)
    -- callers must treat that as "no signal", never as "no collision".
    Performs no semantic matching of its own; that judgment is the calling
    agent's, applying `overlap_check.py`'s existing comparison rule.
    """
    repo = Path(repo)
    result: Dict[str, object] = {"checked": False, "candidates": [], "warning": None}

    if _scan is None:
        result["warning"] = "overlap_check import failed; cannot enumerate spec candidates"
        return result

    specs_root = repo / root
    if not specs_root.is_dir():
        return result

    try:
        specs = _scan(specs_root)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"failed to scan {specs_root}: {exc!r}"
        return result

    candidates: List[Dict[str, Any]] = []
    for s in specs:
        try:
            candidates.append({
                "spec_id": s["spec_id"],
                "stage": s.get("stage"),
                "title": s.get("title"),
                "feature_summary": s.get("feature_summary"),
            })
        except Exception:  # noqa: BLE001 - malformed candidate entry, skip it
            continue

    result["checked"] = True
    result["candidates"] = candidates
    return result


# --- verify(): artifact verification for a single judged candidate ------------

def verify(repo: Path, spec_id: str, root: str = "docs/specs") -> Dict[str, object]:
    """Confirm (or refute) a candidate the calling agent has already judged
    a semantic match.

    Returns `{"spec_id", "confirmed": bool, "status": str|None,
    "files": [str], "note": str|None, "warning": str|None}`.
    `confirmed=True` only when the candidate's `**Status**:` header reads
    `Implemented` AND every task `files:` entry is git-tracked at the repo's
    base checkout. Any non-file artifact claim found in the spec's prose is
    surfaced via `note` and never affects `confirmed`. Never raises --
    every failure path (missing spec dir, unreadable spec file, no files to
    verify, git failure) degrades to `confirmed: false` plus a `warning`.
    """
    repo = Path(repo)
    result: Dict[str, object] = {
        "spec_id": spec_id,
        "confirmed": False,
        "status": None,
        "files": [],
        "note": None,
        "warning": None,
    }

    spec_dir = repo / root / spec_id
    if not spec_dir.is_dir():
        result["warning"] = f"spec directory not found: {spec_dir}"
        return result

    find_spec_file = _find_spec_file or _fallback_find_spec_file
    try:
        spec_file = find_spec_file(spec_dir)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"failed to locate spec doc: {exc!r}"
        return result
    if spec_file is None:
        result["warning"] = f"no spec document found under {spec_dir}"
        return result

    try:
        text = spec_file.read_text(errors="ignore")
    except OSError as exc:
        result["warning"] = f"could not read {spec_file}: {exc!r}"
        return result

    status = _status_header(text)
    result["status"] = status
    result["note"] = _non_file_artifact_note(text)

    if not status or status.strip().lower() != "implemented":
        result["warning"] = f"spec Status is {status!r}, not Implemented"
        return result

    if _git_tracked is None or _task_files_are_shipped is None:
        result["warning"] = "dashboard git-tracking helpers unavailable; cannot verify artifacts"
        return result

    try:
        files = _collect_task_files(spec_dir)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"failed to collect task files: {exc!r}"
        return result
    if not files:
        result["warning"] = "no task files found to verify (no tasks/ dir and no traceability-matrix.md paths)"
        return result
    result["files"] = files

    try:
        tracked = _git_tracked(repo, files)
        shipped = _task_files_are_shipped(repo, files, tracked)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"artifact verification failed: {exc!r}"
        return result

    result["confirmed"] = bool(shipped)
    if not shipped:
        result["warning"] = "not all listed task files are git-tracked at the base branch"
    return result


# --- CLI ------------------------------------------------------------------------

def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--root", default="docs/specs")
    p.add_argument(
        "--verify", metavar="SPEC_ID", default=None,
        help="verify a single already-judged candidate spec_id instead of scanning for candidates",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo)
    if args.verify:
        res = verify(repo, args.verify, root=args.root)
    else:
        res = check(repo, root=args.root)

    if args.json:
        print(json.dumps(res))
    elif args.verify:
        if res["confirmed"]:
            print(f"CONFIRMED: {res['spec_id']} is a shipped collision")
        else:
            print(f"not confirmed: {res.get('warning') or 'no evidence of a shipped collision'}")
    else:
        if res["checked"]:
            candidates = res["candidates"]
            print(f"checked {len(candidates)} candidate spec(s) under {args.root}")
            for c in candidates:
                print(f"  - {c['spec_id']}: {c['title']}")
        else:
            print(f"unknown: {res.get('warning') or 'no docs/specs/ directory found'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
