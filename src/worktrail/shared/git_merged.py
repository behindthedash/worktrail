#!/usr/bin/env python3
"""Content-based "is this branch already in that base?" for every caller.

`git merge-base --is-ancestor` and `git cherry` both answer this by *commit
identity*, and a squash merge breaks both: the squashed commit is not a
descendant of the branch tip, and a multi-commit branch squashed into one
commit has no patch-id in common with it either. Every worktrail surface that
asks the question -- the orchestrator's dependency stacking, its empty-diff
integration guard, and the stale-worktree sweep -- needs the *content*
answer, so it lives here once instead of three times.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return (
        _git(repo, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0
    )


def branch_content_in_base(repo: Path, branch: str, base_ref: str) -> bool:
    """True when `branch`'s changes are already present in `base_ref`, whether
    it landed as a merge/rebase (plain ancestry) or as a SQUASH.

    For the squash case, a three-way merge of `branch` into `base_ref`
    (`git merge-tree --write-tree`) yields exactly `base_ref`'s own tree iff
    the branch contributes nothing base does not already have.

    A CONFLICTED merge-tree (exit 1) also counts as "in base" (brief
    20260905-162859): when a group PR squash-merges with review fixups layered
    on top of a task's own commits, the retained task branch's verbatim diff
    never appears in base and a three-way merge conflicts on exactly those
    hunks -- observed live on every retained shared-pr-landing-pipeline branch
    (2.1/5.1/14.1/17.1). Such a branch can never be a stacking point either
    way: a worktree forked from it fails the base carry with the same conflict
    (`WorktreeMissingDependencyFileError` on every fan-out task). Treating it
    as superseded lets the dependent fork from base, whose content is what the
    dependency's group actually shipped. Only a merge-tree that FAILED to run
    (exit >1, e.g. an unresolvable ref) stays "not in base".
    """
    if is_ancestor(repo, branch, base_ref):
        return True
    merged = _git(repo, "merge-tree", "--write-tree", base_ref, branch)
    if merged.returncode == 1:
        return True
    if merged.returncode != 0:
        return False
    base_tree = _git(
        repo, "rev-parse", "--verify", f"{base_ref}^{{tree}}"
    ).stdout.strip()
    merged_tree = (merged.stdout or "").strip().splitlines()
    return bool(base_tree) and bool(merged_tree) and merged_tree[0] == base_tree


def has_commits_beyond(repo: Path, branch: str, base_ref: str) -> bool:
    """True when `branch` carries at least one commit `base_ref` does not.

    Distinguishes "the work landed already" from "there was never any work":
    a branch whose content is in base AND that has its own commits was
    delivered out-of-band (e.g. as a tail PR); a branch with no commits at all
    beyond base is a delegate that reported done without implementing
    anything.
    """
    out = _git(repo, "rev-list", "--count", f"{base_ref}..{branch}")
    if out.returncode != 0:
        return False
    try:
        return int(out.stdout.strip()) > 0
    except ValueError:
        return False
