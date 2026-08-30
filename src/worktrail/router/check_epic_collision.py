#!/usr/bin/env python3
"""
`/go`'s pre-dispatch epic-collision guard for Route B (epic planning).

Route C/D/F/G already refuse to dispatch blind: `check_spec_collision.py`
compares the request against every already-`Implemented` spec first
(`references/spec-collision-check.md`). Route B has no equivalent -- it can
be dispatched to author a *new* `docs/specs/epics/<id>.md` decomposition
document even when an epic covering the same scope already exists with its
own feature decomposition and a delivery vehicle (a citing spec) already in
flight. Incident (2026-08-27, decision `20260827-030725`): a claimed brief
carried a stale `recommended-route: B`; epic `004-james-agentic-vertical-slice`
already existed with the brief's exact scope as "Feature 2 -- tracked under
spec `053`/`088`", both specs Draft with pending tasks -- yet nothing before
Route B's playbook began surfaced that, so the executing agent had to
discover it mid-run and improvised a blocking human decision over a fact the
router already had the tooling to check: `dashboard.detect_epic_stage()` and
`scan_epics()` already extract exactly this (`citing_specs` per epic, from
`seed_backlog.py`'s backlog-brief seeding) -- this module is the missing
pre-dispatch consumer of that existing extraction, mirroring
`check_spec_collision.py`'s shape for epics instead of specs.

Division of labor, same as `check_spec_collision.py`: `check()` is pure
extraction (no semantic judgment) -- one candidate row per
`docs/specs/epics/<id>.md`, each already carrying its citing specs via
`dashboard.detect_epic_stage()`. Judging whether a candidate's title/
feature_summary matches the request (same actor + capability + primary
domain rule `references/subagent-prompts.md#overlap-check` already applies)
is the calling agent's job. When a candidate is judged a match with a
non-empty `citing_specs`, the caller treats it exactly like
`check_spec_collision.py`'s "task-level match" contract
(`references/spec-collision-check.md#task-level-matches`): redirect the
dispatch at the existing citing spec(s) instead of authoring a new epic --
never an auto-close, since a citing spec at Draft/pending-tasks status is
open, unshipped work, not a confirmed-shipped duplicate.

Best-effort, never raises: any internal failure (unreadable epic file,
missing epics directory, malformed epic doc) degrades to `checked: false`
plus a non-null `warning`, mirroring `check_repo_freshness.py`'s and
`check_spec_collision.py`'s own contract -- callers must treat `checked:
false` as "no signal", never as "no collision".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from .dashboard import EPIC_ID_RE, detect_epic_stage

# --- provider-neutral pending-decision envelope ---------------------------------

# `source` provenance recorded on every envelope this guard builds; the same
# string the decision queue's records carry in their frontmatter.
GUARD_SOURCE = "check_epic_collision"

# Static question/options: identity is derived from (source, repo, subject,
# question), so a re-run against the same matched epic converges on the same
# decision id instead of filing duplicates. The match evidence itself stays
# in `check()`'s own `candidates`, never inside the question text.
DECISION_QUESTION = (
    "An existing epic already decomposes this scope with a citing spec in "
    "progress. Should this dispatch redirect onto the citing spec instead of "
    "authoring a new epic, or is a new epic still the right artifact?"
)
DECISION_OPTIONS = [
    "redirect: continue against the existing citing spec instead of a new epic",
    "author-anyway: the match is superficial; author the new epic as originally routed",
]


def _decision_helpers():
    """Best-effort import of the decision-envelope primitives. Returns
    `(decision_identity, pending_decision_envelope)`, or `(None, None)` when
    `workqueue.decisions` cannot be imported -- the envelope degrades to
    `None`, never an exception."""
    try:
        from ..workqueue.decisions import (
            decision_identity,
            pending_decision_envelope,
        )
    except Exception:  # noqa: BLE001 - envelope is additive, never fatal
        return None, None
    return decision_identity, pending_decision_envelope


def build_pending_decision(
    epic_id: str,
    repo: str,
    *,
    run_id: str | None = None,
    dispatch_mode: str | None = None,
) -> dict[str, Any] | None:
    """Build the versioned pending-decision envelope for an ambiguous epic
    match (a matched candidate whose `citing_specs` don't clearly resolve to
    one delivery vehicle).

    Deterministic identity via `decisions.decision_identity()` keyed on
    (source, repo, subject=epic_id, question), so a re-run against the same
    matched epic converges on the same id. Never raises: any failure (missing
    primitives, blank inputs) returns `None`.
    """
    try:
        epic_id = str(epic_id or "").strip()
        repo = str(repo or "").strip()
        if not epic_id or not repo:
            return None
        identity, envelope = _decision_helpers()
        if identity is None:
            return None
        decision_id = identity(GUARD_SOURCE, repo, epic_id, DECISION_QUESTION)
        return envelope(
            decision_id=decision_id,
            question=DECISION_QUESTION,
            options=list(DECISION_OPTIONS),
            source=GUARD_SOURCE,
            repo=repo,
            subject=epic_id,
            brief=None,
            run_id=run_id,
            dispatch_mode=dispatch_mode,
        )
    except Exception:  # noqa: BLE001 - envelope is additive, never fatal
        return None


_H1_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
_BUSINESS_OBJECTIVE_RE = re.compile(
    r"^##\s+Business Objective\s*\n+(.*?)(?=\n#{1,6}\s|\Z)",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)


def _epic_title(text: str, epic_id: str) -> str:
    m = _H1_RE.search(text)
    if m:
        title = m.group(1).strip()
        if title:
            return title
    return epic_id


def _epic_feature_summary(text: str) -> str | None:
    m = _BUSINESS_OBJECTIVE_RE.search(text)
    if not m:
        return None
    paragraph = m.group(1).strip().split("\n\n", 1)[0].strip()
    return paragraph or None


def check(repo: Path, root: str = "docs/specs/epics") -> dict[str, object]:
    """Enumerate `docs/specs/epics/` candidates for the calling agent to judge.

    Returns `{"checked": bool, "candidates": [{"epic_id", "title", "status",
    "feature_summary", "stage", "features", "citing_specs"}], "warning": str|None}`.
    Each candidate's `stage`/`features`/`citing_specs` come straight from
    `dashboard.detect_epic_stage()` -- no reimplementation of that citation
    scan. `checked=False` means the candidate index could not be built (no
    `docs/specs/epics/` directory, or an internal failure) -- callers must
    treat that as "no signal", never as "no collision".
    """
    repo = Path(repo)
    result: dict[str, object] = {"checked": False, "candidates": [], "warning": None}

    epics_dir = repo / root
    if not epics_dir.is_dir():
        return result

    try:
        epic_files = sorted(
            f for f in epics_dir.glob("*.md") if EPIC_ID_RE.match(f.stem)
        )
    except OSError as exc:
        result["warning"] = f"failed to list {epics_dir}: {exc!r}"
        return result

    candidates: list[dict[str, Any]] = []
    for epic_file in epic_files:
        try:
            text = epic_file.read_text(encoding="utf-8")
            stage_info = detect_epic_stage(epic_file, repo)
        except Exception:  # noqa: BLE001, S112 - one bad epic file, skip it
            continue
        candidates.append(
            {
                "epic_id": stage_info["id"],
                "title": _epic_title(text, stage_info["id"]),
                "status": stage_info.get("status_header"),
                "feature_summary": _epic_feature_summary(text),
                "stage": stage_info.get("stage"),
                "features": stage_info.get("features"),
                "citing_specs": stage_info.get("citing_specs") or [],
            }
        )

    result["checked"] = True
    result["candidates"] = candidates
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="pre-dispatch epic-collision guard for Route B"
    )
    parser.add_argument("--repo", required=True, help="repo root to scan")
    parser.add_argument(
        "--root",
        default="docs/specs/epics",
        help="epics directory relative to --repo (default: docs/specs/epics)",
    )
    parser.add_argument(
        "--decision-for",
        metavar="EPIC_ID",
        help=(
            "build the pending-decision envelope for an ambiguous match "
            "against this epic id, instead of listing candidates"
        ),
    )
    parser.add_argument("--run-id", help="run record id, for --decision-for's envelope")
    parser.add_argument(
        "--dispatch-mode", help="dispatch mode, for --decision-for's envelope"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.decision_for:
        decision = build_pending_decision(
            args.decision_for,
            args.repo,
            run_id=args.run_id,
            dispatch_mode=args.dispatch_mode,
        )
        if args.json:
            print(json.dumps(decision))
        else:
            print(
                decision or "pending_decision: null (decision primitives unavailable)"
            )
        return 0

    result = check(Path(args.repo), args.root)
    if args.json:
        print(json.dumps(result))
    else:
        if not result["checked"]:
            print(f"checked: false ({result['warning'] or 'no epics directory'})")
        for c in result["candidates"]:
            print(
                f"{c['epic_id']}: stage={c['stage']} citing_specs={c['citing_specs']} "
                f"-- {c['title']}"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
