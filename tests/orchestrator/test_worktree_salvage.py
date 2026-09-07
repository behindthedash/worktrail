#!/usr/bin/env python3
"""Salvage of uncommitted task-worktree work during teardown."""

import io
import os
import sys
import unittest
from collections import namedtuple
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import worktree

P = namedtuple("P", "returncode stdout stderr")


class RecordingRunner:
    """Injected runner: records commands, replies from a status/failure script."""

    def __init__(self, status_out="", fail_on=None):
        self.cmds = []
        self.status_out = status_out
        self.fail_on = fail_on or ()

    def __call__(self, cmd):
        self.cmds.append(cmd)
        joined = " ".join(cmd)
        if any(f in joined for f in self.fail_on):
            return P(1, "", "boom")
        if "status" in cmd:
            return P(0, self.status_out, "")
        return P(0, "", "")


def _wm(runner):
    return worktree.WorktreeManager(
        repo_root=Path("/repos/app"),
        spec_id="001-test",
        worktree_base=Path("/wt"),
        runner=runner,
        dry_run=False,
    )


def _removals(runner):
    return [c for c in runner.cmds if c[3:5] == ["worktree", "remove"]]


class SalvageTests(unittest.TestCase):
    def test_clean_worktree_has_no_salvage_commit_or_log(self):
        runner = RecordingRunner(status_out="")
        wm = _wm(runner)
        with redirect_stdout(io.StringIO()):
            wm.remove("TASK-001")
        self.assertFalse([c for c in runner.cmds if "commit" in c])
        self.assertFalse([c for c in runner.cmds if c[-1] == "-u"])
        self.assertFalse([entry for entry in wm.log if entry.startswith("salvage:")])
        self.assertEqual(len(_removals(runner)), 1)

    def test_dirty_worktree_commits_before_removal(self):
        runner = RecordingRunner(status_out=" M src/app.py\n")
        wm = _wm(runner)
        with redirect_stdout(io.StringIO()):
            wm.remove("TASK-001")
        kinds = [c[3] for c in runner.cmds]
        self.assertEqual(kinds, ["status", "add", "commit", "worktree"])
        status_cmd = runner.cmds[0]
        self.assertIn("--untracked-files=no", status_cmd)
        self.assertEqual(status_cmd[1:3], ["-C", str(Path("/wt/001-test-task-001"))])
        self.assertEqual(runner.cmds[1][3:], ["add", "-u"])
        self.assertIn("TASK-001", runner.cmds[2][-1])

    def test_salvage_log_line_names_task_and_branch(self):
        runner = RecordingRunner(status_out=" M src/app.py\n")
        wm = _wm(runner)
        out = io.StringIO()
        with redirect_stdout(out):
            wm.remove("TASK-001")
        notes = [entry for entry in wm.log if entry.startswith("salvage:")]
        self.assertEqual(len(notes), 1)
        self.assertIn("TASK-001", notes[0])
        self.assertIn("001-test/task-001", notes[0])
        self.assertIn("001-test/task-001", out.getvalue())

    def test_salvage_failure_still_issues_removal(self):
        runner = RecordingRunner(status_out=" M src/app.py\n", fail_on=("commit",))
        wm = _wm(runner)
        with redirect_stdout(io.StringIO()):
            wm.remove("TASK-001")
        self.assertEqual(len(_removals(runner)), 1)
        self.assertTrue(
            any(entry.startswith("salvage: failed") for entry in wm.log)
        )

    def test_status_failure_still_issues_removal(self):
        runner = RecordingRunner(fail_on=("status",))
        wm = _wm(runner)
        with redirect_stdout(io.StringIO()):
            wm.remove("TASK-001")
        self.assertEqual(len(_removals(runner)), 1)

    def test_removal_failure_still_raises_worktree_error(self):
        runner = RecordingRunner(status_out=" M src/app.py\n", fail_on=("worktree",))
        wm = _wm(runner)
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(worktree.WorktreeError):
                wm.remove("TASK-001")

    def test_dry_run_issues_no_salvage_effects(self):
        wm = worktree.WorktreeManager(
            repo_root=Path("/repos/app"),
            spec_id="001-test",
            worktree_base=Path("/wt"),
            dry_run=True,
        )
        with redirect_stdout(io.StringIO()):
            wm.remove("TASK-001")
        self.assertFalse([entry for entry in wm.log if "commit" in entry])
        self.assertFalse([entry for entry in wm.log if entry.startswith("salvage:")])


if __name__ == "__main__":
    unittest.main()
