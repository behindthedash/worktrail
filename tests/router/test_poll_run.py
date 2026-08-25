#!/usr/bin/env python3
"""Tests for poll_run.py. Run: python3 test_poll_run.py"""
import os
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import worktrail.router.poll_run as poll_run
from worktrail.router.poll_run import (
    is_finished,
    read_run_record,
    unresolved_decision_ids,
    main,
)


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
        with patch.object(poll_run, "ensure_pr_risk_label", return_value=None), \
             patch("sys.stdout", out):
            rc = main([
                "--run", path,
                "--interval", "0",
                "--max-iterations", "1"
            ])
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("completed_pr_open", output)
        self.assertIn("https://github.com/test/pr/42", output)

    def test_exit_0_on_finish_with_pr_applies_label_correction(self):
        """Test the completion path calls ensure_pr_risk_label with the
        record's own repository/pull_request/risk_level fields, and logs
        when a label was applied -- go's own Phase 7 poll-exit equivalent
        of drain.py's post-hoc correction."""
        path = self._write_record("finished.yaml", final_status="completed_pr_open",
                                  pull_request="https://github.com/test/pr/42")
        seen = []
        out = StringIO()
        with patch.object(poll_run, "ensure_pr_risk_label",
                          lambda repo, pr, risk: seen.append((repo, pr, risk)) or "go:risk-medium"), \
             patch("sys.stdout", out):
            rc = main(["--run", path, "--interval", "0", "--max-iterations", "1"])
        self.assertEqual(rc, 0)
        self.assertEqual(seen, [("/tmp/test-repo", "https://github.com/test/pr/42", "medium")])
        output = out.getvalue()
        self.assertIn("added missing go:risk-medium label", output)

    def test_exit_0_on_finish_without_pr_skips_label_correction(self):
        """No PR URL -- ensure_pr_risk_label must not be called at all."""
        path = self._write_record("finished.yaml", final_status="investigation_complete")

        def unexpected(*_a, **_k):
            raise AssertionError("must not be called when the run has no PR")

        out = StringIO()
        with patch.object(poll_run, "ensure_pr_risk_label", unexpected), \
             patch("sys.stdout", out):
            rc = main(["--run", path, "--interval", "0", "--max-iterations", "1"])
        self.assertEqual(rc, 0)

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


class PendingDecisionSurfacingTests(unittest.TestCase):
    """`pending_user_decision` is a first-class completion result: the poller
    surfaces each still-unanswered decision's exact id so an attended host can
    present it and resume through that id -- never a generic failure."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _write_record(self, filename: str, final_status=None,
                      decisions=()) -> str:
        path = Path(self.tmp) / filename
        lines = [
            "run_id: test-run",
            "status: executing",
            f"final_status: {final_status if final_status else 'null'}",
        ]
        if decisions:
            lines.append("pending_decisions:")
            lines.extend(f"  - {entry}" for entry in decisions)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(path)

    def test_read_run_record_collects_pending_decisions_entries(self):
        path = self._write_record(
            "blocked.yaml", final_status="blocked_product_decision",
            decisions=[
                "2026-08-25T10:00:00+0000 [asked] dec-alpha-000001",
                "2026-08-25T11:00:00+0000 [presented] dec-alpha-000001",
            ],
        )
        record = read_run_record(Path(path))
        self.assertEqual(record.get("pending_decisions"), [
            "2026-08-25T10:00:00+0000 [asked] dec-alpha-000001",
            "2026-08-25T11:00:00+0000 [presented] dec-alpha-000001",
        ])
        self.assertEqual(record.get("final_status"), "blocked_product_decision")

    def test_read_run_record_keeps_scalar_and_null_parsing(self):
        path = Path(self.tmp) / "plain.yaml"
        path.write_text(
            'run_id: plain\n'
            'pull_request: null\n'
            'final_status: null\n'
            'pull_request_quoted: "https://x"\n',
            encoding="utf-8")
        record = read_run_record(Path(path))
        self.assertEqual(record.get("run_id"), "plain")
        self.assertIsNone(record.get("pull_request"))
        self.assertIsNone(record.get("final_status"))
        self.assertEqual(record.get("pull_request_quoted"), "https://x")

    def test_unresolved_ids_track_the_latest_event_per_decision(self):
        record = {"pending_decisions": [
            "t1 [asked] dec-one",
            "t1 [asked] dec-two",
            "t1 [answered] dec-two",
            "t1 [consumed] dec-three",
            "t1 [asked] dec-four",
            "t1 [superseded] dec-four",
        ]}
        self.assertEqual(
            unresolved_decision_ids(record), ["dec-one", "dec-two"])

    def test_unresolved_ids_ignore_malformed_or_missing_entries(self):
        self.assertEqual(unresolved_decision_ids({}), [])
        self.assertEqual(unresolved_decision_ids({"pending_decisions": None}), [])
        self.assertEqual(unresolved_decision_ids({"pending_decisions": [
            "garbage without tokens", 42, None,
            "t1 [consumed] dec-five",
        ]}), [])

    def test_completion_surfaces_open_decisions_with_exact_resume_token(self):
        path = self._write_record(
            "blocked.yaml", final_status="blocked_product_decision",
            decisions=["2026-08-25T10:00:00+0000 [asked] dec-alpha-000001"],
        )
        out = StringIO()
        with patch.object(poll_run, "ensure_pr_risk_label", return_value=None), \
             patch("sys.stdout", out):
            rc = main(["--run", path, "--interval", "0", "--max-iterations", "1"])
        self.assertEqual(rc, 0)
        output = out.getvalue()
        self.assertIn("Run completed: blocked_product_decision", output)
        self.assertIn("pending_user_decision: dec-alpha-000001", output)
        self.assertIn("--resume-decision", output)

    def test_consumed_and_superseded_decisions_are_not_surfaced(self):
        path = self._write_record(
            "done.yaml", final_status="failed_recoverable",
            decisions=[
                "t [asked] dec-old",
                "t [superseded] dec-old",
                "t [asked] dec-used",
                "t [consumed] dec-used",
            ],
        )
        out = StringIO()
        with patch("sys.stdout", out):
            rc = main(["--run", path, "--interval", "0", "--max-iterations", "1"])
        self.assertEqual(rc, 0)
        self.assertNotIn("pending_user_decision", out.getvalue())

    def test_cli_subprocess_surfaces_the_exact_decision_id(self):
        src = str(Path(poll_run.__file__).resolve().parents[2])
        path = self._write_record(
            "blocked.yaml", final_status="blocked_product_decision",
            decisions=["2026-08-25T10:00:00+0000 [asked] dec-subproc-000001"],
        )
        env = {**os.environ, "PYTHONPATH": src}
        result = subprocess.run(
            [sys.executable, "-m", "worktrail.router.poll_run", "--run", path,
             "--interval", "0", "--max-iterations", "1"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("pending_user_decision: dec-subproc-000001", result.stdout)


if __name__ == "__main__":
    unittest.main()
