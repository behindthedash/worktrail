#!/usr/bin/env python3
"""journal_selfcheck.py — run-journal invariant detector (stranded runs).

The orchestrator's run journal (`<repo>-worktrees/run-<spec_id>.json`) is a
state machine artifact. Two invariant violations have each caused real
production incidents that were only caught by an operator noticing manually:

- **stranded-tail** — `integrate_complete: true` with `pending_tail_tasks`
  still non-empty and no live run holding the journal's RunLock. The impl
  groups merged but the held-out e2e/cleanup tail never dispatched (the
  PR #235/#238 bug class). Test-time coverage now exists for that class;
  this is the production-time safety net for it and for whatever state bug
  comes next.
- **malformed-journal** — the journal no longer parses as JSON. State files
  are written only by the orchestrator's atomic writer, so a parse failure
  means something else rewrote it (observed 2026-08-08: a worker hand-edited
  a state file with a generic YAML writer). Skipping it silently would make
  every downstream reader disagree about run state.

Passive detector, not a gate: it flags signals for a human/agent to judge,
matching `quarantine_selfcheck.py`'s posture (which owns QUARANTINED-group
triage; this module deliberately does not re-report those). No network calls.

Usage:
  journal_selfcheck.py --repo /path/to/repo [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


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


def check_repo(repo: Path) -> Dict[str, Any]:
    """Scan `<repo>-worktrees/run-*.json` for stranded-run invariant violations.

    Returns {"findings": [{"kind", "spec_id", "journal", "detail"}, ...]}.
    A journal whose RunLock is currently held belongs to a live run and is
    never flagged — "stranded" means nobody is driving it.
    """
    repo = Path(repo)
    findings: List[Dict[str, Any]] = []
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
    return {"findings": findings}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="run-journal invariant detector")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = check_repo(Path(args.repo))
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
