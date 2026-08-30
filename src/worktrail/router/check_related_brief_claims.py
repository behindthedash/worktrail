#!/usr/bin/env python3
"""
`/go`'s Phase 5.5 related-brief collision guard.

A brief's `related:` frontmatter list names other briefs describing adjacent
or overlapping work. Nothing before dispatch checks whether one of those
related briefs is *already claimed and in flight* -- a second agent can start
work that collides with a session already underway, discovered only when the
two land conflicting changes. This module answers one question: "of this
brief's `related:` ids, which ones are actively claimed by someone else right
now?" so a human can be asked before dispatch proceeds, in the same shape as
its sibling guards: pure extraction, then a bounded,
best-effort lookup, every step best-effort and **never raising to its
caller**. Any condition under which the question cannot be answered (an
unreadable claimed brief) degrades to `checked: false` plus a non-null
`warning` -- never an exception. `checked: false` and `checked: true,
active: []` are deliberately different answers: the first means the question
could not be asked, the second means it was asked and nothing collides.

An individual related id that fails to resolve -- missing, ambiguous, or
whose own frontmatter can't be read -- is skipped, never treated as a reason
to abort the whole check; the ids that *do* resolve cleanly still deserve an
answer.

Evidence surfaced here is for a human to judge, never auto-applied: this
module never blocks dispatch or mutates any brief.

When `check()` finds actively claimed related ids, its result additionally
carries `pending_decision`: the provider-neutral, versioned pending-decision
envelope (`worktrail.pending-decision`, built via `workqueue/decisions.py`'s
`pending_decision_envelope()` under a deterministic `decision_identity()`
keyed on the guard, queue-side repo, and claimed brief) for the
coordinate/wait/proceed call. It is absent (`None`) whenever nothing is
actively claimed and degrades to `None` when the decision primitives are
unavailable; filing it via `ask(decision_id=...)` stays the caller's job.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..shared.brief_frontmatter import read_frontmatter
from ..shared.homedir import worktrail_home
from ..workqueue import work_queue as _wq
from ..workqueue.work_queue import _agent_label as _wq_agent_label
from ..workqueue.work_queue import resolve as _wq_resolve

# A related brief's focus is surfaced for a human to skim, not to read in
# full -- truncated so one runaway focus paragraph doesn't dominate a
# batched prompt covering several related ids.
FOCUS_SUMMARY_LIMIT = 200

_FOCUS_BODY_RE = re.compile(r"^##\s+Focus\s*$\r?\n(.+)$", re.MULTILINE)


# --- provider-neutral pending-decision envelope ---------------------------------

# `source` provenance recorded on every envelope this guard builds; the same
# string the decision queue's records carry in their frontmatter.
GUARD_SOURCE = "check_related_brief_claims"

# Static question/options: identity is derived from (source, repo, subject,
# question), so a re-run against the same claimed brief converges on the same
# decision id instead of filing duplicates. The active claims themselves
# travel in `check()`'s own `active` list, never inside the question text.
DECISION_QUESTION = (
    "One or more related briefs of this brief are actively claimed by "
    "another session right now. How should dispatch proceed?"
)
DECISION_OPTIONS = [
    "coordinate: align with the owner(s) of the active claim(s) first",
    "wait: hold this brief until the related claim(s) land",
    "proceed-anyway: accept the overlap risk and dispatch, recording why",
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


def _claimed_subject(frontmatter: dict[str, Any], path: Path) -> str:
    """The claimed brief's stable subject for decision identity: its
    frontmatter `id:` when present (the canonical work-queue id), else the
    file stem -- both spellings name the same brief."""
    subject = str(frontmatter.get("id") or "").strip()
    return subject or path.stem


def _claimed_repo(frontmatter: dict[str, Any], active: list[dict[str, Any]]) -> str:
    """The repo-side anchor for decision identity: the claimed brief's own
    `repo:` field when present, else the first active claim's repo, else the
    literal ``unspecified`` -- `decision_identity()` refuses blank repos, and
    a missing field must degrade to a stable placeholder, never to no
    envelope at all."""
    repo = str(frontmatter.get("repo") or "").strip()
    if repo:
        return repo
    for entry in active:
        repo = str(entry.get("repo") or "").strip()
        if repo:
            return repo
    return "unspecified"


def build_pending_decision(
    subject: str,
    repo: str,
    *,
    run_id: str | None = None,
    dispatch_mode: str | None = None,
) -> dict[str, Any] | None:
    """Build the versioned pending-decision envelope for actively claimed
    related briefs.

    Deterministic identity via `decisions.decision_identity()` keyed on
    (source, repo, subject=claimed brief id, question), so a re-run against
    the same claimed brief converges on the same id. Provenance (`repo`,
    `subject`, optional `run_id`/`dispatch_mode`) travels inside the envelope
    so the resuming side can validate the answer it finds. Never raises: any
    failure (missing primitives, blank subject/repo) returns `None`.
    """
    try:
        subject = str(subject or "").strip()
        repo = str(repo or "").strip()
        if not subject or not repo:
            return None
        identity, envelope = _decision_helpers()
        if identity is None:
            return None
        decision_id = identity(GUARD_SOURCE, repo, subject, DECISION_QUESTION)
        return envelope(
            decision_id=decision_id,
            question=DECISION_QUESTION,
            options=list(DECISION_OPTIONS),
            source=GUARD_SOURCE,
            repo=repo,
            subject=subject,
            brief=None,
            run_id=run_id,
            dispatch_mode=dispatch_mode,
        )
    except Exception:  # noqa: BLE001 - envelope is additive, never fatal
        return None


def _focus_summary(path: Path, frontmatter: dict[str, Any]) -> str:
    """Best-effort truncated focus text for `path`: frontmatter `focus:`
    first, falling back to the first line under a `## Focus` body heading
    (the same two sources `work_queue.py`'s `_focus_of` reads), truncated to
    `FOCUS_SUMMARY_LIMIT`. Never raises; an unreadable file yields ``""``."""
    focus = frontmatter.get("focus")
    if not focus:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            content = ""
        match = _FOCUS_BODY_RE.search(content)
        focus = match.group(1).strip() if match else ""
    focus = str(focus or "")
    if len(focus) > FOCUS_SUMMARY_LIMIT:
        focus = focus[:FOCUS_SUMMARY_LIMIT].rstrip() + "…"
    return focus


def _stringify(value: Any) -> str | None:
    """`None`-preserving `str()` -- a `date`/`datetime` gets `.isoformat()`
    for a stable, JSON-safe representation instead of the type-dependent
    `str()` spelling."""
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def _resolve_active_match(
    related_id: str, picked_dir: Path, queue_dir: Path
) -> dict[str, Any] | None:
    """Resolve `related_id` against `picked_dir` then `queue_dir`, using
    `work_queue.resolve()`'s own resolution rules. Returns an active-match
    dict only when `related_id` resolves to exactly one file in `picked_dir`
    whose frontmatter `status:` is `picked`; `None` otherwise -- including a
    zero or ambiguous resolution in either directory, or a single match in
    `picked_dir` that has already moved past `picked` (e.g. `done`)."""
    picked_result = _wq_resolve(related_id, picked_dir)
    if picked_result.get("status") != "match":
        # Not (uniquely) claimed. Still resolved against queue_dir, per the
        # documented picked_dir-then-queue_dir order, so a still-queued
        # (never claimed) id is distinguishable from one that doesn't
        # resolve anywhere -- neither is an active match.
        _wq_resolve(related_id, queue_dir)
        return None

    candidate = Path(picked_result["candidates"][0])
    candidate_fm = read_frontmatter(candidate)
    if candidate_fm.get("status") != "picked":
        return None

    return {
        "id": related_id,
        "path": str(candidate),
        "claimed-by": candidate_fm.get("claimed-by"),
        # PyYAML auto-parses an unquoted ISO-8601 `claimed-at:` value into a
        # `datetime`, which `json.dumps` can't serialize -- stringified here
        # so this dict stays directly JSON-safe for the `--json` CLI path.
        "claimed-at": _stringify(candidate_fm.get("claimed-at")),
        "repo": candidate_fm.get("repo"),
        "focus": _focus_summary(candidate, candidate_fm),
    }


def _find_run_record(related_id: str, repo: Any, runs_dir: Path) -> str | None:
    """Scan `runs_dir/<repo-name>/*.yaml` for a run record whose raw content
    references `related_id`, returning its path as a string, or `None` if
    `repo` is missing/unusable, the directory doesn't exist, or nothing
    matches. One unreadable record is skipped, not fatal to the scan."""
    if not repo:
        return None
    repo_dir = runs_dir / Path(str(repo)).name
    if not repo_dir.is_dir():
        return None
    for path in sorted(repo_dir.glob("*.yaml")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if related_id in content:
            return str(path)
    return None


def _enrich_with_run_record(
    entry: dict[str, Any], agent_label: str, runs_dir: Path
) -> None:
    """Best-effort: when `entry`'s `claimed-by` is this machine's own
    `agent_label`, attach a `run_record` path if a local GO run record under
    `runs_dir/<repo-name>/` references the related brief's id. Mutates
    `entry` in place only on success. Wrapped in a broad except so any
    failure here -- an unreadable directory, an unexpected frontmatter shape
    -- never changes whether the match itself is reported, only whether it
    carries this extra, purely informational field.
    """
    try:
        if entry.get("claimed-by") != agent_label:
            return
        run_record_path = _find_run_record(entry["id"], entry.get("repo"), runs_dir)
        if run_record_path:
            entry["run_record"] = run_record_path
    except Exception:  # noqa: BLE001, S110 - enrichment is purely additive, never fatal
        pass


def check(
    claimed_brief_path: Path,
    picked_dir: Path,
    queue_dir: Path,
    agent_label: str | None = None,
    runs_dir: Path | None = None,
    *,
    run_id: str | None = None,
    dispatch_mode: str | None = None,
) -> dict[str, Any]:
    """Do any of `claimed_brief_path`'s `related:` ids name a brief that is
    actively claimed (in `picked_dir`, `status: picked`) right now?

    Returns `{"checked": bool, "active": [...], "warning": str|None,
    "pending_decision": envelope|None}`.
    `active` entries carry `id`, `path`, `claimed-by`, `claimed-at`, `repo`,
    `focus` (see `_resolve_active_match`), and -- when the match is claimed
    by this same machine and a local run record can be found -- `run_record`
    (see `_enrich_with_run_record`). When `active` is non-empty,
    `pending_decision` carries `build_pending_decision()`'s provider-neutral
    envelope for the human's coordinate/wait/proceed call. Never raises;
    `checked` is `false` only when the claimed brief itself can't be read or
    parsed -- an individual related id failing to resolve is skipped, not a
    reason to report `checked: false` for the whole call.

    `agent_label` defaults to this machine's own `work_queue._agent_label()`
    value, so a match claimed by *this* caller (as opposed to some other
    agent or machine) is recognized without the caller having to compute
    that label itself. `runs_dir` defaults to `worktrail_home()/runs`
    (`run_record.py`'s own default), joined per-match with the matched
    brief's `repo:` field to scan `runs_dir/<repo-name>/*.yaml`.
    """
    result: dict[str, Any] = {
        "checked": False,
        "active": [],
        "warning": None,
        "pending_decision": None,
    }

    claimed_brief_path = Path(claimed_brief_path)
    # `read_frontmatter` itself swallows OSError and returns `{}`, which
    # would make "file unreadable" and "file readable with genuinely empty
    # frontmatter" indistinguishable here -- so readability is checked
    # separately (stat only, no content read) purely to preserve the
    # `checked: false` fail-open signal; the actual frontmatter parse still
    # goes through `read_frontmatter`, not a hand-rolled read+split.
    try:
        claimed_brief_path.stat()
    except OSError as exc:
        result["warning"] = (
            f"could not read claimed brief {claimed_brief_path}: {exc!r}"
        )
        return result

    frontmatter = read_frontmatter(claimed_brief_path)
    result["checked"] = True

    related = frontmatter.get("related")
    if not related:
        return result
    if isinstance(related, str):
        related = [related]
    if not isinstance(related, list):
        result["warning"] = f"related: field is not a list ({type(related).__name__})"
        return result

    picked_dir = Path(picked_dir)
    queue_dir = Path(queue_dir)
    try:
        resolved_agent_label = agent_label or _wq_agent_label()
    except Exception:  # noqa: BLE001 - enrichment gating only, never fatal
        resolved_agent_label = agent_label
    resolved_runs_dir = (
        Path(runs_dir).expanduser()
        if runs_dir is not None
        else worktrail_home() / "runs"
    )
    active: list[dict[str, Any]] = []
    warnings: list[str] = []

    for raw_id in related:
        related_id = str(raw_id).strip()
        if not related_id:
            continue
        try:
            entry = _resolve_active_match(related_id, picked_dir, queue_dir)
        except Exception as exc:  # noqa: BLE001 - skip this id, never abort the check
            warnings.append(f"could not resolve related id {related_id!r}: {exc!r}")
            continue
        if entry is not None:
            if resolved_agent_label:
                _enrich_with_run_record(entry, resolved_agent_label, resolved_runs_dir)
            active.append(entry)

    result["active"] = active
    if warnings:
        result["warning"] = "; ".join(warnings)
    if active:
        result["pending_decision"] = build_pending_decision(
            _claimed_subject(frontmatter, claimed_brief_path),
            _claimed_repo(frontmatter, active),
            run_id=run_id,
            dispatch_mode=dispatch_mode,
        )
    return result


def _format_human(res: dict[str, Any]) -> str:
    if not res["checked"]:
        return f"unknown: {res.get('warning') or 'related-brief claims could not be checked'}"

    active: list[dict[str, Any]] = (
        list(res["active"]) if isinstance(res["active"], list) else []
    )
    if not active:
        line = "no collision: none of this brief's related ids are actively claimed"
        if res.get("warning"):
            line += f"\n  warning: {res['warning']}"
        return line

    lines = [f"COLLISION: {len(active)} related brief(s) actively claimed"]
    for m in active:
        lines.append(
            f"  {m['id']}  claimed-by={m.get('claimed-by')}  claimed-at={m.get('claimed-at')}"
            f"  repo={m.get('repo')}"
        )
        if m.get("focus"):
            lines.append(f"    focus: {m['focus']}")
        if m.get("run_record"):
            lines.append(f"    run_record: {m['run_record']}")
    lines.append(
        "  -> surface these to the operator; never block dispatch on this signal alone"
    )
    decision = res.get("pending_decision")
    if decision:
        lines.append(
            f"  -> pending decision {decision['decision_id']}: "
            "surface this envelope to the operator"
        )
    if res.get("warning"):
        lines.append(f"  warning: {res['warning']}")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brief", required=True, help="path to the claimed brief .md file")
    p.add_argument(
        "--picked-dir",
        default=None,
        help="work-queue picked directory (default: work_queue.base_dir()/picked)",
    )
    p.add_argument(
        "--queue-dir",
        default=None,
        help="work-queue directory (default: work_queue.base_dir()/queue)",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    picked_dir = Path(args.picked_dir) if args.picked_dir else _wq.picked_dir()
    queue_dir = Path(args.queue_dir) if args.queue_dir else _wq.queue_dir()

    res = check(Path(args.brief), picked_dir, queue_dir)

    if args.json:
        print(json.dumps(res))
    else:
        print(_format_human(res))

    # Always 0: this is a signal source for a human decision, not a gate. A
    # non-zero exit here would turn "could not determine" into a dispatch
    # failure, which is precisely the fail-open contract this module exists
    # to honor.
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
