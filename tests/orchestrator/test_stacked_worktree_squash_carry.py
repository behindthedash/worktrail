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
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live


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
        (upstream_edit / "shared.py").write_text(
            "line1\nline2-from-dependency\nline3\n"
        )
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
            self.assertEqual(
                status.strip(), "", "clean merge leaves no lingering state"
            )
            self.assertEqual((wt / "dep_file.py").read_text(), "dependency content\n")


class CarrySquashMergedDependenciesConflictFailsLoud(unittest.TestCase):
    def test_conflicting_content_aborts_and_raises_instead_of_leaving_stale_state(
        self,
    ):
        """The dependency's squash-merge lands on the live base AND the local
        checkout independently diverged on the same shared file/line -- a
        genuine content-level conflict outside the dependency's own declared
        files. The carry must fail loud: abort the merge AND raise, instead of
        silently keeping the worktree's stale content and letting the run
        continue on it (brief 20260831-084532 -- a squash-merge boundary
        discards the dependency's original commit ancestry, so no downstream
        check can reliably tell this worktree apart from a genuinely fresh one
        once the carry has already given up).

        `add_stacked_worktree`'s own embedded carry call retries once
        (brief 20260822-115008, gap 2 -- see
        `test_fresh_worktree_dependency_repair.py`) before raising for real:
        it is the definitive attempt, not `_require_dependency_files_with_repair`,
        because that repair path can only detect and retry a MISSING
        declared path -- a no-op backstop for a dependency whose file already
        existed before its own edit (this test's `shared.py`).
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

            with self.assertRaises(live.WorktreeMissingDependencyFileError):
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
                    check=False,
                    capture_output=True,
                ).returncode,
                1,
                "no lingering MERGE_HEAD after the abort",
            )

            # The dependency's declared file genuinely did NOT get carried.
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
                "TASK-001": {
                    "id": "TASK-001",
                    "status": "completed",
                    "files": ["existing.md"],
                },
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


def _push_upstream_tasks_md(
    tmp, bare, content: str, *, also_touch_shared: str | None = None
):
    """Simulate a squash-merged group independently adding
    `openspec/changes/102-x/tasks.md` (from a clone with no local knowledge of
    the worktree's own addition of the same path -- squash-merge history
    losing the common ancestor is exactly what makes this an add/add
    conflict, not a clean 3-way merge)."""
    upstream_edit = Path(tmp) / "upstream_edit"
    subprocess.run(["git", "clone", "-q", str(bare), str(upstream_edit)], check=True)
    _git(upstream_edit, "config", "user.email", "t@example.com")
    _git(upstream_edit, "config", "user.name", "Test")
    tasks_dir = upstream_edit / "openspec" / "changes" / "102-x"
    tasks_dir.mkdir(parents=True)
    (tasks_dir / "tasks.md").write_text(content)
    if also_touch_shared is not None:
        (upstream_edit / "shared.py").write_text(also_touch_shared)
    _git(upstream_edit, "add", "-A")
    _git(upstream_edit, "commit", "-q", "-m", "group squash-merge onto main")
    _git(upstream_edit, "push", "-q", "origin", "main")


class TasksMdChecklistConflictResolvesViaUnion(unittest.TestCase):
    def test_conflict_confined_to_tasks_md_resolves_via_checked_union(self):
        """Both sides independently 'add' the change's own tasks.md (squash-merge
        lost the common ancestor), each checking off a different subset of the
        same task lines. The carry must resolve this deterministically by
        taking the union of checked boxes, rather than aborting."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)

            task, by_id = _by_id_and_task()
            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)
            _git(repo, "worktree", "add", "-b", "102-x/task-002", str(wt), "main")
            wt_tasks_dir = wt / "openspec" / "changes" / "102-x"
            wt_tasks_dir.mkdir(parents=True)
            (wt_tasks_dir / "tasks.md").write_text(
                "## 1. Group A\n\n- [x] 1.1 foo\n- [ ] 1.2 bar\n"
            )
            _git(wt, "add", "-A")
            _git(wt, "commit", "-q", "-m", "TASK-002 checks off 1.1")

            _push_upstream_tasks_md(
                tmp, bare, "## 1. Group A\n\n- [ ] 1.1 foo\n- [x] 1.2 bar\n"
            )

            event = live._carry_squash_merged_dependencies(
                repo, "102-x", task, by_id, wt, "origin", "main"
            )

            self.assertEqual(
                event,
                {
                    "event": "checklist_conflict_resolved",
                    "task": "TASK-002",
                    "at": mock.ANY,
                },
                "resolving the checklist-union exception must report a structured event",
            )
            self.assertEqual(
                (wt_tasks_dir / "tasks.md").read_text(),
                "## 1. Group A\n\n- [x] 1.1 foo\n- [x] 1.2 bar\n",
                "the union of checked boxes from both sides must be kept",
            )
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(
                status.strip(), "", "the resolved merge leaves a clean tree"
            )
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                    check=False,
                    capture_output=True,
                ).returncode,
                1,
                "no lingering MERGE_HEAD after the merge commits",
            )


