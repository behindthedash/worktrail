#!/usr/bin/env python3
"""
Periodic stale-worktree sweep (report-only).

`orchestrator/verify.py`'s `cleanup_group()` is gated on delivery and
deliberately keeps a quarantined group's worktrees for human review. This
sweep is the mechanism that comes back to them later: it attributes each
orchestrator worktree to its QUARANTINED journal group and reports it
`QUARANTINE-MERGED` (reclaimable) once there is positive evidence the group
landed anyway -- the group's PR reports `MERGED`, or
`quarantine_selfcheck.reconcile_finding()` finds its files on base / in a
merged PR. Only `QUARANTINED` groups are ever indexed; `MERGED`/`OPEN`
groups and worktrees with no evidence stay on the git-only path below, and
the DIRTY / unpushed-commit guards are checked first and never bypassed.

`worktree-cleanup.md` already owns the classify-then-confirm procedure for
an attended cleanup, but that flow only runs when an agent picks the dashboard's
`cleanup-worktrees` action; nothing runs it unattended. This module is the
unattended half: modeled on `~/.gitnexus/sweep-orphans.sh`'s cron shape, it
scans every worktree under `<repo>-worktrees/` for one or more repos and
reports which ones look reclaimable -- it never deletes anything. Pruning a
worktree destroys a checkout, and `worktree-cleanup.md`'s own doctrine is to
classify, show the user, and confirm before removing; a cron sweep has no
one to confirm with, so this module stops at reporting.

Classification mirrors `worktree-cleanup.md`'s buckets:
- MERGED, GONE, or QUARANTINE-MERGED, and clean -> reclaimable.
- DIRTY (uncommitted changes) -> keep, report only.
- Unpushed local commits (ahead of the branch's own remote-tracking ref) ->
  keep, report only.
- "Merged" is judged by `git cherry <base>...<branch>`, never by
  `git merge-base --is-ancestor` -- a squash-merged base makes the latter
  unreliable (see memory `feedback_git_main_squash_divergence`).
- A branch whose remote-tracking ref is entirely absent is ambiguous --
  "never pushed" (real local-only work; must keep) and "pushed, then the
  remote branch and the local tracking ref were both pruned" (safe to
  reclaim) are indistinguishable from `git ls-remote` alone. `--json`
  callers get a `MERGED`/`CLOSED`/`OPEN` GitHub PR record for that branch
  (via `gh`, best-effort, degrading silently when `gh` is missing,
  unauthenticated, or finds nothing) as the positive evidence needed to tell
  them apart; a `MERGED`/`CLOSED` PR is proof the branch WAS pushed and is
  done, so it reclaims as GONE. No PR evidence -> keep (the conservative
  default -- never wrongly reclaim unique local work).
- Untracked `openspec/changes/*/reviews/**` scratch does not count as dirty
  -- it is gitignorable point-in-time review output (see the repo's artifact
  policy); one measured repo had 23 of 26 "dirty" worktrees dirty only
  because of it.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from worktrail.router import quarantine_selfcheck

_REVIEWS_UNTRACKED_RE = re.compile(r"^\?\? .*openspec/changes/[^/]+/reviews/")


def _run(
    args: list[str], cwd: Path | None = None, timeout: int = 15
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(cwd) if cwd else None,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _is_git_worktree(path: Path) -> bool:
    return (path / ".git").exists()


def find_worktrees(repo: Path) -> list[Path]:
    """Worktree checkouts live at `<repo's parent>/<repo.name>-worktrees/*`."""
    wt_parent = repo.parent / f"{repo.name}-worktrees"
    if not wt_parent.is_dir():
        return []
    return sorted(d for d in wt_parent.iterdir() if d.is_dir() and _is_git_worktree(d))


def default_base_branch(repo: Path) -> str:
    out = _run(
        ["git", "-C", str(repo), "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]
    )
    if out is not None and out.returncode == 0:
        branch = out.stdout.strip()
        if branch.startswith("origin/"):
            return branch[len("origin/") :]
    return "main"


def branch_of(worktree: Path) -> str | None:
    out = _run(["git", "-C", str(worktree), "symbolic-ref", "--short", "-q", "HEAD"])
    if out is None or out.returncode != 0:
        return None
    return out.stdout.strip() or None


def is_dirty(worktree: Path) -> bool:
    """Uncommitted changes, excluding gitignorable `.../reviews/**` scratch.

    A `status --porcelain` call that fails to run at all (git missing,
    timeout) is treated as dirty -- an unknown state must never be classified
    reclaimable.
    """
    # `--untracked-files=all` expands an untracked directory into its individual
    # files instead of collapsing it to one `?? openspec/` line -- without it, a
    # single tracked file anywhere alongside an untracked `reviews/` dir would
    # make the exclusion regex below unable to see the reviews/ path at all.
    out = _run(
        ["git", "-C", str(worktree), "status", "--porcelain", "--untracked-files=all"]
    )
    if out is None or out.returncode != 0:
        return True
    real = [
        ln
        for ln in out.stdout.splitlines()
        if ln.strip() and not _REVIEWS_UNTRACKED_RE.match(ln)
    ]
    return bool(real)


def has_unpushed_commits(
    repo: Path, branch: str, remote: str = "origin"
) -> bool | None:
    """True if `branch` has commits its own `<remote>/<branch>` doesn't.

    None when the remote-tracking ref doesn't exist locally (never pushed,
    or the remote branch is gone) -- callers must treat that as "unknown,
    keep", not as "no unpushed commits".
    """
    check_ref = _run(
        [
            "git",
            "-C",
            str(repo),
            "rev-parse",
            "--verify",
            "-q",
            f"refs/remotes/{remote}/{branch}",
        ]
    )
    if check_ref is None or check_ref.returncode != 0:
        return None
    out = _run(
        ["git", "-C", str(repo), "rev-list", "--count", f"{remote}/{branch}..{branch}"]
    )
    if out is None or out.returncode != 0:
        return None
    try:
        return int(out.stdout.strip()) > 0
    except ValueError:
        return None


def is_merged_into_base(
    repo: Path, base: str, branch: str, remote: str = "origin"
) -> bool:
    """Squash-merge-safe "is it merged": every commit unique to `branch`
    already exists on `<remote>/<base>` (`git cherry` with no `+` lines).
    """
    out = _run(["git", "-C", str(repo), "cherry", f"{remote}/{base}", branch])
    if out is None or out.returncode != 0:
        return False
    return not any(ln.startswith("+") for ln in out.stdout.splitlines())


def remote_branch_gone(repo: Path, branch: str, remote: str = "origin") -> bool:
    out = _run(
        ["git", "-C", str(repo), "ls-remote", "--exit-code", "--heads", remote, branch]
    )
    return out is not None and out.returncode != 0


def _gh_available() -> bool:
    return shutil.which("gh") is not None


def _run_gh(
    repo: Path, args: list[str], timeout: int
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _gh_authenticated(repo: Path, timeout: int) -> bool:
    out = _run_gh(repo, ["auth", "status"], timeout)
    return out is not None and out.returncode == 0


def pr_state_for_branch(repo: Path, branch: str, timeout: int = 15) -> str | None:
    """Best-effort GitHub PR state (`MERGED`/`CLOSED`/`OPEN`) for `branch`'s most
    recent PR, or `None` when `gh` is missing, unauthenticated, times out, or no
    PR is found. Never raises.
    """
    if not _gh_available() or not _gh_authenticated(repo, timeout):
        return None
    out = _run_gh(
        repo,
        [
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "state",
            "--limit",
            "1",
        ],
        timeout,
    )
    if out is None or out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except (ValueError, TypeError):
        return None
    if not data:
        return None
    return data[0].get("state")


def pr_state_for_url(repo: Path, pr_url: str, timeout: int = 15) -> str | None:
    """Best-effort GitHub PR state for `pr_url` via `gh pr view`, or `None`
    when `gh` is missing, unauthenticated, times out, fails, or returns
    unparseable output. Never raises (same posture as `pr_state_for_branch`).
    """
    if not pr_url or not _gh_available() or not _gh_authenticated(repo, timeout):
        return None
    out = _run_gh(repo, ["pr", "view", pr_url, "--json", "state"], timeout)
    if out is None or out.returncode != 0:
        return None
    try:
        data = json.loads(out.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return data.get("state")


def build_attribution_index(repo: Path) -> dict[str, dict[str, Any]]:
    """Worktree directory name -> `{spec_id, group, pr_url}` for every
    QUARANTINED group in the repo's run journals.

    Task worktrees are `<spec_id>-<task_id>` (lowercased, per
    `orchestrator/worktree.py`'s `worktree_path`), resolved through the cached
    RunPlan via `quarantine_selfcheck.group_task_ids()`; a missing RunPlan
    leaves them unattributed. Verify worktrees are `<spec_id>-verify-<group>`
    by name and need no RunPlan. `MERGED`/`OPEN` groups are never indexed.
    """
    index: dict[str, dict[str, Any]] = {}
    worktrees_dir = repo.parent / f"{repo.name}-worktrees"
    if not worktrees_dir.is_dir():
        return index
    for journal_path in quarantine_selfcheck._iter_journal_files(worktrees_dir):
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        groups = journal.get("groups") if isinstance(journal, dict) else None
        if not isinstance(groups, dict):
            continue
        spec_id = quarantine_selfcheck._spec_id_from_journal_path(journal_path)
        for group_name, group in groups.items():
            if not isinstance(group, dict) or group.get("state") != "QUARANTINED":
                continue
            attribution = {
                "spec_id": spec_id,
                "group": group_name,
                "pr_url": group.get("pr_url", "") or "",
            }
            index[f"{spec_id}-verify-{group_name}"] = attribution
            for task_id in (
                quarantine_selfcheck.group_task_ids(repo, spec_id, group_name) or []
            ):
                index[f"{spec_id}-{task_id.lower()}"] = attribution
    return index


def quarantine_merge_evidence(
    repo: Path,
    attribution: dict[str, Any],
    cache: dict[tuple[str, str], str | None] | None = None,
    gh_timeout: int = 15,
) -> str | None:
    """Reason string when the attributed QUARANTINED group is confirmed landed,
    else `None`. Memoised per `(spec_id, group)` in `cache` so a sweep runs
    the lookup once per group, not once per worktree."""
    key = (attribution["spec_id"], attribution["group"])
    if cache is not None and key in cache:
        return cache[key]
    reason: str | None = None
    pr_url = attribution.get("pr_url") or ""
    if pr_state_for_url(repo, pr_url, timeout=gh_timeout) == "MERGED":
        reason = f"quarantined group {key[1]} of {key[0]}: PR {pr_url} is MERGED"
    else:
        record = quarantine_selfcheck.reconcile_finding(
            repo, {"spec_id": key[0], "group": key[1], "pr_url": pr_url}
        )
        if record is not None:
            reason = (
                f"quarantined group {key[1]} of {key[0]}: reconciled via "
                f"{record.get('method')}"
            )
    if cache is not None:
        cache[key] = reason
    return reason


def classify_worktree(
    repo: Path,
    worktree: Path,
    base: str,
    remote: str = "origin",
    gh_timeout: int = 15,
    attribution_index: dict[str, dict[str, Any]] | None = None,
    evidence_cache: dict[tuple[str, str], str | None] | None = None,
) -> dict[str, Any]:
    branch = branch_of(worktree)
    if branch is None:
        return {
            "path": str(worktree),
            "branch": None,
            "state": "UNKNOWN",
            "reclaimable": False,
            "reason": "detached HEAD or unresolvable branch",
        }

    if is_dirty(worktree):
        return {
            "path": str(worktree),
            "branch": branch,
            "state": "DIRTY",
            "reclaimable": False,
            "reason": "uncommitted changes",
        }

    unpushed = has_unpushed_commits(repo, branch, remote)
    if unpushed is True:
        return {
            "path": str(worktree),
            "branch": branch,
            "state": "UNPUSHED",
            "reclaimable": False,
            "reason": "unpushed local commits",
        }

    attribution = (attribution_index or {}).get(worktree.name)
    if attribution is not None:
        reason = quarantine_merge_evidence(
            repo, attribution, cache=evidence_cache, gh_timeout=gh_timeout
        )
        if reason is not None:
            return {
                "path": str(worktree),
                "branch": branch,
                "state": "QUARANTINE-MERGED",
                "reclaimable": True,
                "reason": reason,
            }

    if is_merged_into_base(repo, base, branch, remote):
        return {
            "path": str(worktree),
            "branch": branch,
            "state": "MERGED",
            "reclaimable": True,
            "reason": "merged into base",
        }

    if not remote_branch_gone(repo, branch, remote):
        return {
            "path": str(worktree),
            "branch": branch,
            "state": "UNMERGED",
            "reclaimable": False,
            "reason": "not merged into base and remote branch still exists",
        }

    if unpushed is False:
        # A remote-tracking ref existed and confirmed zero commits ahead of it,
        # so the branch's content was fully accounted for on the remote before
        # it (and the local tracking ref) disappeared -- safe to reclaim.
        return {
            "path": str(worktree),
            "branch": branch,
            "state": "GONE",
            "reclaimable": True,
            "reason": "remote branch deleted",
        }

    # unpushed is None: no remote-tracking ref at all, so "never pushed" and
    # "pushed then pruned" are indistinguishable from git alone. Fall back to
    # GitHub PR state as positive evidence the branch was actually pushed.
    pr_state = pr_state_for_branch(repo, branch, timeout=gh_timeout)
    if pr_state in ("MERGED", "CLOSED"):
        return {
            "path": str(worktree),
            "branch": branch,
            "state": "GONE",
            "reclaimable": True,
            "reason": f"remote branch gone; GitHub PR state {pr_state}",
        }
    return {
        "path": str(worktree),
        "branch": branch,
        "state": "UNPUSHED",
        "reclaimable": False,
        "reason": (
            "no remote-tracking ref and no PR evidence of prior push "
            "-- cannot confirm safe to reclaim"
        ),
    }


def sweep_repo(
    repo: Path, remote: str = "origin", do_fetch: bool = True, fetch_timeout: int = 20
) -> dict[str, Any]:
    repo = Path(repo).resolve()
    if not (repo / ".git").exists():
        return {
            "repo": str(repo),
            "checked": False,
            "warning": "not a git repository",
            "worktrees": [],
        }

    if do_fetch:
        _run(
            ["git", "-C", str(repo), "fetch", "--prune", "--quiet", remote],
            timeout=fetch_timeout,
        )

    base = default_base_branch(repo)
    worktrees = find_worktrees(repo)
    attribution_index = build_attribution_index(repo)
    evidence_cache: dict[tuple[str, str], str | None] = {}
    results = [
        classify_worktree(
            repo,
            wt,
            base,
            remote,
            attribution_index=attribution_index,
            evidence_cache=evidence_cache,
        )
        for wt in worktrees
    ]
    reclaimable = [r for r in results if r["reclaimable"]]
    return {
        "repo": str(repo),
        "checked": True,
        "warning": None,
        "base_branch": base,
        "worktree_count": len(results),
        "reclaimable_count": len(reclaimable),
        "worktrees": results,
    }


def discover_repos(parent: Path) -> list[Path]:
    """Every git repo directly under `parent`, skipping `*-worktrees` siblings
    (those are worktree containers, not repos to scan themselves)."""
    if not parent.is_dir():
        return []
    return sorted(
        d
        for d in parent.iterdir()
        if d.is_dir() and (d / ".git").exists() and not d.name.endswith("-worktrees")
    )


def sweep(
    repos: list[Path], remote: str = "origin", do_fetch: bool = True
) -> list[dict[str, Any]]:
    return [sweep_repo(r, remote=remote, do_fetch=do_fetch) for r in repos]


def _render_text(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        if not row["checked"]:
            lines.append(f"{row['repo']}: skipped -- {row['warning']}")
            continue
        lines.append(
            f"{row['repo']}: {row['worktree_count']} worktree(s), "
            f"{row['reclaimable_count']} reclaimable"
        )
        for wt in row["worktrees"]:
            if wt["reclaimable"]:
                lines.append(
                    f"  RECLAIMABLE  {wt['state']:<8} {wt['branch']}  ({wt['reason']})  {wt['path']}"
                )
    return "\n".join(lines) if lines else "no worktrees found"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--repo",
        action="append",
        default=None,
        help="path to a single repo to sweep; repeatable",
    )
    g.add_argument(
        "--repos",
        default=None,
        help="parent directory containing multiple repos to sweep (e.g. ~/projects)",
    )
    p.add_argument("--remote", default="origin")
    p.add_argument(
        "--no-fetch",
        action="store_true",
        help="skip the network `git fetch --prune`; compare against "
        "whatever remote-tracking state is already cached locally",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if args.repo:
        repos = [Path(r) for r in args.repo]
    else:
        repos = discover_repos(Path(args.repos).expanduser())

    rows = sweep(repos, remote=args.remote, do_fetch=not args.no_fetch)

    if args.json:
        print(json.dumps(rows))
    else:
        print(_render_text(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
