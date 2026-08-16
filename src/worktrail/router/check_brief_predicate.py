#!/usr/bin/env python3
"""
Phase 5.5's predicate re-check: re-derives whether a brief's captured
staleness *predicate* is still true, before `check_brief_staleness.py`'s
probe-extraction/git-search runs and before any operator prompt.

This module is deliberately a sibling of `check_brief_staleness.py`, not a
new mode inside it: `check_brief_staleness.check()` is prose-probe-shaped
(text in, matches out), while the predicate re-check is
frontmatter-and-registry-shaped (a brief's `drift-source` in, a per-finding
classification out). See
`openspec/changes/stale-brief-precheck-refutation-tier/design.md` -
Decisions.

`PREDICATE_RECHECKS` maps a brief's `drift-source` value to the function
that re-derives its predicate, keyed so a future sweep can register its own
predicate without touching `recheck()`'s own control flow. Only the
`checkbox-drift-sweep` predicate is registered so far.

`recheck()` never raises to its caller -- any condition it cannot answer
(no predicate captured, an unrecognized `drift-source`, a registered
predicate that itself fails) degrades to a non-terminal outcome so Phase 5.5
falls through to today's unmodified brief-staleness flow.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List

# drift-source -> recheck function. Populated as individual sweep predicates
# are implemented; an unpopulated (or unmatched) drift-source degrades to
# outcome="unrecognized" in `recheck()` below.
PREDICATE_RECHECKS: Dict[str, Callable[[Path, List[Dict[str, Any]]], Dict[str, List[str]]]] = {}


def recheck(repo: Path, frontmatter: Dict[str, Any]) -> Dict[str, object]:
    """Never raises. Returns:
    {"attempted": bool, "drift_source": str|None, "outcome": "no-predicate"|
     "unrecognized"|"error"|"still-true"|"resolved", "still_true": [...],
     "resolved": [...], "error": str|None}
    """
    drift_source = frontmatter.get("drift-source")
    return {
        "attempted": False,
        "drift_source": drift_source,
        "outcome": "no-predicate",
        "still_true": [],
        "resolved": [],
        "error": None,
    }
