#!/usr/bin/env python3
"""Regression for work-queue brief 20260817-223443, gap 1:
`ensure_wt`/`_ensure_wt`'s retained-worktree (`else`) branch only re-validated
dependency content via `_require_dependency_files` -- it never re-attempted
the squash-merge carry `add_stacked_worktree` runs at creation time. Once a
worktree hit `WorktreeMissingDependencyFileError` because a dependency
squash-merged (and had its branch deleted) AFTER the worktree was created,
every subsequent resume re-hit the identical error forever, requiring manual
`git worktree remove --force` + `git branch -D` recovery.

`_require_dependency_files_with_repair` is the extracted, directly-testable
helper both `ensure_wt` and `_ensure_wt` now call from their retained-worktree
branch.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _init_bare_and_repo(tmp):
    bare = Path(tmp) / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    subprocess.run(
        ["git", "-C", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"], check=True
    )

    repo = Path(tmp) / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "shared.py").write_text("line1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare, repo


def _push_upstream_squash_merge(tmp, bare):
    """Simulate a dependency's squash-merge landing on the live base via a
    separate clone, after the retained worktree below was already created --
    the exact "dependency squash-merged after worktree creation" timing gap 1
    targets."""
    upstream_edit = Path(tmp) / "upstream_edit"
    subprocess.run(["git", "clone", "-q", str(bare), str(upstream_edit)], check=True)
    _git(upstream_edit, "config", "user.email", "t@example.com")
    _git(upstream_edit, "config", "user.name", "Test")
    (upstream_edit / "dep_file.py").write_text("dependency content\n")
    _git(upstream_edit, "add", "-A")
    _git(upstream_edit, "commit", "-q", "-m", "TASK-001 squash-merge onto main")
    _git(upstream_edit, "push", "-q", "origin", "main")


def _stale_head_sha(repo: Path) -> str:
    """A real commit SHA in `repo`'s object database that is NOT an ancestor of
    `main` -- reproduces the resumed-run shape where the journal already has a
    dependency `head_sha` (this run drove it earlier) that predates the
    squash-merge rewrite, so `_is_ancestor` correctly returns False instead of
    the DONE-status downgrade (which only applies when no `head_sha` is
    journaled at all -- a fresh, --fresh-style run, not a resume)."""
    orphan = Path(str(repo) + "-orphan-scratch")
    subprocess.run(["git", "clone", "-q", str(repo), str(orphan)], check=True)
    _git(orphan, "config", "user.email", "t@example.com")
    _git(orphan, "config", "user.name", "Test")
    _git(orphan, "checkout", "-q", "--orphan", "throwaway")
    (orphan / "unrelated.txt").write_text("unrelated\n")
    _git(orphan, "add", "-A")
    _git(orphan, "commit", "-q", "-m", "unrelated throwaway commit")
    sha = _git(orphan, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "fetch", "-q", str(orphan), "throwaway:refs/throwaway-scratch")
    return sha


def _task_and_by_id(head_sha: str | None = None):
    task = {"id": "TASK-002", "deps": ["TASK-001"]}
    dep = {"id": "TASK-001", "status": "completed", "files": ["dep_file.py"]}
    if head_sha:
        dep["head_sha"] = head_sha
    by_id = {"TASK-001": dep, "TASK-002": task}
    return task, by_id


class RetainedWorktreeRepairsOnDrift(unittest.TestCase):
    def test_repairs_via_carry_when_dependency_squash_merges_after_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)

            # The worktree is created BEFORE the dependency's squash-merge
            # lands -- a plain branch off local HEAD, no carry needed yet.
            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)
            _git(repo, "worktree", "add", "-b", "102-x/task-002", str(wt), "main")

            task, by_id = _task_and_by_id(head_sha=_stale_head_sha(repo))

            # NOW the dependency squash-merges onto the live base -- the
            # retained worktree above predates it and is missing its content.
            _push_upstream_squash_merge(tmp, bare)

            with self.assertRaises(live.WorktreeMissingDependencyFileError):
                live._require_dependency_files(wt, task, by_id)

            events = live._require_dependency_files_with_repair(
                wt, task, by_id, repo, "102-x", "origin", "main"
            )

            self.assertEqual(
                events,
                [
                    {
                        "event": "worktree_drift_repaired",
                        "task": "TASK-002",
                        "at": mock.ANY,
                    }
                ],
            )
            self.assertEqual((wt / "dep_file.py").read_text(), "dependency content\n")
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "the repair leaves a clean tree")

    def test_reraises_when_repair_does_not_resolve_the_drift(self):
        """No remote/base supplied -- the repair can't run at all, so this
        must still fail loud exactly as before this change (the fail-loud
        backstop for a genuinely unresolvable drift is unchanged)."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)

            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)
            _git(repo, "worktree", "add", "-b", "102-x/task-002", str(wt), "main")

            task, by_id = _task_and_by_id(head_sha=_stale_head_sha(repo))
            _push_upstream_squash_merge(tmp, bare)

            with self.assertRaises(live.WorktreeMissingDependencyFileError):
                live._require_dependency_files_with_repair(
                    wt, task, by_id, repo, "102-x", None, None
                )

    def test_no_repair_attempted_when_worktree_already_healthy(self):
        """The common case: dependency content is already present. The repair
        path must not even be entered -- no extra fetch/merge on the hot
        path."""
        with tempfile.TemporaryDirectory() as tmp:
            _bare, repo = _init_bare_and_repo(tmp)
            task, by_id = _task_and_by_id()

            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)
            _git(repo, "worktree", "add", "-b", "102-x/task-002", str(wt), "main")
            (wt / "dep_file.py").write_text("dependency content\n")
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", "already carried")

            with mock.patch.object(
                live, "_carry_squash_merged_dependencies"
            ) as mock_carry:
                events = live._require_dependency_files_with_repair(
                    wt, task, by_id, repo, "102-x", "origin", "main"
                )
            mock_carry.assert_not_called()
            self.assertEqual(events, [])


if __name__ == "__main__":
    unittest.main()
