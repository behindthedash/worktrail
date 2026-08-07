#!/usr/bin/env python3
"""quarantine_selfcheck.py — cross-repo QUARANTINED-group detector.

The orchestrator's `integrate.py` marks a group `QUARANTINED` in its run
journal (`<repo>-worktrees/run-<spec_id>.json`) when it cannot be safely
integrated on its own (e.g. it depends on another quarantined group, or its
merge attempt failed) and needs a human to look at it. That journal state was
previously only visible by opening the JSON file directly -- nothing swept
for it. This is a passive detector, not a gate: it flags signals for a
human/agent to judge, matching `policy_selfcheck.py`'s and
`automerge_selfcheck.py`'s own posture.

No network calls (local file inspection only, matching
`check_repo_freshness.py`'s default posture).

Usage:
  quarantine_selfcheck.py --repo /path/to/repo [--json]
  quarantine_selfcheck.py --repos-root ~/projects [--json]   # sweep every repo
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from worktrail.orchestrator.coordinator import plan_groups

from .policy_selfcheck import discover_repo_names


def _group_files(repo: Path, spec_id: str, group_name: str) -> Optional[List[str]]:
    """Recompute a group's file set from the cached RunPlan, not the journal.

    The journal only records the group name a QUARANTINED task landed in at
    integrate time. Recomputing the partition from the cached RunPlan via
    `plan_groups()` cross-checks that grouping is still current -- if the
    RunPlan has moved on and no group named `group_name` exists anymore,
    that's RunPlan/journal drift and this returns `None` rather than a stale
    file list.
    """
    runplans_dir = repo.parent / f"{repo.name}-worktrees" / "runplans"
    matches = list(runplans_dir.glob(f"{spec_id}-*.json"))
    if not matches:
        return None
    newest = max(matches, key=lambda p: p.stat().st_mtime)
    try:
        payload = json.loads(newest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    tasks = payload.get("tasks")
    if not isinstance(tasks, list):
        return None
    by_id = {t["id"]: t for t in tasks if isinstance(t, dict) and "id" in t}
    for group in plan_groups(tasks):
        if group.get("name") != group_name:
            continue
        files: set = set()
        for task_id in group.get("tasks", []):
            task = by_id.get(task_id)
            if task:
                files.update(task.get("files") or [])
        return sorted(files)
    return None


def _files_on_base(repo: Path, files: List[str], base: Optional[str] = None) -> bool:
    """Whether every path in `files` still exists on `base` (or the current branch).

    A group that reconciled to `QUARANTINED` in the run journal may simply
    already be present on the base branch by the time this check runs (e.g. a
    later group's merge subsumed it). `git ls-tree` against `base` is the
    cheapest way to confirm that without a network call.
    """
    if base is None:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return False
        base = result.stdout.strip()
    for path in files:
        result = subprocess.run(
            ["git", "-C", str(repo), "ls-tree", base, "--", path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
    return True


def _iter_journal_files(worktrees_dir: Path):
    for path in sorted(worktrees_dir.glob("run-*.json")):
        if not path.is_file():
            continue
        if path.name.endswith(".status.json"):
            continue
        yield path


def _spec_id_from_journal_path(path: Path) -> str:
    return path.stem[len("run-"):] if path.stem.startswith("run-") else path.stem


def _age_days(path: Path) -> float:
    return max(0.0, (time.time() - path.stat().st_mtime) / 86400.0)


def check_repo(repo: Path) -> Dict[str, Any]:
    """Findings for every run journal in one repo. Empty `findings` = clean."""
    repo = Path(repo)
    result: Dict[str, Any] = {"repo": repo.name, "path": str(repo), "findings": []}
    worktrees_dir = repo.parent / f"{repo.name}-worktrees"
    if not worktrees_dir.is_dir():
        return result
    for journal_path in _iter_journal_files(worktrees_dir):
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(journal, dict):
            continue
        groups = journal.get("groups")
        if not isinstance(groups, dict):
            continue
        spec_id = _spec_id_from_journal_path(journal_path)
        age_days = _age_days(journal_path)
        for group_name, group in groups.items():
            if not isinstance(group, dict):
                continue
            if group.get("state") != "QUARANTINED":
                continue
            result["findings"].append(
                {
                    "spec_id": spec_id,
                    "group": group_name,
                    "pr_url": group.get("pr_url", ""),
                    "age_days": age_days,
                }
            )
    return result


def sweep(repos_root: Path) -> List[Dict[str, Any]]:
    """check_repo() for every repo under `repos_root` that has findings."""
    names = discover_repo_names(repos_root)
    results = []
    for name in names:
        r = check_repo(repos_root / name)
        if r["findings"]:
            results.append(r)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="single repo to check")
    p.add_argument("--repos-root", help="sweep every repo under this directory")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not args.repo and not args.repos_root:
        p.error("one of --repo or --repos-root is required")

    if args.repos_root:
        root = Path(args.repos_root).expanduser()
        if args.repo:
            results = [check_repo(Path(args.repo).expanduser())]
        else:
            results = sweep(root)
    else:
        results = [check_repo(Path(args.repo).expanduser())]

    flagged = [r for r in results if r["findings"]]
    if args.json:
        print(json.dumps({"results": results, "flagged": len(flagged)}, indent=2))
    else:
        if not flagged:
            print(f"quarantine_selfcheck: {len(results)} repo(s) checked, no QUARANTINED groups")
        for r in flagged:
            print(f"{r['repo']}:")
            for f in r["findings"]:
                print(
                    f"  spec={f['spec_id']} group={f['group']} pr_url={f['pr_url']} "
                    f"age_days={f['age_days']:.1f}"
                )
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
