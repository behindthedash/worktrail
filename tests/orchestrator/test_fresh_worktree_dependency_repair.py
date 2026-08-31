#!/usr/bin/env python3
"""Regression for work-queue brief 20260822-115008, gap 2:
`ensure_wt`/`_ensure_wt`'s fresh-worktree-creation (`if not wt.exists()`)
branch only ever got ONE chance to carry a dependency's content -- the single
attempt `add_stacked_worktree` makes at creation time via
`_carry_squash_merged_dependencies`. If that one attempt failed for any
transient reason (a git fetch/lock error, not a genuine content conflict),
the bare `_require_dependency_files` guard that followed crashed the whole
run with no second chance -- reproduced live 2026-08-22 on devops run
go-20260822-102138 (spec corpus-live-nightly-schedule): task 4.1's worktree
was missing task 3.1's declared file even though 3.1 was already merged to
origin/main well before 4.1's tail dispatch. Manual recovery (delete the
stale worktree/branch, resume) worked precisely because it forced a second
attempt.

`_require_dependency_files_with_repair` already retries the carry once for
the RETAINED (already-existing) worktree branch (brief 20260817-223443, gap
1 of the same defect class -- see `test_retained_worktree_dependency_repair.py`).
It is now also wired into the fresh-creation branch, giving it the same
second chance.
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
    """The dependency (TASK-001) squash-merges onto the live base BEFORE the
    dependent task's worktree is ever created -- the exact "already merged
    well before this task's dispatch" timing the brief reports, as opposed to
    gap 1's "merges after worktree creation" timing."""
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
    `main` -- a task's own recorded branch-tip `head_sha` is never an ancestor
    of the rewritten commit a squash-merge produces on the live base, so this
    reproduces that shape without depending on squash-merge internals."""
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


class FreshWorktreeRepairsWhenFirstCarryAttemptFails(unittest.TestCase):
    def test_second_attempt_succeeds_where_bare_guard_would_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)
            # TASK-001 is already squash-merged onto origin/main BEFORE
            # TASK-002's worktree is ever created (gap 2's timing).
            _push_upstream_squash_merge(tmp, bare)

            task, by_id = _task_and_by_id(head_sha=_stale_head_sha(repo))

            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)

            # Simulate `add_stacked_worktree`'s own creation-time carry
            # attempt failing transiently (e.g. a git fetch/lock error) by
            # making the real `_carry_squash_merged_dependencies` a no-op
            # for exactly that one call.
            with mock.patch.object(
                live, "_carry_squash_merged_dependencies", return_value=None
            ):
                live.add_stacked_worktree(
                    repo, "102-x", task, by_id, wt, remote="origin", base="main"
                )

            # Pre-fix behavior: the bare guard has no way to recover from the
            # failed first attempt and crashes the whole run.
            with self.assertRaises(live.WorktreeMissingDependencyFileError):
                live._require_dependency_files(wt, task, by_id)

            # Post-fix behavior: the repair-capable guard (now wired into
            # both ensure_wt/_ensure_wt fresh-creation branches) gets a real,
            # unmocked second attempt and succeeds.
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


class AddStackedWorktreeRetriesFailedCarryOnce(unittest.TestCase):
    def test_transient_first_failure_recovers_on_internal_retry(self):
        """Follow-up to brief 20260831-084532's own fix (PR #873): making
        `_carry_squash_merged_dependencies` raise on a failed merge (instead
        of WARNing and continuing) regressed THIS brief's guarantee, because
        `add_stacked_worktree`'s own embedded carry call had no retry of its
        own. A raise there now propagated straight out of `add_stacked_worktree`
        on the very first attempt -- and deferring to the caller's follow-up
        `_require_dependency_files_with_repair` as "the real second attempt"
        does not actually work in general: that repair path only detects and
        retries a MISSING declared path, so it is a silent no-op for a
        dependency whose declared file already existed before its own edit
        (`_require_dependency_files`'s bare existence check never raises for
        such a file, so the repair carry it gates never even runs). So
        `add_stacked_worktree` must be the one giving its own carry a second,
        internal try -- mirroring `_add()`'s prune-and-retry-once pattern
        just above it -- before raising for real."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)
            _push_upstream_squash_merge(tmp, bare)

            task, by_id = _task_and_by_id(head_sha=_stale_head_sha(repo))

            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)

            real_carry = live._carry_squash_merged_dependencies
            calls = []

            def _flaky_carry(*args, **kwargs):
                calls.append(1)
                if len(calls) == 1:
                    raise live.WorktreeMissingDependencyFileError(
                        "simulated transient carry failure"
                    )
                return real_carry(*args, **kwargs)

            with mock.patch.object(
                live, "_carry_squash_merged_dependencies", side_effect=_flaky_carry
            ):
                # Must NOT raise: the first (simulated-transient) failure gets
                # a real second, internal attempt that succeeds for real.
                live.add_stacked_worktree(
                    repo, "102-x", task, by_id, wt, remote="origin", base="main"
                )

            self.assertEqual(len(calls), 2, "must retry exactly once internally")
            self.assertEqual((wt / "dep_file.py").read_text(), "dependency content\n")

    def test_two_persistent_failures_raise(self):
        """The two-strikes rule: if BOTH the first attempt and the internal
        retry fail, `add_stacked_worktree` must raise for real -- it is the
        definitive backstop for this shape (see class docstring above), not
        merely a first-of-two-callers pass-through."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)
            _push_upstream_squash_merge(tmp, bare)

            task, by_id = _task_and_by_id(head_sha=_stale_head_sha(repo))

            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)

            with mock.patch.object(
                live,
                "_carry_squash_merged_dependencies",
                side_effect=live.WorktreeMissingDependencyFileError(
                    "simulated persistent carry failure"
                ),
            ):
                with self.assertRaises(live.WorktreeMissingDependencyFileError):
                    live.add_stacked_worktree(
                        repo, "102-x", task, by_id, wt, remote="origin", base="main"
                    )


if __name__ == "__main__":
    unittest.main()
