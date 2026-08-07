#!/usr/bin/env python3
"""
`/go`'s Phase 5.5 related-brief collision guard.

A brief's `related:` frontmatter list names other briefs describing adjacent
or overlapping work. Nothing before dispatch checks whether one of those
related briefs is *already claimed and in flight* -- a second agent can start
work that collides with a session already underway, discovered only when the
two land conflicting changes. This module answers one question: "of this
brief's `related:` ids, which ones are actively claimed by someone else right
now?" so a human can be asked before dispatch proceeds, mirroring
`check_brief_staleness.py`'s shape: pure extraction, then a bounded,
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
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..shared.brief_frontmatter import read_frontmatter, split_frontmatter
from ..workqueue.work_queue import resolve as _wq_resolve

# A related brief's focus is surfaced for a human to skim, not to read in
# full -- truncated so one runaway focus paragraph doesn't dominate a
# batched prompt covering several related ids.
FOCUS_SUMMARY_LIMIT = 200

_FOCUS_BODY_RE = re.compile(r"^##\s+Focus\s*$\r?\n(.+)$", re.MULTILINE)


def _focus_summary(path: Path, frontmatter: Dict[str, Any]) -> str:
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


def _resolve_active_match(related_id: str, picked_dir: Path, queue_dir: Path) -> Optional[Dict[str, Any]]:
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
        "claimed-at": candidate_fm.get("claimed-at"),
        "repo": candidate_fm.get("repo"),
        "focus": _focus_summary(candidate, candidate_fm),
    }


def check(
    claimed_brief_path: Path,
    picked_dir: Path,
    queue_dir: Path,
    agent_label: Optional[str] = None,
    runs_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Do any of `claimed_brief_path`'s `related:` ids name a brief that is
    actively claimed (in `picked_dir`, `status: picked`) right now?

    Returns `{"checked": bool, "active": [...], "warning": str|None}`.
    `active` entries carry `id`, `path`, `claimed-by`, `claimed-at`, `repo`,
    and `focus` (see `_resolve_active_match`). Never raises; `checked` is
    `false` only when the claimed brief itself can't be read or parsed --
    an individual related id failing to resolve is skipped, not a reason to
    report `checked: false` for the whole call.

    `agent_label` and `runs_dir` are accepted here to keep this the single
    call signature callers use; local run-record enrichment keyed off them
    is layered on separately.
    """
    result: Dict[str, Any] = {"checked": False, "active": [], "warning": None}

    claimed_brief_path = Path(claimed_brief_path)
    try:
        content = claimed_brief_path.read_text(encoding="utf-8")
    except OSError as exc:
        result["warning"] = f"could not read claimed brief {claimed_brief_path}: {exc!r}"
        return result

    frontmatter, _body = split_frontmatter(content)
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
    active: List[Dict[str, Any]] = []
    warnings: List[str] = []

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
            active.append(entry)

    result["active"] = active
    if warnings:
        result["warning"] = "; ".join(warnings)
    return result
