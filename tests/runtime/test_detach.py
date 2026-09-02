#!/usr/bin/env python3
"""Tests for `worktrail-detach` (stdlib unittest).

Spawns real subprocesses -- the property under test is process/session
placement and sentinel bookkeeping, which a mocked Popen cannot assert.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from worktrail.runtime import detach

PY = sys.executable


def _wait_for(pred, timeout=10.0, interval=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(interval)
    return False


class DetachLaunchTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_launch_writes_log_pid_and_exit_sentinel(self):
        handle = detach.launch(
            "echo-run",
            [PY, "-c", "print('hello from child'); import sys; sys.exit(7)"],
            None,
            self.sd,
        )
        self.assertNotIn("error", handle)
        p = detach.handle_paths("echo-run", self.sd)
        self.assertTrue(_wait_for(p["exit"].exists))
        self.assertEqual(p["exit"].read_text().strip(), "7")
        log = p["log"].read_text()
        self.assertIn("hello from child", log)
        self.assertIn(f"{detach.MARKER} exit rc=7", log)
        self.assertIsNotNone(handle["pid"])
        self.assertEqual(handle["exit_file"], str(p["exit"]))

    def test_child_runs_in_a_different_session_than_the_launcher(self):
        handle = detach.launch(
            "sess", [PY, "-c", "import time; time.sleep(3)"], None, self.sd
        )
        pid = handle["pid"]
        self.assertIsNotNone(pid)
        self.assertNotEqual(os.getsid(pid), os.getsid(os.getpid()))
        self.assertNotEqual(os.getpgid(pid), os.getpgid(os.getpid()))
        self.assertEqual(detach.status("sess", self.sd)["state"], "running")

    def test_status_transitions_running_to_exited(self):
        detach.launch(
            "short", [PY, "-c", "import time; time.sleep(0.5)"], None, self.sd
        )
        self.assertEqual(detach.status("short", self.sd)["state"], "running")
        p = detach.handle_paths("short", self.sd)
        self.assertTrue(_wait_for(p["exit"].exists))
        st = detach.status("short", self.sd)
        self.assertEqual(st["state"], "exited")
        self.assertEqual(st["exit_code"], 0)

    def test_status_unknown_when_no_handle(self):
        self.assertEqual(detach.status("nothing", self.sd)["state"], "unknown")

    def test_launch_refuses_to_clobber_a_running_handle_without_force(self):
        detach.launch("busy", [PY, "-c", "import time; time.sleep(3)"], None, self.sd)
        second = detach.launch("busy", [PY, "-c", "print(1)"], None, self.sd)
        self.assertEqual(second.get("error"), "already-running")
        forced = detach.launch(
            "busy", [PY, "-c", "print(1)"], None, self.sd, force=True
        )
        self.assertNotIn("error", forced)

    def test_missing_command_records_127(self):
        detach.launch("nope", ["/definitely/not/a/binary"], None, self.sd)
        p = detach.handle_paths("nope", self.sd)
        self.assertTrue(_wait_for(p["exit"].exists))
        self.assertEqual(p["exit"].read_text().strip(), "127")
        self.assertIn("spawn failed", p["log"].read_text())

    def test_cwd_is_honored(self):
        target = self.sd / "workdir"
        target.mkdir()
        detach.launch(
            "cwd", [PY, "-c", "import os; print(os.getcwd())"], str(target), self.sd
        )
        p = detach.handle_paths("cwd", self.sd)
        self.assertTrue(_wait_for(p["exit"].exists))
        self.assertIn(str(target.resolve()), p["log"].read_text())

    def test_invalid_name_rejected(self):
        with self.assertRaises(SystemExit):
            detach.launch("../escape", [PY, "-c", "pass"], None, self.sd)


class DetachWaitTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.sd = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_wait_streams_matching_lines_and_returns_child_rc(self):
        # The script lives in a file so its source text (which mentions the
        # very words the filter matches) never appears in the log's own
        # `started cmd=[...]` header line.
        script = self.sd / "stream.py"
        script.write_text(
            "import sys,time\n"
            "for i in range(3):\n"
            "    print(f'progress {i}', flush=True); print('noise', flush=True); time.sleep(0.1)\n"
            "print('Traceback (most recent call last):', flush=True)\n"
            "sys.exit(3)\n"
        )
        detach.launch("stream", [PY, str(script)], None, self.sd)
        out = io.StringIO()
        rc = detach.wait(
            "stream",
            self.sd,
            match=r"progress|Traceback",
            interval=0.05,
            timeout=15,
            from_start=True,
            out=out,
        )
        self.assertEqual(rc, 3)
        lines = out.getvalue().splitlines()
        self.assertEqual(
            [line for line in lines if line.startswith("progress")],
            ["progress 0", "progress 1", "progress 2"],
        )
        self.assertNotIn("noise", out.getvalue())
        self.assertIn("Traceback (most recent call last):", lines)
        self.assertEqual(lines[-1], f"{detach.MARKER} exited rc=3")

    def test_wait_times_out(self):
        detach.launch("slow", [PY, "-c", "import time; time.sleep(5)"], None, self.sd)
        out = io.StringIO()
        rc = detach.wait("slow", self.sd, None, 0.05, 0.3, False, out=out)
        self.assertEqual(rc, detach.EXIT_TIMEOUT)

    def test_wait_reports_gone_when_pid_dies_without_sentinel(self):
        p = detach.handle_paths("ghost", self.sd)
        p["log"].write_text("")
        p["pid"].write_text("999999999\n")
        out = io.StringIO()
        rc = detach.wait("ghost", self.sd, None, 0.05, 5, False, out=out)
        self.assertEqual(rc, detach.EXIT_GONE)
        self.assertIn("gone without exit sentinel", out.getvalue())


class DetachCliTests(unittest.TestCase):
    def test_cli_launch_then_wait_roundtrip(self):
        import json
        import subprocess

        with tempfile.TemporaryDirectory() as tmp:
            launched = subprocess.run(
                [
                    PY,
                    "-m",
                    "worktrail.runtime.detach",
                    "launch",
                    "--name",
                    "cli",
                    "--state-dir",
                    tmp,
                    "--",
                    PY,
                    "-c",
                    "print('ok'); raise SystemExit(0)",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            handle = json.loads(launched.stdout)
            self.assertEqual(handle["name"], "cli")
            waited = subprocess.run(
                [
                    PY,
                    "-m",
                    "worktrail.runtime.detach",
                    "wait",
                    "--name",
                    "cli",
                    "--state-dir",
                    tmp,
                    "--match",
                    "ok",
                    "--from-start",
                    "--interval",
                    "0.05",
                    "--timeout",
                    "15",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
            self.assertIn("ok", waited.stdout.splitlines()[0])
            st = subprocess.run(
                [
                    PY,
                    "-m",
                    "worktrail.runtime.detach",
                    "status",
                    "--name",
                    "cli",
                    "--state-dir",
                    tmp,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(json.loads(st.stdout)["state"], "exited")


if __name__ == "__main__":
    unittest.main()
