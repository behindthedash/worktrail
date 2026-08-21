#!/usr/bin/env python3
"""Stop-hook deferred-work handoff guard.

Reads one or more `worktrail-run-record` YAML paths (the same `.yaml` files
`run_record.py` writes) and looks only at each record's `deferred_work`
list — the list `worktrail-run-record append PATH deferred_work "..."`
appends to, distinct from `scope_review`, which records completed/excluded
scope, not work an agent chose to defer. A `scope_review` entry such as
`out-of-scope | <item> | different purpose: ...` can carry the same
deferral-flavored vocabulary as a genuine `deferred_work` entry, but it
answers a different question (why something was excluded from *this* run's
scope, already reviewed by `pre_pr_gate.py`'s scope-completeness gate) and
must never be read here — see Requirement: Deferred-Work-Only Signal Source.

Parsing reuses `run_record._load_lenient`, the same lenient reader
`active-conflicts`/`find-by-worktree` use for a directory-wide scan: one
malformed or unreadable run record is skipped, never raised, so a bad file
never takes the whole check down.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Union

from .run_record import _load_lenient


def load_deferred_work_entries(run_record_paths: Iterable[Union[str, Path]]) -> List[Dict[str, str]]:
    """Read only the `deferred_work` list off each run record in `run_record_paths`.

    Returns a flat list of `{"text": str, "run_record": str}`, one per
    non-empty `deferred_work` entry, in the order the paths and their
    entries were given. `scope_review` is never read. A path that does not
    exist, cannot be read, or fails `run_record._load_lenient`'s format
    check is skipped, not raised — the same fail-open posture as every
    other run-record directory scan in this package.
    """
    entries: List[Dict[str, str]] = []
    for raw_path in run_record_paths:
        path = Path(raw_path)
        try:
            record, _warning = _load_lenient(path)
        except OSError:
            # Missing/unreadable file -- `_load_lenient` only catches its own
            # `RunRecordFormatError`, not an absent path or a permissions
            # failure; both are just as fail-open here.
            continue
        if record is None:
            continue
        deferred_work: Any = record.get("deferred_work") or []
        if not isinstance(deferred_work, list):
            continue
        for item in deferred_work:
            if isinstance(item, str) and item.strip():
                entries.append({"text": item, "run_record": str(path)})
    return entries
