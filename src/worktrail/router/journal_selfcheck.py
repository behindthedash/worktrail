#!/usr/bin/env python3
"""journal_selfcheck.py — run-journal invariant detector (stranded runs).

The orchestrator's run journal (`<repo>-worktrees/run-<spec_id>.json`) is a
state machine artifact. These invariant violations have each caused real
production incidents that were only caught by an operator noticing manually:

- **stranded-tail** — `integrate_complete: true` with `pending_tail_tasks`
  still non-empty and no live run holding the journal's RunLock. The impl
  groups merged but the held-out e2e/cleanup tail never dispatched (the
  PR #235/#238 bug class). Test-time coverage now exists for that class;
  this is the production-time safety net for it and for whatever state bug
  comes next.
- **unreconciled-tail-evidence** — `unreconciled_tail_evidence` non-empty: a
  tail-kind (e2e/cleanup) task DID dispatch and reached DONE, but its own
  worktree branch still carries commits that never merged onto base (a
  checkbox flip, an evidence file) -- the sibling bug class to stranded-tail,
  where the run instead reports full success while real evidence sits on a
  branch a later worktree-cleanup pass deletes with zero trace (reproduced
  2026-08-12). See `integrate.detect_unreconciled_tail_evidence`.
- **malformed-journal** — the journal no longer parses as JSON. State files
  are written only by the orchestrator's atomic writer, so a parse failure
  means something else rewrote it (observed 2026-08-08: a worker hand-edited
  a state file with a generic YAML writer). Skipping it silently would make
  every downstream reader disagree about run state.
- **malformed-run-record** — a `~/.go/runs/<repo>/*.yaml` run record (a
  different artifact from the journal above) fails run_record.py's own
  parser for the same reason: a hand-edit or generic-YAML rewrite. Unlike
  the journal case, `run_record.py`'s own directory-wide scans (used by the
  mandatory `#active-conflicts-scan` claim gate) already skip these and keep
  going rather than aborting -- this finding exists purely so the dashboard
  surfaces the degraded file instead of an operator only discovering it via
  a scan's `warnings` field days later.

Passive detector, not a gate: it flags signals for a human/agent to judge,
matching `quarantine_selfcheck.py`'s posture (which owns QUARANTINED-group
triage; this module deliberately does not re-report those). No network calls.

Usage:
  journal_selfcheck.py --repo /path/to/repo [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .run_record import _load_lenient as _load_run_record_lenient

_DEFAULT_RUN_RECORD_DIR = Path.home() / ".go" / "runs"


def _runlock_held(lock_path: Path) -> bool:
    """True if a live process holds an exclusive flock on lock_path.

    Mirrors dashboard._runlock_held (module-private there): the orchestrator's
    RunLock holds the flock only for the run's duration and the kernel releases
    it on process death, so held == a live run RIGHT NOW; stale lock files
    probe as free. Non-POSIX (no fcntl) -> False, matching RunLock's own
    no-op degradation.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return False
    try:
        fh = open(lock_path, "a")
    except OSError:
        return False
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        fh.close()


def _malformed_run_record_findings(
    repo: Path, run_record_dir: Optional[Path]
) -> List[Dict[str, Any]]:
    """Findings for `run_record_dir/<repo-name>/*.yaml` files that fail
    run_record.py's own parser. Mirrors `load_recent_runs()`'s dir-resolution
    (env override, then `~/.go/runs`) so both readers agree on where records
    live.
    """
    if run_record_dir is None:
        override = os.environ.get("GO_RUN_RECORD_DIR")
        run_record_dir = Path(override).expanduser() if override else _DEFAULT_RUN_RECORD_DIR
    run_dir = run_record_dir / repo.resolve().name
    if not run_dir.is_dir():
        return []
    findings: List[Dict[str, Any]] = []
    for record_file in sorted(run_dir.glob("*.yaml")):
        _record, warning = _load_run_record_lenient(record_file)
        if warning is None:
            continue
        findings.append({
            "kind": "malformed-run-record",
            "spec_id": record_file.stem,
            "journal": str(record_file),
            "detail": warning,
        })
    return findings


