#!/usr/bin/env python3
"""release_gate.py — measurable readiness readout for a repo's release freeze.

The v1.0 release gate is defined as N consecutive completed orchestrator/front-
door runs with ZERO manual interventions. Both signals already live in the run
records (`worktrail_home()/runs/<repo-name>/*.yaml`): `final_status` and `interventions`.
This command turns them into a deterministic streak readout so "are we ready
to ship" is a measurement, not a feeling.

Streak semantics (newest-first, consecutive):
- a run with `final_status` in the completed_* states AND an empty
  `interventions` list extends the streak;
- a run with a failed/blocked final_status, or ANY intervention, breaks it;
- a run with no final_status yet (in flight) is skipped, not a break — an
  in-progress run says nothing about the finished-run streak;
- a run record that fails to parse (RunRecordFormatError) BREAKS the streak:
  a corrupted audit artifact is itself an intervention-grade event.

Usage:
  worktrail-release-gate --repo /path/to/repo [--target 5] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .policy import default_run_record_dir, load_policy
from .run_record import RunRecordFormatError, _load

DEFAULT_TARGET = 5

# Successful terminals only: the completed_* trio plus the two non-PR route
# completions. blocked_*/failed_* are real terminals but each one is exactly
# the "run didn't finish clean" signal the streak exists to count against.
CLEAN_STATES = frozenset(
    {
        "completed_and_merged",
        "completed_pr_open",
        "completed_awaiting_human_approval",
        "planned_ready_for_implementation",
        "investigation_complete",
    }
)


def _runs_dir(repo: Path) -> Path:
    policy = load_policy(repo)
    base = Path(
        str(policy.get("run_record_dir") or default_run_record_dir())
    ).expanduser()
    return base / repo.name


def evaluate(repo: Path, target: int = DEFAULT_TARGET) -> dict[str, Any]:
    repo = Path(repo).expanduser().resolve()
    policy = load_policy(repo)
    gate = policy.get("release_gate")
    runs_dir = _runs_dir(repo)
    rows: list[dict[str, Any]] = []
    if runs_dir.is_dir():
        for f in sorted(runs_dir.glob("*.yaml"), reverse=True):  # newest-first
            try:
                rec = _load(f)
            except RunRecordFormatError:
                rows.append(
                    {
                        "run": f.stem,
                        "status": None,
                        "interventions": None,
                        "counts": "break",
                        "reason": "malformed record",
                    }
                )
                continue
            status = rec.get("final_status")
            n_interventions = len(rec.get("interventions") or [])
            if status is None:
                rows.append(
                    {
                        "run": f.stem,
                        "status": None,
                        "interventions": n_interventions,
                        "counts": "skip",
                        "reason": "in flight",
                    }
                )
                continue
            clean = status in CLEAN_STATES and n_interventions == 0
            rows.append(
                {
                    "run": f.stem,
                    "status": status,
                    "interventions": n_interventions,
                    "counts": "clean" if clean else "break",
                    "reason": None
                    if clean
                    else (
                        f"{n_interventions} intervention(s)"
                        if n_interventions
                        else f"final_status {status}"
                    ),
                }
            )

    streak = 0
    for row in rows:
        if row["counts"] == "skip":
            continue
        if row["counts"] == "clean":
            streak += 1
            continue
        break

    return {
        "repo": str(repo),
        "release_gate": gate,
        "target": target,
        "streak": streak,
        "met": streak >= target,
        "runs_considered": len(rows),
        "runs": rows[: max(target * 2, 10)],
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--target", type=int, default=DEFAULT_TARGET)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)
    result = evaluate(Path(args.repo), args.target)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        gate = result["release_gate"] or "(no release_gate set in policy)"
        state = "MET" if result["met"] else "NOT MET"
        print(
            f"release gate {gate}: {state} — streak {result['streak']}/{result['target']} "
            f"clean consecutive runs ({result['runs_considered']} records considered)"
        )
        for row in result["runs"]:
            mark = {"clean": "✓", "break": "✗", "skip": "…"}[row["counts"]]
            reason = f" ({row['reason']})" if row.get("reason") else ""
            print(f"  {mark} {row['run']}: {row['status']}{reason}")
    return 0 if result["met"] else 1


if __name__ == "__main__":
    sys.exit(main())
