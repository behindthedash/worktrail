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

import json
from pathlib import Path
from typing import Any, Callable, Dict, List

from ..taskformats.devkit.checkbox_audit import (
    COMPLETION_AUDIT_SECTIONS,
    _all_checkboxes_checked,
    read_task_file,
)


def _recheck_checkbox_drift(
    repo: Path, findings: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """Re-derive each `drift-findings` entry against the task file's current
    on-disk state.

    A finding is "still-true" when its task file is still `status:
    completed` with at least one unchecked box in
    `COMPLETION_AUDIT_SECTIONS`, and "resolved" when the file is fully
    checked or no longer `status: completed`. A finding whose task file
    can no longer be read (missing, or unparseable frontmatter) raises,
    so the caller degrades the whole brief's recheck to
    `outcome="error"` rather than reporting a partial result.
    """
    still_true: List[str] = []
    resolved: List[str] = []
    for finding in findings:
        rel_path = finding["path"]
        task_path = Path(rel_path)
        if not task_path.is_absolute():
            task_path = repo / task_path
        frontmatter, error, body = read_task_file(task_path)
        if error is not None or frontmatter is None:
            raise ValueError(f"cannot read task file {rel_path!r}: {error}")
        if frontmatter.get("status") == "completed" and not _all_checkboxes_checked(
            body, sections=COMPLETION_AUDIT_SECTIONS
        ):
            still_true.append(rel_path)
        else:
            resolved.append(rel_path)
    return {"still_true": still_true, "resolved": resolved}


# drift-source -> recheck function. Populated as individual sweep predicates
# are implemented; an unpopulated (or unmatched) drift-source degrades to
# outcome="unrecognized" in `recheck()` below.
PREDICATE_RECHECKS: Dict[str, Callable[[Path, List[Dict[str, Any]]], Dict[str, List[str]]]] = {
    "checkbox-drift-sweep": _recheck_checkbox_drift,
}


def recheck(repo: Path, frontmatter: Dict[str, Any]) -> Dict[str, object]:
    """Never raises. Returns:
    {"attempted": bool, "drift_source": str|None, "outcome": "no-predicate"|
     "unrecognized"|"error"|"still-true"|"resolved", "still_true": [...],
     "resolved": [...], "error": str|None}
    """
    drift_source = frontmatter.get("drift-source")
    if drift_source is None:
        return {
            "attempted": False,
            "drift_source": None,
            "outcome": "no-predicate",
            "still_true": [],
            "resolved": [],
            "error": None,
        }

    recheck_fn = PREDICATE_RECHECKS.get(drift_source)
    if recheck_fn is None:
        return {
            "attempted": False,
            "drift_source": drift_source,
            "outcome": "unrecognized",
            "still_true": [],
            "resolved": [],
            "error": None,
        }

    findings = frontmatter.get("drift-findings")
    if not findings:
        return {
            "attempted": True,
            "drift_source": drift_source,
            "outcome": "error",
            "still_true": [],
            "resolved": [],
            "error": "drift-findings is missing or empty",
        }

    try:
        result = recheck_fn(repo, findings)
    except Exception as exc:  # noqa: BLE001 - degrade any predicate failure to outcome="error"
        return {
            "attempted": True,
            "drift_source": drift_source,
            "outcome": "error",
            "still_true": [],
            "resolved": [],
            "error": str(exc),
        }

    still_true = result.get("still_true", [])
    resolved = result.get("resolved", [])
    outcome = "still-true" if still_true else "resolved"
    return {
        "attempted": True,
        "drift_source": drift_source,
        "outcome": outcome,
        "still_true": still_true,
        "resolved": resolved,
        "error": None,
    }


# --- CLI --------------------------------------------------------------------

def _format_human(res: Dict[str, object]) -> str:
    if not res["attempted"]:
        return f"not attempted: outcome={res['outcome']}"
    if res["outcome"] == "error":
        return f"error: {res.get('error')}"
    return (
        f"outcome={res['outcome']} drift_source={res['drift_source']!r} "
        f"still_true={res['still_true']} resolved={res['resolved']}"
    )


def main(argv=None) -> int:
    import argparse

    from ..shared.brief_frontmatter import read_frontmatter

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--brief", required=True)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    frontmatter = read_frontmatter(Path(args.brief))
    res = recheck(Path(args.repo), frontmatter)

    if args.json:
        print(json.dumps(res))
    else:
        print(_format_human(res))

    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
