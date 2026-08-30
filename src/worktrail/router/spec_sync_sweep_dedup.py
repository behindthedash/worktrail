#!/usr/bin/env python3
"""spec_sync_sweep_dedup.py — identity-based dedup lookup for the spec-sync
drift sweep (REQ-015).

Before filing a new Drift Brief for a repo, the sweep must check whether an
unresolved one already exists so repeated sweep runs don't pile up duplicate
briefs for the same drift. "Unresolved" means: sitting in `queue/` (any
status — everything there is by definition unresolved), or sitting in
`picked/` with a status other than `done` (claimed but not yet finished).
A `picked/` brief with `status: done` is resolved and does not block a new
brief from being filed if the repo drifts again (REQ-017).

Identity is `repo` + `drift-source` (defaulting to `spec-sync-sweep`) — narrower
than `score_candidates.py`'s fuzzy content-similarity matching. A brief that
merely mentions the same repo path without a matching `drift-source` marker
(e.g. an unrelated, manually-authored brief, or a brief filed for a different
check-type) must not be treated as a match. Keying on `(repo, drift-source)`
lets a repo carry one outstanding brief per check-type independently — e.g.
`spec-sync-sweep` and `checkbox-drift-sweep` — without either suppressing or
being suppressed by the other.
"""

from __future__ import annotations

from pathlib import Path

from ..shared.brief_frontmatter import read_frontmatter

DRIFT_SOURCE = "spec-sync-sweep"


def _md_files(d: Path) -> list[Path]:
    if not d.is_dir():
        return []
    return sorted(f for f in d.iterdir() if f.is_file() and f.suffix == ".md")


def _is_match(path: Path, repo: str, drift_source: str) -> bool:
    fm = read_frontmatter(path)
    return fm.get("repo") == repo and fm.get("drift-source") == drift_source


def find_unresolved_drift_brief(
    repo: Path,
    queue_base: Path,
    drift_source: str = DRIFT_SOURCE,
) -> Path | None:
    """Return the path of an unresolved Drift Brief for `repo`, or None.

    Scans `queue_base/queue/` (any status counts as unresolved) and
    `queue_base/picked/` (unresolved unless `status: done`) for a brief
    whose `repo` frontmatter equals `str(repo)` and whose `drift-source`
    frontmatter equals `drift_source` (defaults to `spec-sync-sweep` for
    backward compatibility with the original call site).
    """
    repo_str = str(repo)

    for path in _md_files(queue_base / "queue"):
        if _is_match(path, repo_str, drift_source):
            return path

    for path in _md_files(queue_base / "picked"):
        if not _is_match(path, repo_str, drift_source):
            continue
        fm = read_frontmatter(path)
        if fm.get("status") == "done":
            continue
        return path

    return None
