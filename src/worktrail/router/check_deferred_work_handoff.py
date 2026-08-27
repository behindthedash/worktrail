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

from ..workqueue import work_queue as _wq
from ..workqueue.work_queue import _focus_of as _wq_focus_of
from .brief_probes import extract_probes
from .run_record import _load_lenient

# Deliberately narrow per proposal.md's non-goals: v1 ships a small starting
# list and expects follow-up tuning from observed false positives/negatives,
# not a front-loaded exhaustive vocabulary.
DEFERRAL_PHRASES = (
    "advisory for now",
    "deferred",
    "once calibrated",
    "follow-up",
    "follow up",
    "in a later pr",
)


def matches_deferral_phrase(text: str) -> bool:
    """Case-insensitive substring match of `text` against `DEFERRAL_PHRASES`."""
    lowered = text.lower()
    return any(phrase in lowered for phrase in DEFERRAL_PHRASES)


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
        except (OSError, UnicodeDecodeError):
            # Missing/unreadable file, or non-UTF-8 content -- `_load_lenient`
            # only catches its own `RunRecordFormatError`, not an absent path,
            # a permissions failure, or a decode error; all are just as
            # fail-open here.
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


def _brief_focus_texts(directory: Path) -> List[str]:
    """Best-effort focus text for every `*.md` brief directly under `directory`.

    A missing or unreadable `directory` yields no texts. `_focus_of` degrades
    a malformed-YAML brief to `""` rather than raising, but a non-UTF-8 brief
    raises `UnicodeDecodeError` out of `Path.read_text`, so that call is
    wrapped per-brief too -- one bad file must never drop the rest of the
    scan.
    """
    try:
        paths = sorted(directory.glob("*.md"))
    except OSError:
        return []
    texts: List[str] = []
    for path in paths:
        try:
            focus = _wq_focus_of(path)
        except (OSError, UnicodeDecodeError):
            continue
        if focus:
            texts.append(focus)
    return texts


def has_handoff_coverage(text: str) -> bool:
    """Does an existing `queue/` or `picked/` brief already cover the
    deferred-work entry `text`?

    Extracts `text`'s path/symbol/pull-request Evidence Probes via
    `brief_probes.extract_probes` and matches each, case-
    insensitively, as a substring of some brief's focus text -- per the
    task brief, the same technique for all three probe kinds. Checked
    under `work_queue.queue_dir()` or `work_queue.picked_dir()`. No probes
    extracted, or no briefs found/readable in either directory, means no
    coverage -- never treated as a match.
    """
    probes = extract_probes(text)
    candidates: List[str] = [
        *probes.get("paths", []),
        *probes.get("symbols", []),
        *probes.get("pull_requests", []),
    ]
    if not candidates:
        return False

    focus_texts = _brief_focus_texts(_wq.queue_dir()) + _brief_focus_texts(_wq.picked_dir())
    for focus in focus_texts:
        lowered_focus = focus.lower()
        if any(str(probe).lower() in lowered_focus for probe in candidates):
            return True
    return False


def find_flagged(run_record_paths: Iterable[Union[str, Path]]) -> List[Dict[str, str]]:
    """Deferred-work entries that match a deferral phrase and lack handoff coverage.

    Runs `load_deferred_work_entries` (fail-open per-path already), keeps only
    phrase-matching entries, and drops any already covered by an existing
    `queue/`/`picked/` brief per `has_handoff_coverage`.
    """
    flagged: List[Dict[str, str]] = []
    for entry in load_deferred_work_entries(run_record_paths):
        text = entry["text"]
        if not matches_deferral_phrase(text):
            continue
        if has_handoff_coverage(text):
            continue
        flagged.append(entry)
    return flagged


def main(argv: List[str] | None = None) -> int:
    import argparse
    import json

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--run-record", dest="run_record", action="append", default=[],
        metavar="PATH", help="run-record YAML path; may be repeated",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    flagged = find_flagged(args.run_record)
    result = {"flagged": flagged}

    if args.json:
        print(json.dumps(result))
    else:
        if not flagged:
            print("No deferred-work handoff gaps found.")
        else:
            for item in flagged:
                print(f"{item['run_record']}: {item['text']}")

    # Always 0: fail-open signal source, never a dispatch gate -- see
    # Requirement: Fail-Open And Headless-Excluded.
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
