#!/usr/bin/env python3
"""Tests for live._refresh_base_branch / live._resume_drift_report.

brief 20260723-171500-orchestrator-long-run-base-refresh: a long-running run
forks task branches once from `base` and never refreshes them. These two
helpers (1) fast-forward the local `base` ref against `<remote>/<base>`
before integration starts, ff-only and never touching a diverged local base,
and (2) print a resume-time drift report (informational only). Real git
repos are used (cheap, deterministic) rather than mocking subprocess, since
the behavior under test IS git ref plumbing.

Run: python3 test_base_refresh.py
"""

import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)


def _init_bare_and_clone(tmp: Path):
    """Create a bare 'origin' repo plus a local clone on branch 'main'."""
    origin = tmp / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    local = tmp / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True, capture_output=True)
    _run(local, "config", "user.email", "t@t")
    _run(local, "config", "user.name", "T")
    (local / "README.md").write_text("init\n")
    _run(local, "add", "-A")
    _run(local, "commit", "-q", "-m", "init")
    _run(local, "branch", "-M", "main")
    _run(local, "push", "-q", "-u", "origin", "main")
    return origin, local


def _push_extra_commit_from_a_second_clone(origin: Path, tmp: Path, n: int = 1) -> None:
    """Simulate another session pushing N commits to origin/main that the
    `local` clone under test has not fetched yet."""
    other = tmp / "other"
    subprocess.run(["git", "clone", "-q", str(origin), str(other)], check=True, capture_output=True)
    _run(other, "config", "user.email", "o@o")
    _run(other, "config", "user.name", "O")
    # The bare origin's HEAD symref may still point at git's init.defaultBranch
    # (e.g. "master") rather than "main" even after "main" was pushed to it --
    # `git push` never moves the remote's HEAD symref. Explicitly track "main"
    # rather than trusting the clone's auto-checked-out branch.
    _run(other, "checkout", "-B", "main", "origin/main")
    for i in range(n):
        (other / f"upstream-{i}.txt").write_text(f"{i}\n")
        _run(other, "add", "-A")
        _run(other, "commit", "-q", "-m", f"upstream commit {i}")
    _run(other, "push", "-q", "origin", "main")


class RefreshBaseBranchFastForward(unittest.TestCase):
    def test_fast_forwards_local_ref_when_remote_ahead(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            origin, local = _init_bare_and_clone(tmp)
            _push_extra_commit_from_a_second_clone(origin, tmp, n=2)

            before = _run(local, "rev-parse", "main").stdout.strip()
            out = io.StringIO()
            with redirect_stdout(out):
                live._refresh_base_branch(local, "origin", "main")
            after = _run(local, "rev-parse", "main").stdout.strip()

            self.assertNotEqual(before, after, "local main should have fast-forwarded")
            remote_tip = _run(local, "rev-parse", "origin/main").stdout.strip()
            self.assertEqual(after, remote_tip)
            self.assertIn("BASE REFRESH:", out.getvalue())
            self.assertIn(before[:8], out.getvalue())
            self.assertIn(after[:8], out.getvalue())

    def test_noop_when_already_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            _origin, local = _init_bare_and_clone(tmp)

            before = _run(local, "rev-parse", "main").stdout.strip()
            out = io.StringIO()
            with redirect_stdout(out):
                live._refresh_base_branch(local, "origin", "main")
            after = _run(local, "rev-parse", "main").stdout.strip()

            self.assertEqual(before, after)
            self.assertNotIn("->", out.getvalue())


class RefreshBaseBranchDivergedAbortsCleanly(unittest.TestCase):
    def test_diverged_local_base_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            origin, local = _init_bare_and_clone(tmp)
            _push_extra_commit_from_a_second_clone(origin, tmp, n=1)

            # Give `local` its OWN unpushed commit so main has diverged from
            # origin/main (neither is an ancestor of the other).
            (local / "local-only.txt").write_text("mine\n")
            _run(local, "add", "-A")
            _run(local, "commit", "-q", "-m", "local-only work")
            before = _run(local, "rev-parse", "main").stdout.strip()

            out = io.StringIO()
            with redirect_stdout(out):
                live._refresh_base_branch(local, "origin", "main")
            after = _run(local, "rev-parse", "main").stdout.strip()

            self.assertEqual(before, after, "a diverged local base must never be force-updated")
            self.assertIn("diverged", out.getvalue())
            self.assertIn("not fast-forwardable", out.getvalue())

    def test_fetch_failure_degrades_gracefully_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            local = tmp / "solo"
            subprocess.run(["git", "init", "-q", str(local)], check=True)
            _run(local, "config", "user.email", "t@t")
            _run(local, "config", "user.name", "T")
            (local / "README.md").write_text("init\n")
            _run(local, "add", "-A")
            _run(local, "commit", "-q", "-m", "init")
            _run(local, "branch", "-M", "main")
            # No 'origin' remote configured at all -- fetch must fail cleanly.

            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    live._refresh_base_branch(local, "origin", "main")
            except Exception as exc:  # noqa: BLE001
                self.fail(f"must never raise on a missing remote; got {exc!r}")
            self.assertIn("BASE REFRESH:", out.getvalue())
            self.assertIn("failed", out.getvalue())


class ResumeDriftReport(unittest.TestCase):
    def _repo_with_task_branch_and_drift(self, tmp: Path, drift_commits: int):
        repo = tmp / "repo"
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        _run(repo, "config", "user.email", "t@t")
        _run(repo, "config", "user.name", "T")
        (repo / "README.md").write_text("init\n")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "init")
        _run(repo, "branch", "-M", "main")

        # Fork the task branch HERE -- this commit is the "spec base" proxy.
        _run(repo, "checkout", "-q", "-b", "001-x/task-001")
        (repo / "task-001.txt").write_text("work\n")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "TASK-001 work")
        _run(repo, "checkout", "-q", "main")

        for i in range(drift_commits):
            (repo / f"base-drift-{i}.txt").write_text(f"{i}\n")
            _run(repo, "add", "-A")
            _run(repo, "commit", "-q", "-m", f"base moved on {i}")
        return repo

    def test_prints_drift_when_base_moved_since_fork(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = self._repo_with_task_branch_and_drift(tmp, drift_commits=3)

            out = io.StringIO()
            with redirect_stdout(out):
                live._resume_drift_report(repo, "main", "001-x", [{"id": "TASK-001"}])

            self.assertIn("PIPELINE RESUME: base 'main' is 3 commit(s) ahead", out.getvalue())

    def test_silent_when_base_at_fork_point(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = self._repo_with_task_branch_and_drift(tmp, drift_commits=0)

            out = io.StringIO()
            with redirect_stdout(out):
                live._resume_drift_report(repo, "main", "001-x", [{"id": "TASK-001"}])

            self.assertEqual(out.getvalue(), "")

    def test_silent_when_no_task_branch_exists_yet(self):
        with tempfile.TemporaryDirectory() as tmp_s:
            tmp = Path(tmp_s)
            repo = tmp / "repo"
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            _run(repo, "config", "user.email", "t@t")
            _run(repo, "config", "user.name", "T")
            (repo / "README.md").write_text("init\n")
            _run(repo, "add", "-A")
            _run(repo, "commit", "-q", "-m", "init")
            _run(repo, "branch", "-M", "main")

            out = io.StringIO()
            try:
                with redirect_stdout(out):
                    live._resume_drift_report(repo, "main", "001-x", [{"id": "TASK-001"}])
            except Exception as exc:  # noqa: BLE001
                self.fail(f"must never raise when no task branch exists yet; got {exc!r}")
            self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
