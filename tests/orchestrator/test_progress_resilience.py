#!/usr/bin/env python3
"""Tests for the progress.py resilience additions:

  - atomic_write_text (#1): writes the whole file or nothing; leaves no temp file.
  - _pid_alive (#5): live vs dead/None pid.
  - write_actives + render (#13/#5): several concurrent workers render as in-flight;
    a worker whose pid is dead renders STALE and is NOT counted in flight.

Run: python3 scripts/test_progress_resilience.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import progress  # noqa: E402


class AtomicWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_content(self):
        p = self.d / "j.json"
        progress.atomic_write_text(p, '{"a":1}\n')
        self.assertEqual(p.read_text(), '{"a":1}\n')

    def test_overwrites_and_leaves_no_tempfile(self):
        p = self.d / "j.json"
        progress.atomic_write_text(p, "old")
        progress.atomic_write_text(p, "new")
        self.assertEqual(p.read_text(), "new")
        leftovers = [x.name for x in self.d.iterdir() if x.name != "j.json"]
        self.assertEqual(leftovers, [], f"atomic write left temp files: {leftovers}")

    def test_creates_parent_dirs(self):
        p = self.d / "deep" / "nested" / "j.json"
        progress.atomic_write_text(p, "x")
        self.assertEqual(p.read_text(), "x")


class PidAlive(unittest.TestCase):
    def test_self_is_alive(self):
        self.assertTrue(progress._pid_alive(os.getpid()))

    def test_none_and_zero_are_not_alive(self):
        self.assertFalse(progress._pid_alive(None))
        self.assertFalse(progress._pid_alive(0))  # guarded: kill(0) would hit the group

    def test_reaped_child_is_dead(self):
        import subprocess

        p = subprocess.Popen(["true"])
        p.wait()
        self.assertFalse(progress._pid_alive(p.pid))


class MultiActiveRender(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "run-008.json"
        self.journal.write_text(json.dumps({"spec_id": "008", "run_id": "full-1", "entries": []}))

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_concurrent_workers_both_in_flight(self):
        progress.write_actives(
            self.journal,
            run_id="full-1",
            spec_id="008",
            actives=[
                {"task": "TASK-001", "role": "implement", "started_at": 1000.0, "pid": os.getpid()},
                {"task": "TASK-002", "role": "implement", "started_at": 1000.0, "pid": os.getpid()},
            ],
        )
        tasks = [
            {"id": "TASK-001", "status": "implementing"},
            {"id": "TASK-002", "status": "implementing"},
        ]
        out = progress.render(self.journal, tasks=tasks, now=1010.0)
        self.assertIn("[▶] TASK-001", out)
        self.assertIn("[▶] TASK-002", out)
        self.assertIn("2 in flight", out)
        self.assertEqual(out.count("running 10s"), 2)

    def test_dead_pid_renders_stale_not_in_flight(self):
        progress.write_actives(
            self.journal,
            run_id="full-1",
            spec_id="008",
            actives=[
                {"task": "TASK-001", "role": "implement", "started_at": 1000.0, "pid": os.getpid()},
                {"task": "TASK-002", "role": "implement", "started_at": 1000.0, "pid": 999999999},
            ],
        )
        tasks = [
            {"id": "TASK-001", "status": "implementing"},
            {"id": "TASK-002", "status": "implementing"},
        ]
        out = progress.render(self.journal, tasks=tasks, now=1010.0)
        self.assertIn("1 in flight", out)  # only the live one counts
        self.assertIn("STALE", out)
        self.assertIn("[?] TASK-002", out)

    def test_set_phase_clears_actives(self):
        progress.write_actives(
            self.journal,
            run_id="full-1",
            spec_id="008",
            actives=[{"task": "T", "role": "implement", "started_at": 1.0, "pid": os.getpid()}],
        )
        progress.set_phase(self.journal, "integrate")
        hb = json.loads(progress.heartbeat_path(self.journal).read_text())
        self.assertEqual(hb["actives"], [])
        self.assertIsNone(hb["active"])
        self.assertEqual(hb["phase"], "integrate")


class BestEffortGroupPhases(unittest.TestCase):
    """AC-018: progress writes and render failures must never abort the run."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "run-009.json"
        self.journal.write_text(
            json.dumps({"spec_id": "009", "run_id": "full-1", "entries": []})
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_failure_to_bad_path_does_not_raise(self):
        """AC-018: set_group_phases to an impossible path is swallowed."""
        progress.set_group_phases("/no-such-dir-xyz/run.json", {"g": "fanout"})

    def test_render_malformed_heartbeat_does_not_crash(self):
        """AC-018: render with garbage heartbeat JSON does not crash status."""
        hb_path = progress.heartbeat_path(self.journal)
        hb_path.write_text("{ this is not valid json !!! }")
        out = progress.render(self.journal, now=9999.0)
        # _safe_load returns {} for invalid JSON; render falls back gracefully
        self.assertIn("0/0 tasks done", out)

    def test_render_malformed_group_phases_does_not_crash(self):
        """AC-018: render with non-dict group_phases in heartbeat falls back gracefully."""
        hb_path = progress.heartbeat_path(self.journal)
        hb_path.write_text(
            json.dumps(
                {
                    "run_id": "full-1",
                    "spec_id": "009",
                    "phase": "fanout",
                    "group_phases": "not-a-dict",  # malformed: should be dict
                }
            )
        )
        # isinstance check in render() prevents the bad value from reaching the
        # summary computation; falls back to single phase: line (no crash).
        out = progress.render(self.journal, now=9999.0)
        self.assertIn("phase: fanout", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
