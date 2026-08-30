#!/usr/bin/env python3
"""Unit tests for the cross-repo live-activity view (stdlib unittest)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router import live_status as ls


def _mkproc(
    proc_root: Path, pid: int, argv: list, ppid: int = 1, mtime: float = 1000.0
) -> None:
    d = proc_root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(("\0".join(argv) + "\0").encode())
    (d / "status").write_text(f"Name:\tproc{pid}\nPPid:\t{ppid}\n")
    import os

    os.utime(d, (mtime, mtime))


class TestListProcs(unittest.TestCase):
    def test_non_proc_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(ls.list_procs(Path(t) / "does-not-exist"), [])

    def test_parses_cmdline_and_ppid(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mkproc(root, 100, ["python3", "live.py", "full-real"], ppid=1)
            procs = ls.list_procs(root)
            self.assertEqual(len(procs), 1)
            self.assertEqual(procs[0]["pid"], 100)
            self.assertEqual(procs[0]["ppid"], 1)
            self.assertEqual(procs[0]["argv"][:2], ["python3", "live.py"])

    def test_non_numeric_entries_and_empty_cmdline_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "self").mkdir()  # non-pid /proc entry
            empty = root / "200"
            empty.mkdir()
            (empty / "cmdline").write_bytes(b"")  # kernel thread, no argv
            (empty / "status").write_text("Name:\tkthread\nPPid:\t2\n")
            self.assertEqual(ls.list_procs(root), [])


class TestFindLiveRuns(unittest.TestCase):
    def test_finds_full_real_and_counts_worker_children(self):
        procs = [
            {
                "pid": 100,
                "ppid": 1,
                "argv": [
                    "python3",
                    ".../live.py",
                    "full-real",
                    "--repo",
                    "/home/u/projects/app",
                    "--spec",
                    "docs/specs/007-x",
                ],
                "start_time": 1000.0,
            },
            {
                "pid": 101,
                "ppid": 100,
                "argv": ["claude", "-p", "..."],
                "start_time": 1005.0,
            },
            {
                "pid": 102,
                "ppid": 100,
                "argv": ["claude", "-p", "..."],
                "start_time": 1006.0,
            },
            {
                "pid": 103,
                "ppid": 999,
                "argv": ["claude", "-p", "unrelated"],
                "start_time": 1007.0,
            },
            {
                "pid": 104,
                "ppid": 1,
                "argv": ["bash", "-c", "echo hi"],
                "start_time": 1008.0,
            },
        ]
        runs = ls.find_live_runs(procs)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["repo"], "/home/u/projects/app")
        self.assertEqual(runs[0]["spec"], "docs/specs/007-x")
        self.assertEqual(runs[0]["active_workers"], 2)

    def test_non_full_real_live_py_invocation_ignored(self):
        procs = [
            {
                "pid": 100,
                "ppid": 1,
                "argv": ["python3", ".../live.py", "smoke"],
                "start_time": 1.0,
            }
        ]
        self.assertEqual(ls.find_live_runs(procs), [])

    def test_unrelated_process_ignored(self):
        procs = [{"pid": 100, "ppid": 1, "argv": ["bash"], "start_time": 1.0}]
        self.assertEqual(ls.find_live_runs(procs), [])


class TestScanLocksAndCompute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.repos = self.root / "projects"
        self.repos.mkdir()
        self.repo = self.repos / "app"
        self.repo.mkdir()
        self.worktrees = self.repos / "app-worktrees"
        self.worktrees.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_scan_locks_reports_only_held_locks(self):
        (self.worktrees / "run-a.lock").write_text("")
        nested = self.worktrees / "007-x-worktrees"
        nested.mkdir()
        (nested / "run-b.lock").write_text("")

        held_paths = {str(nested / "run-b.lock")}
        result = ls.scan_locks(self.repos, lambda p: str(p) in held_paths)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["lock"], str(nested / "run-b.lock"))

    def test_compute_flags_orphaned_lock_with_no_matching_run(self):
        (self.worktrees / "run-a.lock").write_text("")
        result = ls.compute(self.repos, Path(self.root / "no-proc"), lambda _p: True)
        self.assertEqual(result["runs"], [])
        self.assertEqual(len(result["held_locks"]), 1)
        self.assertEqual(len(result["orphaned_locks"]), 1)

    def test_compute_matched_run_not_orphaned(self):
        proc_root = self.root / "proc"
        _mkproc(
            proc_root,
            100,
            [
                "python3",
                "live.py",
                "full-real",
                "--repo",
                str(self.repo),
                "--spec",
                "docs/specs/007-x",
            ],
        )
        (self.worktrees / "run-x.lock").write_text("")
        result = ls.compute(self.repos, proc_root, lambda _p: True)
        self.assertEqual(len(result["runs"]), 1)
        self.assertEqual(len(result["held_locks"]), 1)
        self.assertEqual(result["orphaned_locks"], [])


class TestRender(unittest.TestCase):
    def test_no_activity(self):
        self.assertEqual(
            ls.render({"runs": [], "held_locks": [], "orphaned_locks": []}),
            "No live orchestrator runs found.",
        )

    def test_renders_run_and_orphaned_lock(self):
        import time

        result = {
            "runs": [
                {
                    "pid": 100,
                    "repo": "/home/u/projects/app",
                    "spec": "docs/specs/007-x",
                    "start_time": time.time() - 300,
                    "active_workers": 2,
                }
            ],
            "held_locks": [],
            "orphaned_locks": [
                {"repo": "/home/u/projects/other", "lock": "/x/run-y.lock"}
            ],
        }
        out = ls.render(result)
        self.assertIn("app", out)
        self.assertIn("docs/specs/007-x", out)
        self.assertIn("2 active worker(s)", out)
        self.assertIn("no matching process", out)


if __name__ == "__main__":
    unittest.main()
