"""resume_group.py — return QUARANTINED orchestrator groups to the resume path.

`integrate.py` marks a group `QUARANTINED` in the run journal
(`<repo>-worktrees/run-<spec_id>.json`) when it cannot be integrated safely.
A resume skips a group that already has a record, so a quarantined group stays
quarantined forever unless its record is removed. Clearing it by hand means
editing JSON and remembering to drop `integrate_complete` too.

This command does exactly that, and records what it cleared under
`resumed_quarantines` so the prior reason/detail/PR is history, not a deletion.

Usage:
  worktrail-resume-group --repo /path/to/repo --spec openspec/changes/<id> --group g1
  worktrail-resume-group --repo /path/to/repo --spec <spec> --all-resumable
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from worktrail.orchestrator import live, progress
from worktrail.orchestrator.integrate import QUARANTINE_BUDGET_EXHAUSTED
from worktrail.router.journal_selfcheck import _runlock_held


def select_groups(
    journal: dict[str, Any], names: list[str], all_resumable: bool
) -> tuple[list[str], list[str]]:
    """Resolve the group names to clear, plus the reasons this cannot proceed.

    A *named* group that has no journal record, or whose `state` is not
    `QUARANTINED`, is a problem entry -- never a silent skip, because silently
    clearing nothing while reporting success is how a typo'd group name turns
    into "the resume didn't do anything and nobody knows why".

    `all_resumable` adds only records that are `QUARANTINED` *and* whose
    `quarantine_reason` is `budget_exhausted`: those failed on a spend cap, not
    on anything about the change, so re-running them is safe without a human
    first fixing the branch.
    """
    groups = journal.get("groups")
    if not isinstance(groups, dict):
        groups = {}
    selected: list[str] = []
    problems: list[str] = []
    for name in names:
        record = groups.get(name)
        if not isinstance(record, dict):
            problems.append(f"group {name!r}: no record in journal")
            continue
        state = record.get("state")
        if state != "QUARANTINED":
            problems.append(f"group {name!r}: state is {state!r}, not QUARANTINED")
            continue
        if name not in selected:
            selected.append(name)
    if all_resumable:
        for name, record in groups.items():
            if not isinstance(record, dict):
                continue
            if record.get("state") != "QUARANTINED":
                continue
            if record.get("quarantine_reason") != QUARANTINE_BUDGET_EXHAUSTED:
                continue
            if name not in selected:
                selected.append(name)
    return selected, problems


def clear_groups(journal: dict[str, Any], selected: list[str]) -> dict[str, Any]:
    """Pop each selected group record, drop `integrate_complete`, and append one
    `resumed_quarantines` entry per cleared group. Records that were not
    selected are left untouched (byte-identical on re-serialization)."""
    groups = journal.get("groups")
    if not isinstance(groups, dict):
        groups = {}
    history = journal.get("resumed_quarantines")
    if not isinstance(history, list):
        history = []
    cleared_at = datetime.now(timezone.utc).isoformat()
    for name in selected:
        record = groups.pop(name, None)
        if not isinstance(record, dict):
            record = {}
        history.append(
            {
                "group": name,
                "quarantine_reason": record.get("quarantine_reason", ""),
                "quarantine_detail": record.get("quarantine_detail", ""),
                "pr_url": record.get("pr_url", ""),
                "cleared_at": cleared_at,
            }
        )
    journal["resumed_quarantines"] = history
    journal.pop("integrate_complete", None)
    return journal


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True, help="repo whose run journal to edit")
    p.add_argument("--spec", required=True, help="spec path/id keying the journal")
    p.add_argument(
        "--group", action="append", default=[], help="group name to clear (repeatable)"
    )
    p.add_argument(
        "--all-resumable",
        action="store_true",
        help=f"clear every QUARANTINED group whose reason is {QUARANTINE_BUDGET_EXHAUSTED}",
    )
    p.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    journal_path = live.journal_path_for(repo, args.spec)

    def fail(message: str) -> int:
        if args.json:
            print(json.dumps({"ok": False, "error": message}, indent=2))
        else:
            print(f"ERROR: {message}")
        return 1

    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return fail(f"cannot read run journal {journal_path}: {exc}")
    if not isinstance(journal, dict):
        return fail(f"run journal {journal_path} is not a JSON object")

    # A live run owns the journal and rewrites it wholesale from memory; editing
    # it underneath would be silently discarded at best.
    if _runlock_held(journal_path.with_suffix(".lock")):
        return fail(f"a live run holds the run lock for {journal_path}; not editing")

    selected, problems = select_groups(journal, args.group, args.all_resumable)
    if problems:
        return fail("; ".join(problems))
    if not selected:
        if args.json:
            print(
                json.dumps(
                    {"ok": True, "cleared": [], "dry_run": args.dry_run}, indent=2
                )
            )
        else:
            print("nothing to clear")
        return 0

    clear_groups(journal, selected)
    if not args.dry_run:
        progress.atomic_write_text(
            journal_path, json.dumps(journal, indent=2, sort_keys=True) + "\n"
        )

    next_cmd = f"worktrail-live full-real --repo {repo} --spec {args.spec} --resume"
    if args.json:
        print(
            json.dumps(
                {
                    "ok": True,
                    "cleared": selected,
                    "dry_run": args.dry_run,
                    "next": next_cmd,
                },
                indent=2,
            )
        )
    else:
        prefix = "would clear" if args.dry_run else "cleared"
        print(f"{prefix}: {', '.join(selected)}")
        print(f"next: {next_cmd}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
