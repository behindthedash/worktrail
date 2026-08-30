#!/usr/bin/env python3
"""Stop-hook durable-artifact dedup gate — transcript-local evidence only.

Answers one question for the Claude Code Stop hook (`suggest_next_step.py`,
Layer 2 of the durable-artifact dedup gate): did THIS session already track
its follow-up work in a durable artifact, so that auto-capturing a new
handoff brief would duplicate it? Three kinds of hits, every one harvested
from the session transcript itself — never from a directory-wide scan or a
remote query:

1. `session_touched_durable_artifact` — a touched path under `docs/specs/**`
   or `openspec/changes/**` (exactly the trees the hook's extended
   `scan_transcript` collects). Creating or editing a spec/change this
   session IS durable tracking of whatever the follow-up idea would say.
2. `planned_run_record` — a run-record YAML whose path appeared verbatim in
   the transcript (the hook's `RUN_RECORD_PATH_RE` harvest) and whose
   `final_status` is `planned_ready_for_implementation`: the run ended by
   producing a planned change, i.e. the durable artifact already exists.
   Read through `run_record._load_lenient`, so one malformed or unreadable
   record is skipped, never raised — the same tolerance policy as every
   other run-record read in this package.
3. `merged_docs_only_spec_pr` — the transcript shows a merge marker
   (`gh pr merge` / `git merge`) AND spec-tree paths were touched this
   session: a docs-only spec PR was merged here, so the follow-up is
   already tracked by the merged change.

Every input arrives as a repeated CLI flag fed by the hook out of its
single transcript pass — `--touched-path`, `--run-record`, and
`--bash-command` — so this checker never re-reads the transcript file and
never shells out.

Fail-open contract (Requirement: Fail-Open And Headless-Excluded): missing,
unreadable, malformed, or non-UTF-8 inputs degrade to zero hits, output
stays valid JSON, and `main()` always exits 0. A broken checker must never
block a capture suggestion; it only fails to trigger the downgrade-to-
suggestion behavior the hook applies on hits.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .run_record import _load_lenient

PLANNED_STATUS = "planned_ready_for_implementation"

# Segment pairs naming the two durable spec trees. Matched as a segment
# window anywhere in the path — not a string `startswith` — so the same
# path classifies identically whether the transcript carried it absolute
# (`/repo/docs/specs/x/spec.md`), repo-relative (`docs/specs/x/spec.md`),
# or `~`-expanded.
DURABLE_ARTIFACT_PATH_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("docs", "specs"),
    ("openspec", "changes"),
)

# Transcript Bash-command substrings treated as "this session merged".
# Deliberately narrow: `gh pr merge` already covers --merge/--squash/--rebase,
# and a false positive only downgrades capture to a suggestion, never blocks.
MERGE_MARKERS: tuple[str, ...] = ("gh pr merge", "git merge")

SESSION_TOUCHED_DURABLE_ARTIFACT = "session_touched_durable_artifact"
PLANNED_RUN_RECORD = "planned_run_record"
MERGED_DOCS_ONLY_SPEC_PR = "merged_docs_only_spec_pr"


def _segments(raw: str | Path) -> list[str]:
    """Lowercased, non-empty path segments of `raw`, separators normalized."""
    text = str(raw).replace("\\", "/").strip()
    return [segment.lower() for segment in text.split("/") if segment not in ("", ".")]


def is_durable_artifact_path(raw: str | Path) -> bool:
    """Is `raw` a path with content under one of the durable spec trees?

    The sliding-window bound doubles as the content check: a path ending
    exactly at a tree root (`docs/specs`, `openspec/changes`) leaves no room
    for a full window plus a trailing segment, so touching the bare tree
    directory itself never counts as touching a durable artifact.
    """
    segments = _segments(raw)
    for prefix in DURABLE_ARTIFACT_PATH_PREFIXES:
        plen = len(prefix)
        for i in range(len(segments) - plen):
            if tuple(segments[i : i + plen]) == prefix:
                return True
    return False


def touched_durable_artifacts(touched_paths: Iterable[str | Path]) -> list[str]:
    """Unique durable-spec-tree paths among `touched_paths`, input order kept."""
    artifacts: list[str] = []
    seen: set[str] = set()
    for raw in touched_paths:
        if not is_durable_artifact_path(raw):
            continue
        path = str(Path(os.path.expanduser(str(raw))))
        if path not in seen:
            seen.add(path)
            artifacts.append(path)
    return artifacts


def find_planned_run_records(
    run_record_paths: Iterable[str | Path],
) -> list[dict[str, str]]:
    """Run records that finished `planned_ready_for_implementation`.

    Each path goes through `run_record._load_lenient`, which converts its own
    `RunRecordFormatError` to `(None, warning)`; a missing, unreadable, or
    non-UTF-8 file raises past it and is caught here — all equally fail-open,
    mirroring `check_deferred_work_handoff.load_deferred_work_entries`.
    Duplicate paths are read once.
    """
    planned: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_path in run_record_paths:
        path = Path(os.path.expanduser(str(raw_path)))
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            record, _warning = _load_lenient(path)
        except (OSError, UnicodeDecodeError):
            continue
        if record is None:
            continue
        if record.get("final_status") == PLANNED_STATUS:
            planned.append({"run_record": key, "final_status": PLANNED_STATUS})
    return planned


def merge_markers_in(bash_commands: Iterable[str]) -> list[str]:
    """Merge markers (`MERGE_MARKERS`) appearing in any given command line."""
    markers: list[str] = []
    for command in bash_commands:
        lowered = str(command).lower()
        for marker in MERGE_MARKERS:
            if marker in lowered and marker not in markers:
                markers.append(marker)
    return markers


def find_hits(
    touched_paths: Iterable[str | Path],
    run_record_paths: Iterable[str | Path],
    bash_commands: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """All dedup hits, kinds ordered: touched artifacts, planned records, merged PR."""
    hits: list[dict[str, Any]] = []
    spec_paths = touched_durable_artifacts(touched_paths)
    for path in spec_paths:
        hits.append({"kind": SESSION_TOUCHED_DURABLE_ARTIFACT, "path": path})
    for entry in find_planned_run_records(run_record_paths):
        hits.append({"kind": PLANNED_RUN_RECORD, **entry})
    markers = merge_markers_in(bash_commands)
    if markers and spec_paths:
        hits.append(
            {
                "kind": MERGED_DOCS_ONLY_SPEC_PR,
                "spec_paths": spec_paths,
                "merge_markers": markers,
            }
        )
    return hits


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--touched-path",
        dest="touched_path",
        action="append",
        default=[],
        metavar="PATH",
        help="session-touched file path from the hook's transcript scan; may be repeated",
    )
    parser.add_argument(
        "--run-record",
        dest="run_record",
        action="append",
        default=[],
        metavar="PATH",
        help="run-record YAML path mentioned in the transcript; may be repeated",
    )
    parser.add_argument(
        "--bash-command",
        dest="bash_command",
        action="append",
        default=[],
        metavar="TEXT",
        help="Bash command line from the hook's transcript scan; may be repeated",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    hits = find_hits(args.touched_path, args.run_record, args.bash_command)

    if args.json:
        print(json.dumps({"hits": hits}))
    elif hits:
        for hit in hits:
            kind = hit["kind"]
            if kind == SESSION_TOUCHED_DURABLE_ARTIFACT:
                print(f"durable artifact touched this session: {hit['path']}")
            elif kind == PLANNED_RUN_RECORD:
                print(f"run record finished {hit['final_status']}: {hit['run_record']}")
            else:
                print(
                    f"merged docs-only spec PR "
                    f"(markers: {', '.join(hit['merge_markers'])}): "
                    f"{', '.join(hit['spec_paths'])}"
                )
    else:
        print("No durable-artifact dedup hits.")

    # Always 0: fail-open signal source, never a dispatch gate -- see
    # Requirement: Fail-Open And Headless-Excluded.
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
