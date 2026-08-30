#!/usr/bin/env python3
"""branch_selfcheck.py — cross-repo stale local-branch detector.

A local git branch that is fully merged into its repo's base branch is safe
to delete (AGENTS.md's Git Workflow rule: "Delete merged branches once their
PR lands"), but nothing previously found these automatically -- they only got
cleaned up when a human (or a forked agent burning six-figure tokens) sat
down and worked through the accumulated list by hand, one repo's worktrees
at a time.

The merge classification here is the same four-tier check
`prevent-destructive-commands.py` (this workspace's Claude Code
destructive-command-prevention hook, `~/projects/devops/scripts/claude-hooks/`)
already uses to decide whether `git branch -D <branch>` is safe to allow:
direct ancestry, squash-aware patch equivalence (`git cherry`), a GitHub-side
merged-PR lookup (catches multi-commit squash merges, which patch-equivalence
alone misses), and a one-hop indirect check for a branch that never had its
own PR but whose content fully landed through an intermediate branch that
did (worktrail's own orchestrator produces exactly this shape: per-task and
spec-authoring branches merge into a per-group integration branch by a real
git merge, and only that group branch opens a PR). Ported here rather than
imported: this package has no dependency on the `devops` repo, and the
classification is generic git logic, not hook-specific.

A passive detector, not a gate -- matching `quarantine_selfcheck.py`'s and
this module's other `router/*_selfcheck.py` siblings' posture: `check_repo`/
`sweep` only report which local branches are provably merged; `drain.py`'s
`find_stale_branches`/`prune_stale_branch` pair (registered in
`REMEDIATION_TABLE`) is what actually deletes anything, on drain's existing
schedule.

Usage:
  branch_selfcheck.py --repo /path/to/repo [--json]
  branch_selfcheck.py --repos-root ~/projects [--json]   # sweep every repo
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .policy import load_policy
from .policy_selfcheck import discover_repo_names

# Branches never considered for pruning, even if a merge check would
# otherwise clear them -- defense in depth alongside the repo's own resolved
# base branch and whichever branch each worktree currently has checked out.
PROTECTED_BRANCHES: frozenset[str] = frozenset({"main", "master", "dev", "stg", "prd"})


def _base_branch_for(repo: Path) -> str:
    """Mirrors `drain.py`'s `_base_branch_for` (not imported: `router/` sits
    below `drain/` in the layering, and this is a two-line policy read)."""
    try:
        return load_policy(repo).get("base_branch") or "dev"
    except Exception:  # noqa: BLE001
        return "dev"


def _resolve_ref_sha(ref: str, cwd: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", ref],
            check=False,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _has_merged_pr(branch: str, cwd: Path) -> bool:
    """Ask GitHub whether a PR from this branch's head merged. The only tier
    that can see a multi-commit squash merge for what it is -- GitHub itself
    records the merge, independent of whether the resulting commit graph is
    patch-equivalent to any single source commit. Best-effort: False (fail
    closed) if `gh` is missing, unauthenticated, offline, times out, or
    returns anything unparseable."""
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "merged",
                "--head",
                branch,
                "--json",
                "number",
                "--limit",
                "1",
            ],
            check=False,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    try:
        prs = json.loads(result.stdout)
    except (json.JSONDecodeError, ValueError):
        return False
    return isinstance(prs, list) and len(prs) > 0


def _has_merged_indirectly(branch: str, cwd: Path) -> bool:
    """A branch with no merged PR of its own may still be fully landed if
    some OTHER local branch that contains it as an ancestor has itself
    landed. Covers worktrail's own two-hop orchestrator structure: a
    per-task branch (`<spec_id>/<task_id>`) or spec-authoring branch
    (`spec/<spec_id>`) merges via a real git merge -- ancestry preserved --
    into a per-group integration branch (`full-<run-id>/<group>`), and only
    THAT group branch opens its own PR. Bounded to one hop; a branch that
    would need two hops fails closed here, same as every other inconclusive
    case in this module. Best-effort: False if `git branch --contains`
    fails or times out."""
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)", "--contains", branch],
            check=False,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    containers = [
        line.strip()
        for line in result.stdout.splitlines()
        if line.strip() and line.strip() != branch
    ]
    for other in containers:
        other_sha = _resolve_ref_sha(f"refs/heads/{other}", cwd)
        if not other_sha:
            continue
        try:
            ancestor = subprocess.run(
                ["git", "merge-base", "--is-ancestor", other_sha, "HEAD"],
                check=False,
                cwd=str(cwd),
                timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if ancestor.returncode == 0:
            return True
        if _has_merged_pr(other, cwd):
            return True
    return False


def merge_method(branch: str, cwd: Path) -> str | None:
    """The first classification tier that proves `branch` is fully merged
    into `cwd`'s current HEAD, or None if none of them do (fails closed).

    Four checks, in order, each catching what the previous one misses:
    1. `ancestry` -- direct ancestry (regular merge / fast-forward).
    2. `cherry` -- patch equivalence via `git cherry` (a single-commit
       squash merge rewrites history, so the branch tip is never a literal
       ancestor of the base even though the change landed).
    3. `merged-pr` -- `gh pr list --state merged --head <branch>` (a
       multi-commit squash merge defeats `git cherry` too).
    4. `merged-indirectly` -- one-hop indirect landing (see
       `_has_merged_indirectly`).
    """
    branch_sha = _resolve_ref_sha(f"refs/heads/{branch}", cwd)
    if not branch_sha:
        return None

    try:
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", branch_sha, "HEAD"],
            check=False,
            cwd=str(cwd),
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if ancestor.returncode == 0:
        return "ancestry"

    try:
        cherry = subprocess.run(
            ["git", "cherry", "HEAD", branch_sha],
            check=False,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cherry.returncode == 0:
        lines = [line for line in cherry.stdout.splitlines() if line.strip()]
        if lines and all(line.startswith("-") for line in lines):
            return "cherry"

    if _has_merged_pr(branch, cwd):
        return "merged-pr"

    if _has_merged_indirectly(branch, cwd):
        return "merged-indirectly"

    return None


def _local_branches(repo: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "branch", "--format=%(refname:short)"],
            check=False,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _worktree_branches(repo: Path) -> dict[str, Path]:
    """Map every branch checked out in one of `repo`'s worktrees (including
    the canonical checkout itself) to that worktree's path, parsed from
    `git worktree list --porcelain`."""
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            check=False,
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    mapping: dict[str, Path] = {}
    current_path: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            current_path = Path(line[len("worktree ") :].strip())
        elif line.startswith("branch ") and current_path is not None:
            ref = line[len("branch ") :].strip()
            branch = ref.removeprefix("refs/heads/")
            mapping[branch] = current_path
    return mapping


def _is_worktree_dirty(worktree: Path) -> bool:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            check=False,
            cwd=str(worktree),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True  # unresolvable -- fail closed, never prune
    if result.returncode != 0:
        return True
    return bool(result.stdout.strip())


def check_repo(repo: Path) -> dict[str, Any]:
    """Findings for one repo's local branches. Empty `prunable` = clean.

    A branch is `prunable` only when it is neither the repo's resolved base
    branch nor one of `PROTECTED_BRANCHES`, is not currently checked out in
    a worktree with uncommitted changes (git itself also refuses to delete
    a branch checked out anywhere, so a clean worktree is removed first --
    see `prune_stale_branch` in `drain.py`), and `merge_method` proves it is
    fully merged into the repo's current HEAD."""
    repo = Path(repo)
    result: dict[str, Any] = {"repo": repo.name, "path": str(repo), "prunable": []}
    if not (repo / ".git").exists():
        return result
    base = _base_branch_for(repo)
    excluded = PROTECTED_BRANCHES | {base}
    worktree_branches = _worktree_branches(repo)
    for branch in _local_branches(repo):
        if branch in excluded:
            continue
        worktree = worktree_branches.get(branch)
        if worktree is not None:
            if worktree == repo:
                continue  # the canonical checkout's own current branch
            if _is_worktree_dirty(worktree):
                continue
        method = merge_method(branch, repo)
        if method is None:
            continue
        result["prunable"].append(
            {
                "branch": branch,
                "worktree_path": str(worktree) if worktree else None,
                "method": method,
            }
        )
    return result


def sweep(repos_root: Path) -> list[dict[str, Any]]:
    """check_repo() for every repo under `repos_root` that has prunable branches."""
    names = discover_repo_names(repos_root)
    results = []
    for name in names:
        r = check_repo(repos_root / name)
        if r["prunable"]:
            results.append(r)
    return results


def main(argv: list[str] | None = None) -> int:
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

    flagged = [r for r in results if r["prunable"]]
    if args.json:
        print(json.dumps({"results": results, "flagged": len(flagged)}, indent=2))
    else:
        if not flagged:
            print(
                f"branch_selfcheck: {len(results)} repo(s) checked, no prunable branches"
            )
        for r in flagged:
            print(f"{r['repo']}:")
            for f in r["prunable"]:
                wt = f"worktree={f['worktree_path']} " if f["worktree_path"] else ""
                print(f"  branch={f['branch']} {wt}method={f['method']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
