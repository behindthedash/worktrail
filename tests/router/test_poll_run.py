#!/usr/bin/env python3
"""Tests for poll_run.py. Run: python3 test_poll_run.py"""
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router.poll_run import is_finished, read_run_record, main


class TestPollRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write_record(self, filename: str, final_status=None, pull_request=None) -> str:
        """Helper to write a minimal run record YAML file."""
        path = Path(self.tmp) / filename
        lines = [
            "run_id: test-run",
            "started_at: 2026-06-15T12:00:00-0700",
            "completed_at: null",
            "repository: /tmp/test-repo",
            "base_branch: main",
            "base_commit: abc123",
            "worktree: null",
            "request_summary: test request",
            "selected_route: F",
            "route_reason: null",
            "risk_level: medium",
            f"status: {'done' if final_status else 'executing'}",
            "epic: null",
            "feature: null",
            "specification: null",
            "handoffs_consumed:",
            "handoffs_created:",
            "skills_loaded:",
            "subagents_called:",
            "files_changed:",
            "tests_run:",
            "decisions:",
            "assumptions:",
            "deferred_work:",
            "validation_evidence:",
            "failure_recovery:",
            "interventions:",
        ]

        if pull_request:
            lines.append(f"pull_request: \"{pull_request}\"")
        else:
            lines.append("pull_request: null")

        if final_status:
            lines.append(f"final_status: {final_status}")
        else:
            lines.append("final_status: null")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def test_read_run_record_with_finished_status(self):
        """Test reading a record with final_status set."""
        path = self._write_record("finished.yaml", final_status="completed_pr_open",
                                  pull_request="https://github.com/test/pr/1")
        record = read_run_record(Path(path))
        self.assertEqual(record.get("final_status"), "completed_pr_open")
        self.assertEqual(record.get("pull_request"), "https://github.com/test/pr/1")

    def test_is_finished_with_final_status(self):
        """Test is_finished returns True when final_status is not null."""
        path = self._write_record("finished.yaml", final_status="completed_pr_open")
        record = read_run_record(Path(path))
        self.assertTrue(is_finished(record))

    def test_is_finished_without_final_status(self):
        """Test is_finished returns False when final_status is null."""
        path = self._write_record("not_finished.yaml")
        record = read_run_record(Path(path))
        self.assertFalse(is_finished(record))

    def test_exit_0_on_finish_with_pr(self):
        """Test script exits 0 and prints state + PR URL when record is finished."""
        path = self._write_record("finished.yaml", final_status="completed_pr_open",
                                  pull_request="https://github.com/test/pr/42")
        out = StringIO()
        with patch("sys.stdout", out):
            rc = main([
                "--run", path,
                "--interval", "0",
                "--max-iterations", "1"
            ])
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("completed_pr_open", output)
        self.assertIn("https://github.com/test/pr/42", output)

    def test_exit_0_on_finish_without_pr(self):
        """Test script exits 0 and prints state only when no PR URL."""
        path = self._write_record("finished.yaml", final_status="investigation_complete")
        out = StringIO()
        with patch("sys.stdout", out):
            rc = main([
                "--run", path,
                "--interval", "0",
                "--max-iterations", "1"
            ])
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("investigation_complete", output)

    def test_exit_1_on_ceiling_reached(self):
        """Test script exits 1 with ceiling message after max iterations."""
        path = self._write_record("not_finished.yaml")
        out = StringIO()
        with patch("sys.stdout", out):
            rc = main([
                "--run", path,
                "--interval", "0",
                "--max-iterations", "2"
            ])
        self.assertEqual(rc, 1)
        output = out.getvalue()
        self.assertIn("ceiling reached", output)
        self.assertIn("subprocess still running", output)

    def test_bounded_loop_enforced(self):
        """Test that exactly max_iterations polls are made (no unbounded loop)."""
        path = self._write_record("not_finished.yaml")

        # Patch time.sleep to count calls
        sleep_calls = []
        orig_sleep = __import__("time").sleep
        def mock_sleep(interval):
            sleep_calls.append(interval)
            return orig_sleep(0)

        with patch("time.sleep", mock_sleep):
            out = StringIO()
            with patch("sys.stdout", out):
                rc = main([
                    "--run", path,
                    "--interval", "1",
                    "--max-iterations", "3"
                ])

        self.assertEqual(rc, 1)
        # Should sleep max_iterations - 1 times (no sleep after last iteration)
        self.assertEqual(len(sleep_calls), 2)

    def test_no_unbounded_while_true(self):
        """Verify poll_run.py source has no 'while True' loop."""
        import inspect
        source = inspect.getsource(main)
        self.assertNotIn("while True", source)

    def test_script_not_found_file(self):
        """Test script handles missing run record gracefully."""
        out = StringIO()
        with patch("sys.stdout", out):
            rc = main([
                "--run", "/nonexistent/path/run.yaml",
                "--interval", "0",
                "--max-iterations", "2"
            ])
        self.assertEqual(rc, 1)


class TestCLI(unittest.TestCase):
    """Integration tests via CLI invocation."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write_record(self, filename: str, final_status=None) -> str:
        """Helper to write a minimal run record YAML file."""
        path = Path(self.tmp) / filename
        lines = [
            "run_id: test-run",
            "status: executing",
            f"final_status: {final_status if final_status else 'null'}",
        ]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def test_cli_exit_0_when_finished(self):
        """Test CLI exits 0 when record is finished."""
        path = self._write_record("finished.yaml", final_status="completed_pr_open")
        result = subprocess.run(
            [sys.executable, "-m", "worktrail.router.poll_run", "--run", path,
             "--interval", "0", "--max-iterations", "1"],
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 0)

    def test_cli_exit_1_when_timeout(self):
        """Test CLI exits 1 when max iterations reached."""
        path = self._write_record("not_finished.yaml")
        result = subprocess.run(
            [sys.executable, "-m", "worktrail.router.poll_run", "--run", path,
             "--interval", "0", "--max-iterations", "2"],
            capture_output=True,
            text=True
        )
        self.assertEqual(result.returncode, 1)


if __name__ == "__main__":
    unittest.main()
