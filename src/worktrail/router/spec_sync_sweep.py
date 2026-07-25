#!/usr/bin/env python3
"""spec_sync_sweep.py — recurring, unattended spec-sync drift sweep (REQ-018..022),
now also running an independent checkbox-drift sweep in the same run (REQ-CHG-001).

Composes the sweep's four building blocks into one unattended run:
discover every repo under `--repos-root` that has a `docs/specs/` tree
(`spec_sync_sweep_discovery.discover_repos_with_specs`), check each for
spec-sync drift (`spec_sync_sweep_check.check_repo_drift`), and for a
repo with findings, file exactly one Drift Brief into `--queue-dir`
unless an unresolved one already exists
(`spec_sync_sweep_dedup.find_unresolved_drift_brief`,
`spec_sync_sweep_brief.file_drift_brief`).

For each of the same discovered repos, this run also independently checks
for checkbox-completion drift (`spec_sync_sweep_checkbox_check.check_repo_checkbox_drift`)
and, for a repo with hits and no unresolved checkbox-drift brief already
outstanding, files exactly one Checkbox Drift Brief
(`spec_sync_sweep_dedup.find_unresolved_drift_brief(repo, queue_base,
drift_source="checkbox-drift-sweep")`, `spec_sync_sweep_checkbox_brief.file_checkbox_drift_brief`).
The two checks are fully independent per repo: neither check's failure or
dedup outcome for a repo affects the other check's outcome for that same
repo (REQ-CHG-005).

Single-flight locking (REQ-021, REQ-NR005): before doing anything else,
`run_sweep()` takes a non-blocking exclusive `fcntl.flock()` on
`--lock-file`. If a previous run is still holding it, this run returns
immediately with `skipped_overlap: True` and performs no discovery or
checks — so a scheduler that fires while a run is still in flight skips
the overlapping run instead of running concurrently.

Failure handling deliberately treats two failure classes differently
(Error Scenarios table): a single repo's drift check erroring is
captured per-repo (`check_repo_drift` never raises) and every other repo
in the run is still processed; but the queue write path failing (e.g. an
unwritable `--queue-dir`) is the entire point of the run, so it is
allowed to propagate out of `run_sweep()` and `main()` reports it as a
loud, non-zero run-level failure instead of silently continuing.

This is a plain, directly-invocable stdlib CLI (REQ-020, REQ-NR003) — no
hosted-runner API, no network call, nothing beyond local filesystem
access to `--repos-root` and `--queue-dir`. Wire it into a recurring
schedule with cron, e.g. a weekly run every Monday at 06:00 (this single
scheduled invocation runs both the spec-sync-drift and checkbox-drift
checks; no second crontab entry is introduced):

    0 6 * * 1 /usr/bin/python3 /path/to/spec_sync_sweep.py \\
        --repos-root ~/projects --queue-dir ~/.gitnexus/queue \\
        --lock-file /tmp/spec-sync-sweep.lock

Usage:
  spec_sync_sweep.py --repos-root ~/projects --queue-dir ~/.gitnexus/queue [--lock-file PATH] [--json]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .spec_sync_sweep_discovery import discover_repos_with_specs
from .spec_sync_sweep_check import check_repo_drift
from .spec_sync_sweep_dedup import find_unresolved_drift_brief
from .spec_sync_sweep_brief import file_drift_brief
from .spec_sync_sweep_checkbox_check import check_repo_checkbox_drift
from .spec_sync_sweep_checkbox_brief import file_checkbox_drift_brief

_DEFAULT_LOCK_FILE = str(Path(tempfile.gettempdir()) / "spec-sync-sweep.lock")


def _empty_record(skipped_overlap: bool) -> Dict[str, Any]:
    return {
        "skipped_overlap": skipped_overlap,
        "checked": [],
        "drifted": [],
        "filed": [],
        "skipped_existing": [],
        "failed": [],
        "checkbox_drifted": [],
        "checkbox_filed": [],
        "checkbox_skipped_existing": [],
        "checkbox_failed": [],
    }


def run_sweep(repos_root: Path, queue_base: Path, lock_path: Path) -> Dict[str, Any]:
    """Run one unattended spec-sync drift sweep.

    Acquires a non-blocking exclusive lock on `lock_path` first; if it is
    already held, returns immediately with `skipped_overlap: True` and
    empty lists, without calling discovery or any per-repo check
    (AC-017). Otherwise discovers every repo under `repos_root` with a
    `docs/specs/` tree and, for each one, runs both the spec-sync-drift
    check and the checkbox-drift check as two fully independent
    invocations: a drifted repo with no unresolved Drift Brief already
    outstanding gets exactly one filed, and a repo with checkbox-drift
    hits and no unresolved Checkbox Drift Brief already outstanding gets
    exactly one filed, independently of the other check's outcome for
    that same repo (REQ-CHG-001, REQ-CHG-005). A repo whose drift check
    errors is recorded in `failed` (and its checkbox-drift check still
    runs); a repo whose checkbox-drift check errors is recorded in
    `checkbox_failed` (and its spec-sync-drift check still runs) — either
    way every other repo's checks still run. A queue-write failure
    (`file_drift_brief` or `file_checkbox_drift_brief` raising) is a
    run-level failure and propagates to the caller.
    """
    repos_root = Path(repos_root)
    queue_base = Path(queue_base)
    lock_path = Path(lock_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file = open(lock_path, "a+")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return _empty_record(skipped_overlap=True)

    try:
        record = _empty_record(skipped_overlap=False)
        for repo in discover_repos_with_specs(repos_root):
            repo_str = str(repo)
            record["checked"].append(repo_str)

            result = check_repo_drift(repo)
            if result["error"] is not None:
                record["failed"].append(repo_str)
            elif result["findings"]:
                record["drifted"].append(repo_str)
                existing = find_unresolved_drift_brief(repo, queue_base)
                if existing is not None:
                    record["skipped_existing"].append(repo_str)
                else:
                    file_drift_brief(repo, result["findings"], queue_base)
                    record["filed"].append(repo_str)

            checkbox_result = check_repo_checkbox_drift(repo)
            if checkbox_result["error"] is not None:
                record["checkbox_failed"].append(repo_str)
            elif checkbox_result["findings"]:
                record["checkbox_drifted"].append(repo_str)
                existing_checkbox = find_unresolved_drift_brief(
                    repo, queue_base, drift_source="checkbox-drift-sweep"
                )
                if existing_checkbox is not None:
                    record["checkbox_skipped_existing"].append(repo_str)
                else:
                    file_checkbox_drift_brief(repo, checkbox_result["findings"], queue_base)
                    record["checkbox_filed"].append(repo_str)

        return record
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repos-root", required=True, help="directory containing candidate repos")
    parser.add_argument("--queue-dir", required=True, help="deferred-work queue base directory")
    parser.add_argument("--lock-file", default=_DEFAULT_LOCK_FILE, help="single-flight lock file path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repos_root = Path(args.repos_root).expanduser()
    queue_base = Path(args.queue_dir).expanduser()
    lock_path = Path(args.lock_file).expanduser()

    try:
        record = run_sweep(repos_root, queue_base, lock_path)
    except Exception as exc:  # noqa: BLE001 - queue write failure is a loud run-level failure
        message = f"spec_sync_sweep: run-level failure (queue write path): {exc}"
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(message, file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(record, indent=2))
    elif record["skipped_overlap"]:
        print("spec_sync_sweep: skipped — a previous run is still in progress")
    else:
        print(
            "spec_sync_sweep: "
            f"{len(record['checked'])} checked, {len(record['drifted'])} drifted, "
            f"{len(record['filed'])} filed, {len(record['skipped_existing'])} already outstanding, "
            f"{len(record['failed'])} failed; "
            f"checkbox-drift: {len(record['checkbox_drifted'])} drifted, "
            f"{len(record['checkbox_filed'])} filed, "
            f"{len(record['checkbox_skipped_existing'])} already outstanding, "
            f"{len(record['checkbox_failed'])} failed"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
