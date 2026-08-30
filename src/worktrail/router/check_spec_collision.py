#!/usr/bin/env python3
"""
`/go`'s pre-dispatch spec-collision guard (Spec-Collision Guard, per
`docs/specs/ontology.md`).

Before dispatching a fresh brief/spec, `/go` should be able to ask: "does an
existing spec in `docs/specs/` already claim to cover this?" This module
answers that in two separate, best-effort-only steps, mirroring
`check_repo_freshness.py`'s shape:

  1. `check(repo, root, target)` -- pure extraction. Delegates to
     `overlap_check.scan()` for the whole-spec candidate index (spec_id +
     title + feature_summary per spec) and performs NO semantic judgment of
     its own: deciding whether a candidate is a strong match (same actor +
     capability + primary domain, or a clear sub-set/extension) is the
     calling agent's job, applying the same comparison rule `overlap_check.py`
     already documents for the brainstorm overlap gate.

     When the caller passes an explicit `target` OpenSpec change id, `check()`
     additionally delegates to `overlap_check.task_candidates()` for that
     change's open, unchecked tasks and surfaces them under the separate
     `task_candidates` key -- never merged into `candidates` -- so a
     task-level match (open, unchecked work) is structurally distinguishable
     from a whole-spec match (only ever confirmed via `verify()`'s
     `Implemented` + shipped-artifacts check below). A task-level match is
     never grounds for the existing auto-close-on-Implemented behavior:
     nothing in `task_candidates` is ever fed to `verify()`.

  2. `verify(repo, spec_id, root)` -- artifact verification for a single
     candidate the calling agent has already judged a semantic match
     (Collision Candidate -> Confirmed Collision). Checks the candidate's
     `**Status**:` header and whether its task `files:` are git-tracked at
     the repo's base branch, reusing `dashboard.py`'s own stale-bookkeeping
     helpers (`_git_tracked`, `_task_files_are_shipped`, `_load_tasks`)
     rather than reimplementing them.

When `verify()` confirms a collision, its result additionally carries
`pending_decision`: the provider-neutral, versioned pending-decision
envelope (`worktrail.pending-decision`, built via
`workqueue/decisions.py`'s `pending_decision_envelope()` under a
deterministic `decision_identity()`) the attended host presents for a human
to answer -- proceed despite the shipped collision, extend the existing
spec, or redirect. It is absent (`None`) whenever nothing was confirmed and
degrades to `None` when the decision primitives are unavailable; filing it
via `ask(decision_id=...)` stays the caller's job.

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
from typing import Any

from .dashboard import (
    _dir_creation_timestamp,
    _git_tracked,
    _load_tasks,
    _task_files_are_shipped,
)

# dashboard.py is a sibling module -- its `_git_tracked`/
# `_task_files_are_shipped` stale-bookkeeping helpers and `_load_tasks`
# task-frontmatter loader are reused verbatim for REQ-002's artifact
# verification (no fresh `git ls-files` call, no reimplemented frontmatter
# parsing). `find_spec_file` is reused to locate the candidate's spec doc
# for its `**Status**:` header.
from .dashboard import (
    find_spec_file as _find_spec_file,
)

# overlap_check is a sibling module -- reused for the candidate index rather
# than re-implementing its extraction logic.
from .overlap_check import scan as _scan
from .overlap_check import task_candidates as _task_candidates

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


def _fallback_find_spec_file(spec_dir: Path) -> Path | None:
    cands = [
        f
        for f in spec_dir.glob("*.md")
        if not any(f.stem.lower().endswith(s) for s in _AUX_SUFFIX)
    ]
    if not cands:
        return None
    dated = sorted(f for f in cands if _DATE_PREFIX.match(f.name))
    return dated[-1] if dated else min(cands)


def _status_header(spec_text: str) -> str | None:
    m = _STATUS_RE.search(spec_text)
    return m.group(1).strip() if m else None


def _non_file_artifact_note(spec_text: str) -> str | None:
    m = _ARTIFACT_CLAIM_RE.search(spec_text)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def _files_from_traceability_matrix(spec_dir: Path) -> list[str]:
    """Fallback file-path extraction for a pre-task-split spec (no tasks/
    dir): backtick-quoted, path-shaped tokens from traceability-matrix.md."""
    tm = spec_dir / "traceability-matrix.md"
    if not tm.is_file():
        return []
    try:
        text = tm.read_text(errors="ignore")
    except OSError:
        return []
    return sorted(
        {m.group(1) for m in _TRACE_FILE_RE.finditer(text) if "/" in m.group(1)}
    )


def _collect_task_files(spec_dir: Path) -> list[str]:
    if _load_tasks is not None:
        try:
            tasks = _load_tasks(spec_dir)
        except Exception:  # noqa: BLE001
            tasks = None
        if tasks:
            files = sorted({f for t in tasks for f in (t.get("files") or [])})
            if files:
                return files
    return _files_from_traceability_matrix(spec_dir)


# --- check(): pure extraction --------------------------------------------------

# --- provider-neutral pending-decision envelope ---------------------------------

# `source` provenance recorded on every envelope this guard builds; the same
# string the decision queue's records carry in their frontmatter.
GUARD_SOURCE = "check_spec_collision"

# Static question/options: identity is derived from (source, repo, subject,
# question), so a re-run on unchanged facts converges on the same decision id
# instead of filing duplicates. The evidence itself travels in `verify()`'s
# own output, never inside the question text.
DECISION_QUESTION = (
    "A shipped spec already covers this scope: its Status is Implemented and "
    "its task files are verified git-tracked at the base branch. How should "
    "this request proceed?"
)
DECISION_OPTIONS = [
    "extend: continue the existing spec instead of starting overlapping work",
    "proceed-anyway: dispatch despite the confirmed collision, recording why",
    "redirect: choose a different scope for this request",
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
    repo: Path,
    spec_id: str,
    *,
    run_id: str | None = None,
    dispatch_mode: str | None = None,
) -> dict[str, Any] | None:
    """Build the versioned pending-decision envelope for a confirmed collision.

    Deterministic identity via `decisions.decision_identity()` keyed on
    (source, repo, subject=spec_id, question), so a re-run against the same
    shipped spec converges on the same id. Provenance (`repo`, `subject`,
    optional `run_id`/`dispatch_mode`) travels inside the envelope so the
    resuming side can validate the answer it finds. Never raises: any failure
    (missing primitives, blank inputs) returns `None`.
    """
    try:
        repo_str = str(Path(repo).resolve())
        identity, envelope = _decision_helpers()
        if identity is None or not spec_id or not str(spec_id).strip():
            return None
        decision_id = identity(
            GUARD_SOURCE, repo_str, str(spec_id).strip(), DECISION_QUESTION
        )
        return envelope(
            decision_id=decision_id,
            question=DECISION_QUESTION,
            options=list(DECISION_OPTIONS),
            source=GUARD_SOURCE,
            repo=repo_str,
            subject=str(spec_id).strip(),
            brief=None,
            run_id=run_id,
            dispatch_mode=dispatch_mode,
        )
    except Exception:  # noqa: BLE001 - envelope is additive, never fatal
        return None


def check(
    repo: Path, root: str = "docs/specs", target: str | None = None
) -> dict[str, object]:
    """Enumerate `docs/specs/` candidates for the calling agent to judge.

    Returns `{"checked": bool, "candidates": [{"spec_id", "stage", "title",
    "feature_summary"}], "task_candidates": [{"spec_id", "task_id",
    "task_text", "checked"}], "warning": str|None,
    "pending_decision": None}`. `pending_decision` is always `None` here:
    `check()` performs no semantic matching of its own, so it never presumes
    a collision worth a decision -- only `verify()`'s confirmed collisions
    carry an envelope. `checked=False` means
    the whole-spec index could not be built (no `docs/specs/` dir, or an
    internal failure) -- callers must treat that as "no signal", never as
    "no collision". Performs no semantic matching of its own; that judgment
    is the calling agent's, applying `overlap_check.py`'s existing comparison
    rule.

    When `target` names an explicit OpenSpec change id, `task_candidates` is
    additionally populated with that change's open, unchecked tasks (via
    `overlap_check.task_candidates()`) -- kept in its own key, never merged
    into `candidates`, so a task-level match (open, unchecked) can never be
    confused with a whole-spec match (only ever confirmed via `verify()`'s
    `Implemented` + shipped-artifacts check). A devkit-shaped root, no
    `target`, or a `target` with no readable `tasks.md` leaves
    `task_candidates` empty -- `checked`/`candidates` are unaffected either
    way.
    """
    repo = Path(repo)
    result: dict[str, object] = {
        "checked": False,
        "candidates": [],
        "task_candidates": [],
        "warning": None,
        "pending_decision": None,
    }

    if _scan is None:
        result["warning"] = (
            "overlap_check import failed; cannot enumerate spec candidates"
        )
        return result

    specs_root = repo / root
    if not specs_root.is_dir():
        return result

    try:
        specs = _scan(specs_root)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"failed to scan {specs_root}: {exc!r}"
        return result

    candidates: list[dict[str, Any]] = []
    for s in specs:
        try:
            candidates.append(
                {
                    "spec_id": s["spec_id"],
                    "stage": s.get("stage"),
                    "title": s.get("title"),
                    "feature_summary": s.get("feature_summary"),
                }
            )
        except Exception:  # noqa: BLE001, S112 - malformed candidate entry, skip it
            continue

    result["checked"] = True
    result["candidates"] = candidates

    if target:
        result["task_candidates"] = _task_level_candidates(specs_root, target)

    return result


def _task_level_candidates(specs_root: Path, target: str) -> list[dict[str, Any]]:
    """Task-level candidates for `target`, tagged with `spec_id` -- empty
    (never raising) unless `target` resolves to an OpenSpec change with a
    readable `tasks.md` (`overlap_check.task_candidates()` falls back to
    whole-spec/whole-change `scan()` shape otherwise, which this rejects by
    checking for the `task_id` key rather than assuming the target
    resolved)."""
    if _task_candidates is None:
        return []
    try:
        raw = _task_candidates(specs_root, target)
    except Exception:  # noqa: BLE001 - best-effort, never raise to caller
        return []
    return [
        {
            "spec_id": target,
            "task_id": e["task_id"],
            "task_text": e["task_text"],
            "checked": e["checked"],
        }
        for e in raw
        if "task_id" in e
    ]


# --- verify(): artifact verification for a single judged candidate ------------


def verify(
    repo: Path,
    spec_id: str,
    root: str = "docs/specs",
    *,
    run_id: str | None = None,
    dispatch_mode: str | None = None,
) -> dict[str, object]:
    """Confirm (or refute) a candidate the calling agent has already judged
    a semantic match.

    Returns `{"spec_id", "confirmed": bool, "status": str|None,
    "files": [str], "note": str|None, "warning": str|None,
    "pending_decision": envelope|None}`.
    `confirmed=True` only when the candidate's `**Status**:` header reads
    `Implemented` AND every task `files:` entry is git-tracked at the repo's
    base checkout. A confirmed collision additionally carries
    `pending_decision`: `build_pending_decision()`'s provider-neutral
    envelope for the human's proceed/extend/redirect call. Any non-file
    artifact claim found in the spec's prose is surfaced via `note` and never
    affects `confirmed`. Never raises --
    every failure path (missing spec dir, unreadable spec file, no files to
    verify, git failure) degrades to `confirmed: false` plus a `warning`.
    """
    repo = Path(repo)
    result: dict[str, object] = {
        "spec_id": spec_id,
        "confirmed": False,
        "status": None,
        "files": [],
        "note": None,
        "warning": None,
        "pending_decision": None,
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
        result["warning"] = (
            "dashboard git-tracking helpers unavailable; cannot verify artifacts"
        )
        return result

    try:
        files = _collect_task_files(spec_dir)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"failed to collect task files: {exc!r}"
        return result
    if not files:
        result["warning"] = (
            "no task files found to verify (no tasks/ dir and no traceability-matrix.md paths)"
        )
        return result
    result["files"] = files

    try:
        tracked = _git_tracked(repo, files)
        since_ts = _dir_creation_timestamp(str(repo), str(spec_dir))
        shipped = _task_files_are_shipped(repo, files, tracked, since_ts)
    except Exception as exc:  # noqa: BLE001 - best-effort, never raise to caller
        result["warning"] = f"artifact verification failed: {exc!r}"
        return result

    result["confirmed"] = bool(shipped)
    if shipped:
        result["pending_decision"] = build_pending_decision(
            repo, spec_id, run_id=run_id, dispatch_mode=dispatch_mode
        )
    else:
        result["warning"] = (
            "not all listed task files are git-tracked at the base branch"
        )
    return result


# --- CLI ------------------------------------------------------------------------


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--root", default="docs/specs")
    p.add_argument(
        "--verify",
        metavar="SPEC_ID",
        default=None,
        help="verify a single already-judged candidate spec_id instead of scanning for candidates",
    )
    p.add_argument(
        "--target",
        metavar="CHANGE_ID",
        default=None,
        help="explicit target OpenSpec change id; also populates task_candidates "
        "with that change's open, unchecked tasks (ignored with --verify)",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo)
    if args.verify:
        res = verify(repo, args.verify, root=args.root)
    else:
        res = check(repo, root=args.root, target=args.target)

    if args.json:
        print(json.dumps(res))
    elif args.verify:
        if res["confirmed"]:
            print(f"CONFIRMED: {res['spec_id']} is a shipped collision")
            decision = res.get("pending_decision")
            if decision:
                print(
                    f"  -> pending decision {decision['decision_id']}: "
                    "surface this envelope to the operator"
                )
        else:
            print(
                f"not confirmed: {res.get('warning') or 'no evidence of a shipped collision'}"
            )
    else:
        if res["checked"]:
            candidates = res["candidates"]
            print(f"checked {len(candidates)} candidate spec(s) under {args.root}")
            for c in candidates:
                print(f"  - {c['spec_id']}: {c['title']}")
            task_candidates = res["task_candidates"]
            if task_candidates:
                print(
                    f"  {len(task_candidates)} open task-level candidate(s) in {args.target}:"
                )
                for t in task_candidates:
                    print(f"    - {t['task_id']}: {t['task_text']}")
        else:
            print(f"unknown: {res.get('warning') or 'no docs/specs/ directory found'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