def check_repo(repo: Path, run_record_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Scan `<repo>-worktrees/run-*.json` for stranded-run invariant violations,
    plus `run_record_dir/<repo-name>/*.yaml` for malformed run records.

    Returns {"findings": [{"kind", "spec_id", "journal", "detail"}, ...]}.
    A journal whose RunLock is currently held belongs to a live run and is
    never flagged — "stranded" means nobody is driving it.
    """
    repo = Path(repo)
    findings: List[Dict[str, Any]] = list(_malformed_run_record_findings(repo, run_record_dir))
    wt_base = repo.parent / f"{repo.name}-worktrees"
    if not wt_base.is_dir():
        return {"findings": findings}
    for journal_file in sorted(wt_base.glob("run-*.json")):
        spec_id = journal_file.stem[len("run-"):]
        if _runlock_held(journal_file.with_suffix(".lock")):
            continue  # live run — not stranded by definition
        try:
            journal = json.loads(journal_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(
                {
                    "kind": "malformed-journal",
                    "spec_id": spec_id,
                    "journal": str(journal_file),
                    "detail": f"journal does not parse: {exc}",
                }
            )
            continue
        if not isinstance(journal, dict):
            findings.append(
                {
                    "kind": "malformed-journal",
                    "spec_id": spec_id,
                    "journal": str(journal_file),
                    "detail": f"journal root is {type(journal).__name__}, expected object",
                }
            )
            continue
        pending_tail = journal.get("pending_tail_tasks")
        if journal.get("integrate_complete") and isinstance(pending_tail, list) and pending_tail:
            findings.append(
                {
                    "kind": "stranded-tail",
                    "spec_id": spec_id,
                    "journal": str(journal_file),
                    "detail": (
                        f"integrate_complete with undispatched tail task(s) "
                        f"{', '.join(str(t) for t in pending_tail)} and no live run — "
                        "resume the run to dispatch them"
                    ),
                }
            )
        unreconciled = journal.get("unreconciled_tail_evidence")
        if isinstance(unreconciled, list) and unreconciled:
            manual_triage: List[str] = []
            reconciling: List[str] = []
            for u in unreconciled:
                if isinstance(u, dict):
                    task_id = str(u.get("task", u))
                    reconcile_state = u.get("reconcile_state")
                    pr_url = u.get("reconcile_pr_url") or ""
                else:
                    task_id, reconcile_state, pr_url = str(u), None, ""
                if reconcile_state == "merged":
                    continue  # belt-and-suspenders: detect_ already stops reporting these
                if reconcile_state in ("opened", "already-open"):
                    reconciling.append(f"{task_id} ({pr_url})" if pr_url else task_id)
                else:  # "quarantined", or missing/absent for pre-reconciliation journals
                    manual_triage.append(task_id)
            detail_parts = []
            if manual_triage:
                detail_parts.append(
                    f"tail task(s) {', '.join(manual_triage)} completed but their own "
                    "commits never merged onto base — reconcile before the "
                    "worktree is cleaned up"
                )
            if reconciling:
                detail_parts.append(
                    f"tail task(s) {', '.join(reconciling)} auto-reconciliation "
                    "PR(s) open, awaiting merge"
                )
            if detail_parts:
                findings.append(
                    {
                        "kind": "unreconciled-tail-evidence",
                        "spec_id": spec_id,
                        "journal": str(journal_file),
                        "detail": "; ".join(detail_parts),
                    }
                )
    return {"findings": findings}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="run-journal invariant detector")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-record-dir", default=None,
                         help="defaults to $GO_RUN_RECORD_DIR or ~/.go/runs")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    run_record_dir = Path(args.run_record_dir).expanduser() if args.run_record_dir else None
    result = check_repo(Path(args.repo), run_record_dir=run_record_dir)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for f in result["findings"]:
            print(f"{f['kind']}: {f['spec_id']} — {f['detail']}")
        if not result["findings"]:
            print("clean: no stranded-run findings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
