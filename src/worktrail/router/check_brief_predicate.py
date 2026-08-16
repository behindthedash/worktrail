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

The two terminal outcomes each reuse an existing queue/run-record pattern
rather than inventing a new one (see design.md - Decisions): `"resolved"`
reuses `work_queue.py done`'s `--note`, and `"still-true"` reuses the
post-Phase-6 `worktrail-run-record append "$RUN" decisions "..."` pattern the
probe-based "proceed" outcome already uses in `brief-staleness-check.md`.
`format_still_true_evidence` builds that exact line from `recheck()`'s
`still_true` list so Phase 5.5's skill doc has one canonical string to call
instead of composing prose ad hoc:

    Predicate re-check (checkbox-drift-sweep) found the staleness predicate
    still true for 2 finding(s): docs/specs/x/tasks/TASK-001.md,
    docs/specs/x/tasks/TASK-004.md. Proceeded automatically without an
    operator prompt.

Unlike the probe-based line it mirrors, there is no commit SHA or PR number
to cite -- the probe search never runs on this path -- so the still-true
task-file paths themselves are the cited evidence.

`format_resolved_closure_note` builds the mirror-image string for the
`"resolved"` outcome, passed as `work_queue.py done`'s `--note` the same way
the probe-based "close as already-delivered" branch's note is:

    worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note \\
      "Closed as already-delivered: predicate re-check (checkbox-drift-sweep)
      found the staleness predicate resolved for 2 finding(s):
      docs/specs/x/tasks/TASK-001.md, docs/specs/x/tasks/TASK-004.md. Surfaced
      by the Phase 5.5 predicate re-check; closed automatically without an
      operator prompt."

As with the still-true evidence line, the predicate re-check itself -- not a
commit SHA or PR number -- is the cited evidence, since the probe search
never runs on this path.
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


def format_still_true_evidence(result: Dict[str, object]) -> str:
    """Build the exact evidence line for `recheck()`'s `"still-true"` outcome.

    Callers append this via the same post-Phase-6 pattern the probe-based
    "proceed" outcome uses (`worktrail-run-record append "$RUN" decisions
    "<this string>"`), so the run record reads the same way regardless of
    which path decided to proceed. There is no commit SHA or PR number to
    cite here -- the probe search never runs on this path -- so the
    still-true task-file paths from `result["still_true"]` are the cited
    evidence instead.
    """
    still_true = result["still_true"]
    paths = ", ".join(still_true)
    return (
        f"Predicate re-check ({result['drift_source']}) found the staleness "
        f"predicate still true for {len(still_true)} finding(s): {paths}. "
        "Proceeded automatically without an operator prompt."
    )


def format_resolved_closure_note(result: Dict[str, object]) -> str:
    """Build the exact closure note for `recheck()`'s `"resolved"` outcome.

    Callers pass this as `work_queue.py done`'s `--note` the same way the
    probe-based "close as already-delivered" branch does
    (`worktrail-work-queue done "$BRIEF_ID" --implementation-complete --note
    "<this string>"`), so both closure paths read the same way in the
    queue's history regardless of which one fired. Unlike that sibling note,
    there is no commit SHA or PR number to cite -- the probe search never
    runs on this path -- so the predicate re-check itself, named explicitly,
    is the cited evidence, alongside the resolved task-file paths from
    `result["resolved"]`.
    """
    resolved = result["resolved"]
    paths = ", ".join(resolved)
    return (
        "Closed as already-delivered: predicate re-check "
        f"({result['drift_source']}) found the staleness predicate resolved "
        f"for {len(resolved)} finding(s): {paths}. Surfaced by the Phase 5.5 "
        "predicate re-check; closed automatically without an operator "
        "prompt."
    )


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
