#!/usr/bin/env python3
"""Regression coverage for `_carry_squash_merged_dependencies`
(`add_stacked_worktree`'s remote/base carry path), from work-queue brief
20260816-153112.

No test anywhere previously called `add_stacked_worktree` with `remote`/`base`
arguments, so this carry path -- reachable from real orchestrator runs via
`live.py`'s `ensure_wt` -- had zero coverage, not even a happy-path test.

The carry used to merge the live base ref into the stacked worktree with
`git merge --no-edit -X ours <ref>`, which auto-resolves EVERY content-level
conflict in favor of the (possibly stale) worktree side -- not just conflicts
touching the triggering dependency's own declared files. That was the same
architectural risk class already root-caused and fixed for `integrate_one`'s
dependency-branch-gone fallback (PR #475,
`docs/specs/research/integrate-one-dep-branch-gone-fallback-root-cause.md`).
See `docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md` for
the investigation that reproduced the silent-content-loss defect and led to
dropping `-X ours` from the merge (this PR): a genuine conflict now fails the
merge loudly instead of being auto-resolved, letting dependency-file
validation's existing fail-loud backstop catch it.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _init_bare_and_repo(tmp):
    """A bare `origin` remote plus a local `repo` pushed to it as `main`,
    both with an explicit `main` HEAD so a second push from a fresh clone
    doesn't hit git's ambiguous-default-branch push rejection."""
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
    (repo / "shared.py").write_text("line1\nline2-original\nline3\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare, repo


def _push_upstream_squash_merge(tmp, bare, *, dep_file_only: bool):
    """Simulate the dependency's squash-merge landing on the live base via a
    separate clone -- `repo`'s own checkout never sees these commits locally,
    matching the branch-gone fallback's "HEAD predates the squash" scenario.
    `dep_file_only=True` touches only the dependency's own declared file
    (the happy path); `False` also rewrites the shared file's line the local
    checkout independently diverges on (the conflicting-content scenario).
    """
    upstream_edit = Path(tmp) / "upstream_edit"
    subprocess.run(["git", "clone", "-q", str(bare), str(upstream_edit)], check=True)
    _git(upstream_edit, "config", "user.email", "t@example.com")
    _git(upstream_edit, "config", "user.name", "Test")
    (upstream_edit / "dep_file.py").write_text("dependency content\n")
    if not dep_file_only:
        (upstream_edit / "shared.py").write_text("line1\nline2-from-dependency\nline3\n")
    _git(upstream_edit, "add", "-A")
    _git(upstream_edit, "commit", "-q", "-m", "TASK-001 squash-merge onto main")
    _git(upstream_edit, "push", "-q", "origin", "main")


def _by_id_and_task():
    task = {"id": "TASK-002", "deps": ["TASK-001"]}
    by_id = {
        "TASK-001": {"id": "TASK-001", "status": "completed", "files": ["dep_file.py"]},
        "TASK-002": task,
    }
    return task, by_id


def _push_upstream_edit_to_existing_file(tmp, bare):
    """Simulate the dependency's squash-merge editing `existing.md`, a file
    that was already present in `repo` before this dependency ever ran (unlike
    `dep_file.py` above, which is brand new). This is the shape the tail-kind
    freshness bug (brief 20260817-120332) reproduces: a dependency's declared
    file exists on disk in every worktree regardless of which commit it was
    forked from, so a mere existence check can never tell a stale copy from a
    fresh one."""
    upstream_edit = Path(tmp) / "upstream_edit"
    subprocess.run(["git", "clone", "-q", str(bare), str(upstream_edit)], check=True)
    _git(upstream_edit, "config", "user.email", "t@example.com")
    _git(upstream_edit, "config", "user.name", "Test")
    (upstream_edit / "existing.md").write_text("original\ndependency-added-section\n")
    _git(upstream_edit, "add", "-A")
    _git(upstream_edit, "commit", "-q", "-m", "TASK-001 squash-merge onto main")
    _git(upstream_edit, "push", "-q", "origin", "main")


class CarrySquashMergedDependenciesCleanCase(unittest.TestCase):
    def test_carries_dependency_file_with_no_unrelated_conflict(self):
        """Happy path: the dependency's squash-merge touches only its own
        declared file. The carry brings it into the worktree cleanly -- no
        conflict, nothing to resolve either way."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)
            _push_upstream_squash_merge(tmp, bare, dep_file_only=True)

            task, by_id = _by_id_and_task()
            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)

            live.add_stacked_worktree(
                repo, "102-x", task, by_id, wt, remote="origin", base="main"
            )

            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "clean merge leaves no lingering state")
            self.assertEqual((wt / "dep_file.py").read_text(), "dependency content\n")