class TasksMdConflictPlusOtherFileFailsLoud(unittest.TestCase):
    def test_conflict_touching_tasks_md_and_another_file_still_aborts(self):
        """The checklist-union exception is confined to a conflict touching
        ONLY tasks.md. If the same squash-merge also conflicts on another
        file, the whole merge must still abort and fail loud -- exactly the
        general case -- rather than applying the union resolution to
        tasks.md while some other conflict is silently left unresolved."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)

            task, by_id = _by_id_and_task()
            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)
            _git(repo, "worktree", "add", "-b", "102-x/task-002", str(wt), "main")
            wt_tasks_dir = wt / "openspec" / "changes" / "102-x"
            wt_tasks_dir.mkdir(parents=True)
            (wt_tasks_dir / "tasks.md").write_text(
                "## 1. Group A\n\n- [x] 1.1 foo\n- [ ] 1.2 bar\n"
            )
            (wt / "shared.py").write_text("line1\nline2-STALE-LOCAL\nline3\n")
            _git(wt, "add", "-A")
            _git(
                wt, "commit", "-q", "-m", "TASK-002 checks off 1.1 and edits shared.py"
            )

            _push_upstream_tasks_md(
                tmp,
                bare,
                "## 1. Group A\n\n- [ ] 1.1 foo\n- [x] 1.2 bar\n",
                also_touch_shared="line1\nline2-from-dependency\nline3\n",
            )

            with self.assertRaises(live.WorktreeMissingDependencyFileError):
                live._carry_squash_merged_dependencies(
                    repo, "102-x", task, by_id, wt, "origin", "main"
                )

            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "an aborted merge leaves a clean tree")
            self.assertEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                    check=False,
                    capture_output=True,
                ).returncode,
                1,
                "no lingering MERGE_HEAD after the abort",
            )
            # Neither side was applied -- tasks.md keeps the worktree's own
            # pre-merge content, not the union, since the whole merge aborted.
            self.assertEqual(
                (wt_tasks_dir / "tasks.md").read_text(),
                "## 1. Group A\n\n- [x] 1.1 foo\n- [ ] 1.2 bar\n",
            )
            self.assertEqual(
                (wt / "shared.py").read_text(), "line1\nline2-STALE-LOCAL\nline3\n"
            )


class ConflictedCarryOnPreExistingFileRaisesInsteadOfLeavingStaleState(
    unittest.TestCase
):
    def test_add_stacked_worktree_raises_when_carry_conflicts_on_pre_existing_file(
        self,
    ):
        """Brief 20260831-084532: when the carry's merge fails for a
        dependency whose declared file already existed before the
        dependency's own edit (as opposed to a newly-created file like
        `dep_file.py` in `ConflictFailsLoud` above), the worktree is left with
        the *pre-existing* path still present -- just holding stale content,
        not the dependency's edit. Path existence alone can never distinguish
        this stale-but-present file from a genuinely fresh one (the same
        defect class already fixed for `_carry_squash_merged_dependencies`'s
        own gate and `integrate_one`'s branch-gone fallback), so the carry
        failure itself must raise -- exactly the live incident reported in
        the brief (WARN logged for a base group's squash-merge carry, run
        continued on stale state, downstream tasks cascaded into
        merge-conflict quarantine 16 tasks later).

        `add_stacked_worktree` retries the carry once internally (brief
        20260822-115008, gap 2) before raising for real -- it is the
        definitive attempt for this shape, since the caller's follow-up
        `_require_dependency_files_with_repair` can only detect and retry a
        MISSING declared path, a no-op backstop for a file that already
        existed before the dependency's own edit (see
        `test_fresh_worktree_dependency_repair.py`)."""
        with tempfile.TemporaryDirectory() as tmp:
            bare, repo = _init_bare_and_repo(tmp)

            (repo / "existing.md").write_text("original\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "add existing.md")
            _git(repo, "push", "-q", "origin", "main")

            upstream_edit = Path(tmp) / "upstream_edit"
            subprocess.run(
                ["git", "clone", "-q", str(bare), str(upstream_edit)], check=True
            )
            _git(upstream_edit, "config", "user.email", "t@example.com")
            _git(upstream_edit, "config", "user.name", "Test")
            (upstream_edit / "existing.md").write_text(
                "original\ndependency-added-section\n"
            )
            _git(upstream_edit, "add", "-A")
            _git(upstream_edit, "commit", "-q", "-m", "TASK-001 squash-merge onto main")
            _git(upstream_edit, "push", "-q", "origin", "main")

            # `repo`'s local checkout independently diverges on the SAME
            # existing file/region -- forces the carry's merge to conflict
            # and abort instead of cleanly landing the dependency's edit.
            (repo / "existing.md").write_text("original\nlocal-independent-edit\n")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-q", "-m", "local-only stale edit")

            task = {"id": "TASK-002", "deps": ["TASK-001"]}
            by_id = {
                "TASK-001": {
                    "id": "TASK-001",
                    "status": "completed",
                    "files": ["existing.md"],
                },
                "TASK-002": task,
            }
            wt = Path(tmp) / "wt" / "102-x-task-002"
            wt.parent.mkdir(parents=True)

            with self.assertRaises(live.WorktreeMissingDependencyFileError):
                live.add_stacked_worktree(
                    repo, "102-x", task, by_id, wt, remote="origin", base="main"
                )

            # The carry conflicted and aborted -- the worktree keeps its own
            # (stale, pre-dependency) content at the declared path, and the
            # merge left no lingering half-applied state.
            self.assertEqual(
                (wt / "existing.md").read_text(),
                "original\nlocal-independent-edit\n",
            )
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "an aborted merge leaves a clean tree")


if __name__ == "__main__":
    unittest.main()
