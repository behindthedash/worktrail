#!/usr/bin/env python3
"""Regression tests for per-task worktree dependency bootstrap.

Task worktrees branch off the base commit and start WITHOUT the base checkout's
gitignored install output (node_modules). Before this, every fanned-out
implement/fix/review worker rediscovered the missing install and ran it itself
mid-task. `live.bootstrap_worktree` installs deps into the fresh worktree right
after `git worktree add`, before the worker is spawned, driven by the opt-in
go-policy.yaml `worktree_bootstrap_cmd` (threaded as `live.py full-real
--bootstrap-cmd`).

These tests guard three things that must not silently break:
  1. the helper's contract (skip on None, run-in-worktree on success, and an
     explicit required-mode failure before worker spawn);
  2. that `--bootstrap-cmd` parses into `args.bootstrap_cmd`; and
  3. that every driver on the fan-out path accepts a `bootstrap_cmd` kwarg, so
     the CLI -> full_real -> _full_real_inner -> scheduler threading can't rot.

Run:
    python3 scripts/test_worktree_bootstrap.py
"""

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402


class BootstrapHelperTest(unittest.TestCase):
    def _tmp_wt(self) -> Path:
        d = Path(self._tmp.name) / "025-feature-task-001"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_none_is_skipped(self) -> None:
        wt = self._tmp_wt()
        logs: list = []
        self.assertFalse(live.bootstrap_worktree(wt, None, log=logs.append))
        self.assertFalse(live.bootstrap_worktree(wt, "", log=logs.append))
        # Nothing ran, nothing logged.
        self.assertEqual(logs, [])

    def test_runs_in_the_worktree_on_success(self) -> None:
        wt = self._tmp_wt()
        # A command whose side effect proves both that it ran AND that cwd is the
        # worktree (the sentinel lands relative to cwd, not the test's cwd).
        ok = live.bootstrap_worktree(wt, "touch .bootstrapped", log=lambda _m: None)
        self.assertTrue(ok)
        self.assertTrue((wt / ".bootstrapped").exists())

    def test_failure_is_non_fatal(self) -> None:
        wt = self._tmp_wt()
        logs: list = []
        # Non-zero exit must NOT raise (the worker self-recovers); returns False
        # and logs loudly.
        result = live.bootstrap_worktree(wt, "exit 7", log=logs.append)
        self.assertFalse(result)
        self.assertTrue(any("failed" in m for m in logs))

    def test_required_failure_stops_before_worker_spawn(self) -> None:
        wt = self._tmp_wt()
        with self.assertRaises(live.WorktreeAddError):
            live.bootstrap_worktree(wt, "exit 7", log=lambda _m: None, required=True)


class ThreadingContractTest(unittest.TestCase):
    def test_bootstrap_cmd_flag_is_wired(self) -> None:
        # The `full-real` subparser must expose `--bootstrap-cmd` and the dispatch
        # must thread `args.bootstrap_cmd` into `full_real` — otherwise the policy
        # value the conductor passes is silently dropped.
        src = Path(live.__file__).read_text()
        self.assertIn('"--bootstrap-cmd"', src)
        self.assertIn('dest="bootstrap_cmd"', src)
        self.assertIn("bootstrap_cmd=args.bootstrap_cmd", src)

    def test_every_fanout_driver_accepts_bootstrap_cmd(self) -> None:
        for fn_name in (
            "full_real",
            "_full_real_inner",
            "_pipeline_scheduler",
            "live_run_real",
            "bootstrap_worktree",
        ):
            fn = getattr(live, fn_name)
            params = inspect.signature(fn).parameters
            if fn_name == "bootstrap_worktree":
                self.assertIn("bootstrap_cmd", params)
            else:
                self.assertIn(
                    "bootstrap_cmd", params, f"{fn_name} lost its bootstrap_cmd kwarg"
                )


if __name__ == "__main__":
    unittest.main()