class CarrySquashMergedDependenciesConflictFailsLoud(unittest.TestCase):
    def test_conflicting_content_aborts_instead_of_silently_favoring_worktree(self):
        """The dependency's squash-merge lands on the live base AND the local
        checkout independently diverged on the same shared file/line -- a
        genuine content-level conflict outside the dependency's own declared
        files. The carry must fail loud (abort the merge) rather than silently
        keep the worktree's stale content, so the caller's dependency-file
        validation stays the source of truth for "content genuinely isn't
        available" instead of a favor-one-side merge masking it.
        """
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)
            _push_upstream_squash_merge(tmp, bare, dep_file_only=False)

            # `repo`'s local checkout stays behind and picks up its OWN
            # genuinely different, never-pushed edit to the same shared
            # file/line -- this becomes the worktree's content once it
            # branches off local HEAD (the dependency's branch never existed
            # locally, so `dependency_start_ref` falls back to bare HEAD).
            (repo / "shared.py").write_text("line1\nline2-STALE-LOCAL\nline3\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "local-only stale edit")

            task, by_id = _by_id_and_task()
            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)

            live.add_stacked_worktree(
                repo, "102-x", task, by_id, wt, remote="origin", base="main"
            )

            # The merge conflicted and was aborted -- the working tree is
            # clean (no lingering conflict markers, no half-merged state) and
            # there is no MERGE_HEAD left behind.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "an aborted merge leaves a clean tree")
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                    capture_output=True,
                ).returncode,
                1,
                "no lingering MERGE_HEAD after the abort",
            )

            # The dependency's declared file genuinely did NOT get carried --
            # this is the fail-loud signal: a caller checking for it (the
            # `_require_dependency_files` backstop) still sees it missing and
            # raises, instead of the conflict being silently resolved away.
            self.assertFalse(
                (wt / "dep_file.py").exists(),
                "dependency's declared file must still be missing after an aborted carry",
            )

            # The worktree's own content is untouched by the aborted merge --
            # neither side was silently favored.
            self.assertEqual(
                (wt / "shared.py").read_text(), "line1\nline2-STALE-LOCAL\nline3\n"
            )


class CarrySquashMergedDependenciesExistingFileStaleness(unittest.TestCase):
    def test_carries_dependency_edit_to_a_file_that_already_existed(self):
        """Regression for brief 20260817-120332: a dependency's declared file
        that already existed in the repo BEFORE the dependency's own change
        (e.g. an established doc or source file, as opposed to `dep_file.py`
        above which is brand new). `_dependency_file_declared_path_exists` is
        a bare existence check, so before the fix it returns True for this
        file in every worktree regardless of which commit it forked from --
        the carry never fires, and the worktree keeps the pre-squash-merge
        content forever. This reproduced live: `full-real`'s tail dispatch
        forked task 3.1's worktree from a commit that predated the just-merged
        fan-out PRs, and 3.1's dependency (2.4) declared exactly this shape of
        file (`skills/worktrail-go/references/ci-watch-loop.md`, edited, not
        created)."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)

            # `existing.md` is present in the repo BEFORE the dependency's
            # squash-merge -- this is the shape the existence check can't
            # distinguish from "already fresh".
            (repo / "existing.md").write_text("original\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "add existing.md")
            _git(repo, "push", "-q", "origin", "main")

            _push_upstream_edit_to_existing_file(tmp, bare)

            task = {"id": "TASK-002", "deps": ["TASK-001"]}
            by_id = {
                "TASK-001": {"id": "TASK-001", "status": "completed", "files": ["existing.md"]},
                "TASK-002": task,
            }
            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)

            # `repo`'s local checkout never fetched the dependency's
            # squash-merge -- `dependency_start_ref` falls back to bare HEAD,
            # which still has the pre-edit `existing.md` content.
            live.add_stacked_worktree(
                repo, "102-x", task, by_id, wt, remote="origin", base="main"
            )

            self.assertEqual(
                (wt / "existing.md").read_text(),
                "original\ndependency-added-section\n",
                "the dependency's edit to a pre-existing file must be carried into "
                "the worktree, not silently skipped because the path already existed",
            )


if __name__ == "__main__":
    unittest.main()
