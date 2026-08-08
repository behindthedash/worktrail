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
from worktrail.orchestrator.integrate import QUARANTINE_BUDGET_EXHAUSTED

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


def _files_on_base(repo: Path, files: List[str], base: str = "") -> bool:
    """Whether every path in `files` still exists on `base` (or the current branch).

    A group that reconciled to `QUARANTINED` in the run journal may simply
    already be present on the base branch by the time this check runs (e.g. a
    later group's merge subsumed it). `git ls-tree` against `base` is the
    cheapest way to confirm that without a network call.
    """
    if not base:
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


def _merged_pr_matching(repo: Path, files: List[str]) -> Optional[str]:
    """URL of the newest merged PR whose changed-file set is a superset of `files`.

    Mirrors `reconcile_pr_labels.py`'s `_open_prs()` subprocess pattern: never
    raises, `None` on any `gh` failure (missing, unauthenticated, offline,
    non-zero exit, bad JSON) so a network hiccup fails a finding open (still
    surfaced to a human) rather than guessing.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "merged",
             "--json", "url,files", "--limit", "50"],
            capture_output=True, text=True, timeout=30, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        prs = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(prs, list):
        return None
    wanted = set(files)
    for pr in prs:
        pr_files = pr.get("files")
        if not isinstance(pr_files, list):
            continue
        changed = {f.get("path") for f in pr_files if isinstance(f, dict) and f.get("path")}
        if wanted <= changed:
            return pr.get("url")
    return None


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


def reconcile_finding(repo: Path, finding: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Attempt to auto-resolve a raw QUARANTINED finding.

    Tries base-branch file presence first (cheap, no network), then a
    merged-PR file-set match (network). Returns a reconciliation record
    (`spec_id`, `group`, `method`, `evidence`) on either signal succeeding,
    else `None` (unreconciled -- the finding stays a finding).
    """
    files = _group_files(repo, finding["spec_id"], finding["group"])
    if files is None:
        return None
    if _files_on_base(repo, files):
        return {
            "spec_id": finding["spec_id"],
            "group": finding["group"],
            "method": "base-branch-files",
            "evidence": files,
        }
    pr_url = _merged_pr_matching(repo, files)
    if pr_url:
        return {
            "spec_id": finding["spec_id"],
            "group": finding["group"],
            "method": "merged-pr-files",
            "evidence": pr_url,
        }
    return None


def check_repo(repo: Path) -> Dict[str, Any]:
    """Findings for every run journal in one repo. Empty `findings` = clean.

    `findings` holds only unreconciled QUARANTINED groups needing human
    triage; `reconciled` holds the auto-resolved ones (never a silent drop --
    see `reconcile_finding()`). `resumable` holds groups whose
    `quarantine_reason` is `QUARANTINE_BUDGET_EXHAUSTED`: the group simply
    never got a chance to run (it did not fail), so it is safely resumable
    with a plain re-run and is routed here instead of into `findings`'
    human-triage path.
    """
    repo = Path(repo)
    result: Dict[str, Any] = {
        "repo": repo.name, "path": str(repo),
        "findings": [], "reconciled": [], "resumable": [],
    }
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
            quarantine_reason = group.get("quarantine_reason", "")
            finding = {
                "spec_id": spec_id,
                "group": group_name,
                "pr_url": group.get("pr_url", ""),
                "age_days": age_days,
                "quarantine_reason": quarantine_reason,
            }
            if quarantine_reason == QUARANTINE_BUDGET_EXHAUSTED:
                result["resumable"].append(finding)
                continue
            reconciliation = reconcile_finding(repo, finding)
            if reconciliation is not None:
                result["reconciled"].append(reconciliation)
            else:
                result["findings"].append(finding)
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
    resumable_repos = [r for r in results if r["resumable"]]
    if args.json:
        print(json.dumps({"results": results, "flagged": len(flagged)}, indent=2))
    else:
        if not flagged and not resumable_repos:
            print(f"quarantine_selfcheck: {len(results)} repo(s) checked, no QUARANTINED groups")
        for r in flagged:
            print(f"{r['repo']}:")
            for f in r["findings"]:
                print(
                    f"  spec={f['spec_id']} group={f['group']} pr_url={f['pr_url']} "
                    f"age_days={f['age_days']:.1f}"
                )
        for r in resumable_repos:
            print(f"{r['repo']} (resumable, no action needed -- re-run full-real):")
            for f in r["resumable"]:
                print(f"  spec={f['spec_id']} group={f['group']} age_days={f['age_days']:.1f}")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
