#!/usr/bin/env python3
"""Extra coverage for worktree.py: dry-run, CLI, error paths."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import worktree


class WorktreeManagerDryRunTests(unittest.TestCase):
    def _wm(self, **kw):
        return worktree.WorktreeManager(repo_root=Path("/repos/app"), spec_id="001-test",
                                        dry_run=True, **kw)

    def test_add_dry_run_returns_path(self):
        wm = self._wm()
        p = wm.add("TASK-001")
        self.assertIsInstance(p, Path)
        self.assertIn("task-001", str(p).lower())

    def test_remove_dry_run_does_not_raise(self):
        wm = self._wm()
        wm.remove("TASK-001")  # dry_run -> no actual git call

    def test_delete_branch_dry_run_does_not_raise(self):
        wm = self._wm()
        wm.delete_branch("TASK-001")

    def test_prune_dry_run_does_not_raise(self):
        wm = self._wm()
        wm.prune()

    def test_list_worktrees_dry_run_returns_empty(self):
        wm = self._wm()
        result = wm.list_worktrees()
        self.assertEqual(result, "")

    def test_log_accumulates_commands(self):
        wm = self._wm()
        wm.add("TASK-001")
        wm.prune()
        self.assertTrue(len(wm.log) >= 2)
        self.assertTrue(any("worktree" in cmd for cmd in wm.log))

    def test_custom_worktree_base(self):
        wm = worktree.WorktreeManager(repo_root=Path("/repos/app"), spec_id="001-test",
                                      worktree_base=Path("/custom/wt-base"), dry_run=True)
        p = wm.add("TASK-002")
        self.assertIn("/custom/wt-base", str(p))


class WorktreeFakeRunnerTests(unittest.TestCase):
    """Test real-mode paths using an injectable runner that controls returncode."""

    def _proc(self, returncode=0, stdout="", stderr=""):
        from collections import namedtuple
        P = namedtuple("P", "returncode stdout stderr")
        return P(returncode, stdout, stderr)

    def test_worktree_add_error_raises(self):
        def fail_runner(_cmd):
            return self._proc(returncode=1, stderr="not a git repo")
        wm = worktree.WorktreeManager(repo_root=Path("/repos/app"), spec_id="001-test",
                                      runner=fail_runner, dry_run=False)
        with self.assertRaises(worktree.WorktreeError):
            wm.add("TASK-001")

    def test_sandbox_denied_raises_sandbox_error(self):
        def sandbox_runner(_cmd):
            return self._proc(returncode=1, stderr="Operation not permitted (sandbox)")
        wm = worktree.WorktreeManager(repo_root=Path("/repos/app"), spec_id="001-test",
                                      runner=sandbox_runner, dry_run=False)
        with self.assertRaises(worktree.SandboxDenied):
            wm.add("TASK-001")


class WorktreeCLITests(unittest.TestCase):
    def test_demo_exits_zero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = worktree.main(["demo"])
        self.assertEqual(rc, 0)
        self.assertIn("WORKTREE", buf.getvalue().upper())


class HelperFunctionTests(unittest.TestCase):
    def test_worktree_path_structure(self):
        p = worktree.worktree_path(Path("/wt-base"), "001-spec", "TASK-042")
        self.assertIn("001-spec", str(p))
        self.assertIn("task-042", str(p).lower())

    def test_task_branch_structure(self):
        branch = worktree.task_branch("001-spec", "TASK-042")
        self.assertIn("001-spec", branch)
        self.assertIn("task-042", branch.lower())

    def test_default_worktree_base(self):
        base = worktree.default_worktree_base(Path("/repos/my-app"))
        self.assertIn("my-app", str(base))
        self.assertIn("worktrees", str(base))


class HasTaskWorktreesTests(unittest.TestCase):
    """`compile.py`'s force-vs-active-worktrees guard is built on this check."""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()

    def test_false_when_the_worktree_base_does_not_exist(self):
        self.assertFalse(worktree.has_task_worktrees(self.repo, "003-payments"))

    def test_false_when_the_base_exists_but_has_no_matching_task_dirs(self):
        base = worktree.default_worktree_base(self.repo)
        (base / "runplans").mkdir(parents=True)
        (base / "004-other-task-001").mkdir()
        self.assertFalse(worktree.has_task_worktrees(self.repo, "003-payments"))

    def test_true_when_a_matching_task_worktree_dir_exists(self):
        base = worktree.default_worktree_base(self.repo)
        base.mkdir(parents=True)
        (base / "003-payments-task-001").mkdir()
        self.assertTrue(worktree.has_task_worktrees(self.repo, "003-payments"))

    def test_a_file_with_a_matching_name_does_not_count(self):
        base = worktree.default_worktree_base(self.repo)
        base.mkdir(parents=True)
        (base / "003-payments-task-001").write_text("not a directory")
        self.assertFalse(worktree.has_task_worktrees(self.repo, "003-payments"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
