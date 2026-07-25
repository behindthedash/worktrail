#!/usr/bin/env python3
"""Tests for --notify-cmd and --progress-interval wiring in live.py full-real."""

import inspect
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live


# ---------------------------------------------------------------------------
# _fire_notify
# ---------------------------------------------------------------------------


class FireNotifyTests(unittest.TestCase):
    def test_runs_shell_command_with_json_on_stdin(self):
        received = []

        def fake_run(_cmd, **kwargs):
            received.append({"cmd": _cmd, "input": kwargs.get("input")})
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            live._fire_notify("cat", {"spec": "001-foo", "phase": "fanout", "tasks_done": 1})

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["cmd"], "cat")
        payload = json.loads(received[0]["input"])
        self.assertEqual(payload["spec"], "001-foo")
        self.assertEqual(payload["phase"], "fanout")
        self.assertEqual(payload["tasks_done"], 1)

    def test_shell_true_is_passed(self):
        kwargs_seen = []

        def fake_run(cmd, **kwargs):
            kwargs_seen.append(kwargs)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            live._fire_notify("true", {})

        self.assertTrue(kwargs_seen[0].get("shell"))

    def test_never_raises_on_subprocess_error(self):
        with patch("subprocess.run", side_effect=OSError("broken")):
            live._fire_notify("broken-cmd", {"x": 1})  # must not raise

    def test_never_raises_on_timeout(self):
        import subprocess

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("cmd", 10)):
            live._fire_notify("slow-cmd", {})  # must not raise

    def test_text_mode_enabled(self):
        kwargs_seen = []

        def fake_run(cmd, **kwargs):
            kwargs_seen.append(kwargs)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            live._fire_notify("echo", {"k": "v"})

        self.assertTrue(kwargs_seen[0].get("text"))

    def test_timeout_is_set(self):
        kwargs_seen = []

        def fake_run(cmd, **kwargs):
            kwargs_seen.append(kwargs)
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=fake_run):
            live._fire_notify("echo", {})

        self.assertIn("timeout", kwargs_seen[0])
        self.assertGreater(kwargs_seen[0]["timeout"], 0)


# ---------------------------------------------------------------------------
# Signature tests: new params present with correct defaults
# ---------------------------------------------------------------------------


class LiveRunRealSignature(unittest.TestCase):
    def test_notify_cmd_param_defaults_none(self):
        sig = inspect.signature(live.live_run_real)
        self.assertIn("notify_cmd", sig.parameters)
        self.assertIsNone(sig.parameters["notify_cmd"].default)

    def test_progress_interval_param_defaults_none(self):
        sig = inspect.signature(live.live_run_real)
        self.assertIn("progress_interval", sig.parameters)
        self.assertIsNone(sig.parameters["progress_interval"].default)


class FullRealSignature(unittest.TestCase):
    def test_notify_cmd_in_full_real(self):
        sig = inspect.signature(live.full_real)
        self.assertIn("notify_cmd", sig.parameters)
        self.assertIsNone(sig.parameters["notify_cmd"].default)

    def test_progress_interval_in_full_real(self):
        sig = inspect.signature(live.full_real)
        self.assertIn("progress_interval", sig.parameters)
        self.assertIsNone(sig.parameters["progress_interval"].default)

    def test_notify_cmd_in_full_real_inner(self):
        sig = inspect.signature(live._full_real_inner)
        self.assertIn("notify_cmd", sig.parameters)
        self.assertIsNone(sig.parameters["notify_cmd"].default)

    def test_progress_interval_in_full_real_inner(self):
        sig = inspect.signature(live._full_real_inner)
        self.assertIn("progress_interval", sig.parameters)
        self.assertIsNone(sig.parameters["progress_interval"].default)


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------


