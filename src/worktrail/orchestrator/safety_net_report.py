#!/usr/bin/env python3
"""Aggregate observability report for the orchestrator's "safety net" recovery
paths -- code that recovers instead of failing hard (see live.py's
`_require_dependency_files` WARN downgrade and verify.py's `auto_merge`
preflight-query-error fallback).

Both paths append a structured event to the run journal they already fire
from (`progress.append_safety_net_events` / `entries`' `dependency_file_drift`
markers -- see `journal_path_for`), so a single run's transcript already shows
"took the safety net" vs. "used the normal path". This script scans EVERY run
journal for a repo and aggregates fire counts across runs, so a review can ask
"how often does declared-vs-actual file drift recur, and on which
tasks/specs" or "is the preflight fallback absorbing a one-off blip or
masking a persistently flaky `gh api` endpoint" without grepping raw logs.

Mirrors agent_capacity.py's small-CLI-over-a-JSON-store shape (status
subcommand, `--json`, sanitized output) -- this store is just journals already
on disk rather than a dedicated cache.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator


def journal_paths(repo: Path) -> Iterator[Path]:
    """Every run journal for *repo*, across all specs -- same location
    `live.journal_path_for` writes to (`<repo>-worktrees/run-*.json`)."""
    wt_base = repo.resolve().parent / f"{repo.resolve().name}-worktrees"
    if not wt_base.is_dir():
        return
    yield from sorted(wt_base.glob("run-*.json"))


def _load(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def events_in_journal(journal: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Every safety-net event in one journal: `dependency_file_drift` markers
    live inline in `entries` (see live.py's `reconcile_from_journal`);
    group-level events (e.g. `automerge_preflight_fallback`) live in the
    dedicated `safety_net_events` list (`progress.append_safety_net_events`)."""
    for entry in journal.get("entries", []) or []:
        if entry.get("event"):
            yield entry
    yield from journal.get("safety_net_events", []) or []


def scan(repo: Path) -> Dict[str, Any]:
    """Aggregate every safety-net event across all of *repo*'s run journals.

    Returns a JSON-safe summary: total fire count per event type, and a
    breakdown that differs by event shape -- `dependency_file_drift` by
    `dep_id` (which dependency's frontmatter keeps drifting), and
    `automerge_preflight_fallback` by `outcome` (queued vs. fallback_failed,
    i.e. is the fallback itself reliable).
    """
    by_event: Counter[str] = Counter()
    drift_by_dep: Counter[str] = Counter()
    preflight_by_outcome: Counter[str] = Counter()
    worktree_drift_repaired_by_task: Counter[str] = Counter()
    checklist_conflict_resolved_by_task: Counter[str] = Counter()
    runs_scanned = 0
    runs_with_events = 0

    for path in journal_paths(repo):
        journal = _load(path)
        if not journal:
            continue
        runs_scanned += 1
        fired_here = False
        for event in events_in_journal(journal):
            kind = event.get("event", "unknown")
            by_event[kind] += 1
            fired_here = True
            if kind == "dependency_file_drift":
                drift_by_dep[event.get("dep_id", "unknown")] += 1
            elif kind == "automerge_preflight_fallback":
                preflight_by_outcome[event.get("outcome", "unknown")] += 1
            elif kind == "worktree_drift_repaired":
                worktree_drift_repaired_by_task[event.get("task", "unknown")] += 1
            elif kind == "checklist_conflict_resolved":
                checklist_conflict_resolved_by_task[event.get("task", "unknown")] += 1
        if fired_here:
            runs_with_events += 1

    return {
        "runs_scanned": runs_scanned,
        "runs_with_safety_net_events": runs_with_events,
        "by_event": dict(by_event),
        "dependency_file_drift_by_dep_id": dict(drift_by_dep),
        "automerge_preflight_fallback_by_outcome": dict(preflight_by_outcome),
        "worktree_drift_repaired_by_task": dict(worktree_drift_repaired_by_task),
        "checklist_conflict_resolved_by_task": dict(checklist_conflict_resolved_by_task),
    }


def render(summary: Dict[str, Any], repo: Path) -> str:
    lines = [f"safety-net report: {repo}", f"runs scanned: {summary['runs_scanned']}"]
    if not summary["by_event"]:
        lines.append("no safety-net events recorded in any scanned run journal")
        return "\n".join(lines)
    lines.append(f"runs with at least one event: {summary['runs_with_safety_net_events']}")
    lines.append("")
    lines.append("fires by event type:")
    for kind, count in sorted(summary["by_event"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {kind}: {count}")
    if summary["dependency_file_drift_by_dep_id"]:
        lines.append("")
        lines.append("dependency_file_drift by dep_id (frontmatter that keeps drifting):")
        for dep_id, count in sorted(
            summary["dependency_file_drift_by_dep_id"].items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {dep_id}: {count}")
    if summary["automerge_preflight_fallback_by_outcome"]:
        lines.append("")
        lines.append("automerge_preflight_fallback by outcome:")
        for outcome, count in sorted(
            summary["automerge_preflight_fallback_by_outcome"].items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {outcome}: {count}")
    if summary["worktree_drift_repaired_by_task"]:
        lines.append("")
        lines.append("worktree_drift_repaired by task:")
        for task, count in sorted(
            summary["worktree_drift_repaired_by_task"].items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {task}: {count}")
    if summary["checklist_conflict_resolved_by_task"]:
        lines.append("")
        lines.append("checklist_conflict_resolved by task:")
        for task, count in sorted(
            summary["checklist_conflict_resolved_by_task"].items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {task}: {count}")
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="orchestrator safety-net observability report")
    parser.add_argument("--repo", type=str, required=True, help="repo root (not the -worktrees dir)")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a summary")
    args = parser.parse_args(list(argv) if argv is not None else None)

    repo = Path(args.repo).expanduser()
    summary = scan(repo)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(render(summary, repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
