#!/usr/bin/env python3
"""Regression coverage for brief 20260901-175031: a dependency whose task branch
SURVIVES after its group PR squash-merged into base.

`dependency_start_ref` used to stack the dependent on that surviving branch,
so a re-created worktree forked from a pre-squash tip that base does not
descend from -- failing the retained-branch ancestry guard
(`WorktreeAddError '<dep> is not an ancestor'`) or the base carry
(`WorktreeMissingDependencyFileError 'squash-merge carry ... failed'`) and
unrecoverable by clear-task / worktree deletion / --re-integrate (live repro:
managed-codex-probe-contract 4.3, run go-20260831-153221).

With `base_ref` given, a dependency whose content is already in base is
satisfied by base content: the dependent forks from base itself.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live

SPEC = "spec-x"


def _git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=check
    )


def _commit(repo, name, content, msg):
    (repo / name).write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def _init(tmp):
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
    _commit(repo, "base.txt", "base\n", "base")
    _git(repo, "remote", "add", "origin", str(bare))
    _git(repo, "push", "-q", "-u", "origin", "main")
    return bare, repo


def _squash_merge_dep_branch_upstream(tmp, bare, repo, dep_branch):
    """Land `dep_branch`'s content on origin/main as a SQUASH commit (no
    ancestry to the branch tip), plus an unrelated follow-up commit so base
    is strictly ahead. The local `repo` keeps its stale `main`."""
    clone = Path(tmp) / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "fetch", "-q", str(repo), f"{dep_branch}:{dep_branch}")
    _git(clone, "merge", "--squash", "-q", dep_branch)
    _git(clone, "commit", "-q", "-m", f"squash {dep_branch}")
    _commit(clone, "other.txt", "landed later\n", "unrelated follow-up")
    _git(clone, "push", "-q", "origin", "main")


def _squash_merge_reviewed_dep_branch_upstream(tmp, bare, repo, dep_branch, path):
    """Land `dep_branch` on origin/main as a SQUASH whose content was REVISED
    on the group branch first (a review fixup), plus an unrelated follow-up.
    The retained task branch's verbatim diff therefore never appears in base:
    a three-way merge of the branch into base CONFLICTS on `path` (brief
    20260905-162859, live: shared-pr-landing-pipeline/{2.1,5.1,14.1,17.1})."""
    clone = Path(tmp) / "clone"
    subprocess.run(["git", "clone", "-q", str(bare), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@example.com")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "fetch", "-q", str(repo), f"{dep_branch}:{dep_branch}")
    _git(clone, "merge", "--squash", "-q", dep_branch)
    (clone / path).write_text("dep content, reviewed\n")
    _git(clone, "add", "-A")
    _git(clone, "commit", "-q", "-m", f"squash {dep_branch} + review fixup")
    _commit(clone, "other.txt", "landed later\n", "unrelated follow-up")
    _git(clone, "push", "-q", "origin", "main")


class SquashMergedSurvivingDependencyBranch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self.bare, self.repo = _init(tmp)
        self.dep_branch = f"{SPEC}/2.1"
        _git(self.repo, "checkout", "-q", "-b", self.dep_branch)
        _commit(self.repo, "dep.py", "dep content\n", "2.1 work")
        _git(self.repo, "checkout", "-q", "main")
        _squash_merge_dep_branch_upstream(tmp, self.bare, self.repo, self.dep_branch)
        self.by_id = {
            "2.1": {"id": "2.1", "status": "done", "files": ["dep.py"]},
            "4.3": {"id": "4.3", "deps": ["2.1"], "files": ["new.py"]},
        }
        self.task = self.by_id["4.3"]
        self.wt = Path(tmp) / "wt-4.3"

    def tearDown(self):
        self._tmp.cleanup()

    def test_branch_content_in_base_detects_squash(self):
        _git(self.repo, "fetch", "-q", "origin", "main")
        self.assertFalse(live._is_ancestor(self.repo, self.dep_branch, "origin/main"))
        self.assertTrue(
            live._branch_content_in_base(self.repo, self.dep_branch, "origin/main")
        )
        # An unmerged branch is NOT reported as in base.
        _git(self.repo, "checkout", "-q", "-b", f"{SPEC}/9.9", "main")
        _commit(self.repo, "nine.py", "unmerged\n", "9.9 work")
        _git(self.repo, "checkout", "-q", "main")
        self.assertFalse(
            live._branch_content_in_base(self.repo, f"{SPEC}/9.9", "origin/main")
        )

    def test_start_ref_prefers_base_over_squash_merged_branch(self):
        _git(self.repo, "fetch", "-q", "origin", "main")
        start, extra = live.dependency_start_ref(
            self.repo, SPEC, self.task, self.by_id, base_ref="origin/main"
        )
        self.assertEqual((start, extra), ("origin/main", []))
        # Without base_ref the legacy ancestry-stacking answer is unchanged.
        start, _ = live.dependency_start_ref(self.repo, SPEC, self.task, self.by_id)
        self.assertEqual(start, self.dep_branch)

    def test_fresh_worktree_forks_from_base(self):
        live.add_stacked_worktree(
            self.repo,
            SPEC,
            self.task,
            self.by_id,
            self.wt,
            remote="origin",
            base="main",
        )
        self.assertTrue((self.wt / "dep.py").exists())
        self.assertTrue((self.wt / "other.txt").exists())
        self.assertTrue(live._is_ancestor(self.wt, "origin/main", "HEAD"))

    def test_retained_branch_reset_to_base_passes_ancestry_guard(self):
        # The only manual recovery that worked live: task branch reset to base.
        _git(self.repo, "fetch", "-q", "origin", "main")
        _git(self.repo, "branch", f"{SPEC}/4.3", "origin/main")
        live.add_stacked_worktree(
            self.repo,
            SPEC,
            self.task,
            self.by_id,
            self.wt,
            remote="origin",
            base="main",
        )
        self.assertEqual(
            _git(self.wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(),
            f"{SPEC}/4.3",
        )
        self.assertTrue((self.wt / "dep.py").exists())


class ReviewedSquashConflictingDependencyBranch(unittest.TestCase):
    """The dependency branch's content landed in base only in a REVISED form
    (group-level review fixups squashed with it), so the branch is neither an
    ancestor of base nor cleanly re-mergeable into it. Stacking on it is
    guaranteed to fail the base carry; the dependent must fork from base."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = self._tmp.name
        self.bare, self.repo = _init(tmp)
        self.dep_branch = f"{SPEC}/2.1"
        _git(self.repo, "checkout", "-q", "-b", self.dep_branch)
        _commit(self.repo, "dep.py", "dep content\n", "2.1 work")
        _git(self.repo, "checkout", "-q", "main")
        _squash_merge_reviewed_dep_branch_upstream(
            tmp, self.bare, self.repo, self.dep_branch, "dep.py"
        )
        _git(self.repo, "fetch", "-q", "origin", "main")
        self.by_id = {
            "2.1": {"id": "2.1", "status": "done", "files": ["dep.py"]},
            "4.3": {"id": "4.3", "deps": ["2.1"], "files": ["new.py"]},
        }
        self.task = self.by_id["4.3"]
        self.wt = Path(tmp) / "wt-4.3"

    def tearDown(self):
        self._tmp.cleanup()

    def test_merge_tree_conflicts_for_the_fixture(self):
        # Fixture sanity: this is the live shape -- not an ancestor, and the
        # three-way merge into base conflicts (rc=1) rather than yielding base.
        self.assertFalse(live._is_ancestor(self.repo, self.dep_branch, "origin/main"))
        mt = _git(
            self.repo,
            "merge-tree",
            "--write-tree",
            "origin/main",
            self.dep_branch,
            check=False,
        )
        self.assertEqual(mt.returncode, 1, mt.stdout + mt.stderr)

    def test_branch_content_in_base_detects_reviewed_squash(self):
        self.assertTrue(
            live._branch_content_in_base(self.repo, self.dep_branch, "origin/main")
        )

    def test_start_ref_prefers_base_over_conflicting_retained_branch(self):
        start, extra = live.dependency_start_ref(
            self.repo, SPEC, self.task, self.by_id, base_ref="origin/main"
        )
        self.assertEqual((start, extra), ("origin/main", []))

    def test_fresh_worktree_forks_from_base_and_reports_stale_branch(self):
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            live.add_stacked_worktree(
                self.repo,
                SPEC,
                self.task,
                self.by_id,
                self.wt,
                remote="origin",
                base="main",
            )
        self.assertEqual((self.wt / "dep.py").read_text(), "dep content, reviewed\n")
        self.assertTrue(live._is_ancestor(self.wt, "origin/main", "HEAD"))
        out = buf.getvalue()
        self.assertIn(self.dep_branch, out)
        self.assertIn("git branch -D", out)


if __name__ == "__main__":
    unittest.main()