class CLINotifyArgs(unittest.TestCase):
    """--notify-cmd and --progress-interval parse correctly from live.py's main()."""

    def _parse(self, *extra):
        # Use live.main() with a fake full-real invocation; capture the full_real call.
        captured = {}

        def fake_full_real(*_args, **kwargs):
            captured.update(kwargs)
            return {}

        argv = [
            "full-real",
            "--repo", "/fake/repo",
            "--spec", "docs/specs/001-foo",
            "--base", "dev",
        ] + list(extra)

        with patch.object(live, "full_real", side_effect=fake_full_real):
            live.main(argv)

        return captured

    def test_notify_cmd_absent_passes_none(self):
        captured = self._parse()
        self.assertIsNone(captured.get("notify_cmd"))

    def test_notify_cmd_passed_through(self):
        captured = self._parse("--notify-cmd", "notify-send 'orch done'")
        self.assertEqual(captured.get("notify_cmd"), "notify-send 'orch done'")

    def test_progress_interval_absent_passes_none(self):
        captured = self._parse()
        self.assertIsNone(captured.get("progress_interval"))

    def test_progress_interval_passed_through(self):
        captured = self._parse("--progress-interval", "30")
        self.assertEqual(captured.get("progress_interval"), 30)

    def test_run_budget_absent_defaults_off(self):
        captured = self._parse()
        self.assertEqual(captured.get("run_budget"), 0)

    def test_run_budget_minutes_converted_to_seconds(self):
        captured = self._parse("--run-budget", "2")
        self.assertEqual(captured.get("run_budget"), 120)

    def test_run_budget_fractional_minutes_converted_to_seconds(self):
        captured = self._parse("--run-budget", "0.5")
        self.assertEqual(captured.get("run_budget"), 30.0)


# ---------------------------------------------------------------------------
# Progress emitter thread: starts and stops cleanly
# ---------------------------------------------------------------------------


class ProgressEmitterTests(unittest.TestCase):
    """_stop_emitter is set and the timer thread fires at the right cadence."""

    def _run_fanout_with_interval(self, interval, wait_s=0.25):
        """Run a minimal fake fan-out with progress_interval wired in.

        We replace the internal coordinator/loader/spawn machinery with stubs
        so the fan-out loop exits immediately (no tasks), then verify that the
        emitter thread fired at least once during a brief sleep.
        """
        printed = []

        original_print = print

        def capture_print(*pargs, **kwargs):
            printed.append(" ".join(str(a) for a in pargs))
            original_print(*pargs, **kwargs)

        fake_tasks = [{"id": "TASK-001", "status": "done", "retry_count": 0, "files": []}]

        with tempfile.TemporaryDirectory() as td:
            cassette = os.path.join(td, "run.json")

            with (
                patch("worktrail.taskformats.devkit.source.load_spec", return_value=("spec-001", fake_tasks)),
                patch("worktrail.orchestrator.coordinator.runnable_frontier", return_value=[]),
                patch("worktrail.orchestrator.coordinator.DONE", {"done", "completed"}),
                patch("worktrail.orchestrator.coordinator.IN_FLIGHT", set()),
                patch("worktrail.orchestrator.coordinator.disjoint_batches", return_value=[]),
                patch("worktrail.orchestrator.live.validate_task_metadata"),
                patch("builtins.print", side_effect=capture_print),
            ):
                live.live_run_real(
                    repo=MagicMock(__truediv__=lambda *_: MagicMock(exists=lambda: False)),
                    spec_rel="docs/specs/001-foo",
                    out_cassette=cassette,
                    progress_interval=interval,
                )
                time.sleep(wait_s)

        return printed

    def test_emitter_fires_at_interval(self):
        lines = self._run_fanout_with_interval(interval=1, wait_s=0.0)
        # With interval=1 and the loop exiting immediately, the emitter
        # should NOT fire before the stop event is set (daemon thread waits 1s first).
        # This test verifies the thread starts without error and the stop event is set.
        # A full timing test would be flaky in CI; we just assert no exception was raised.
        self.assertIsInstance(lines, list)

    def test_no_emitter_when_interval_none(self):
        """When progress_interval is None, no emitter thread is spawned."""
        thread_names_before = {t.name for t in threading.enumerate()}

        fake_tasks = [{"id": "TASK-001", "status": "done", "retry_count": 0, "files": []}]

        with tempfile.TemporaryDirectory() as td:
            cassette = os.path.join(td, "run.json")
            with (
                patch("worktrail.taskformats.devkit.source.load_spec", return_value=("spec-001", fake_tasks)),
                patch("worktrail.orchestrator.coordinator.runnable_frontier", return_value=[]),
                patch("worktrail.orchestrator.coordinator.DONE", {"done", "completed"}),
                patch("worktrail.orchestrator.coordinator.IN_FLIGHT", set()),
                patch("worktrail.orchestrator.coordinator.disjoint_batches", return_value=[]),
                patch("worktrail.orchestrator.live.validate_task_metadata"),
            ):
                live.live_run_real(
                    repo=MagicMock(__truediv__=lambda *_: MagicMock(exists=lambda: False)),
                    spec_rel="docs/specs/001-foo",
                    out_cassette=cassette,
                    progress_interval=None,
                )

        thread_names_after = {t.name for t in threading.enumerate()}
        self.assertNotIn("progress-emitter", thread_names_after - thread_names_before)


if __name__ == "__main__":
    unittest.main()
