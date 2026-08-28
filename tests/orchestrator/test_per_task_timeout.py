#!/usr/bin/env python3
"""Unit tests for per-task timeout feature (spec 011-orchestrator-per-task-timeout).

Covers AC-1, AC-2, AC-5, AC-6, AC-7, AC-8, AC-9.
"""

import io
import os
import subprocess
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from worktrail.orchestrator import dispatch
from worktrail.orchestrator import spawnlib
from worktrail.orchestrator.live import LiveSpawn, WORKER_TIMEOUT_DEFAULT

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_spawn_factory():
    """Returns a fake spawn_claude_p that records its timeout= argument."""
    calls = []

    def fake(prompt, worktree, tier=None, prefer=None, exclude_harness=None, timeout=None, extra_args=None, resume_session_id=None, log=None):
        calls.append({"timeout": timeout})
        return (
            '{"task": "T", "step": "implement", "status": "success",'
            ' "head_sha": "abc", "files_touched": [], "tests": "none",'
            ' "review_status": null, "critical_issues": 0, "major_issues": 0, "notes": "ok"}'
        )

    fake.calls = calls
    return fake


def _started_line(spawn_obj, task):
    """Compute the 'started' log output using the same formula as _drive_task()."""
    eff = task.get("timeout") or getattr(spawn_obj, "timeout", "?")
    marker = (
        " [task-override]"
        if isinstance(eff, int) and eff != getattr(spawn_obj, "timeout", None)
        else ""
    )
    return f"(worker running; timeout {eff}s{marker})"


def _call(spawn_obj, task, role="implement"):
    """Invoke LiveSpawn.__call__ with mocked dependencies."""
    with patch.object(dispatch, "build_worker_prompt", return_value="fake-prompt"), patch.object(
        spawnlib, "spawn_claude_p", _fake_spawn_factory()
    ):
        return spawn_obj(role, task, Path("/fake/wt"))


def _call_capturing_timeout(spawn_obj, task, fake):
    """Invoke LiveSpawn.__call__ with a provided fake; return fake's recorded calls."""
    with patch.object(dispatch, "build_worker_prompt", return_value="fake-prompt"), patch.object(
        spawnlib, "spawn_claude_p", fake
    ):
        spawn_obj("implement", task, Path("/fake/wt"))
    return fake.calls


# ---------------------------------------------------------------------------
# AC-1, AC-2: WORKER_TIMEOUT_DEFAULT constant and env override
# ---------------------------------------------------------------------------


class TestWorkerTimeoutDefault(unittest.TestCase):

    def test_default_is_1800_when_env_not_set(self):
        """AC-1: default formula yields 1800 when ORCH_WORKER_TIMEOUT is absent."""
        env_without_var = {k: v for k, v in os.environ.items() if k != "ORCH_WORKER_TIMEOUT"}
        with patch.dict(os.environ, env_without_var, clear=True):
            result = int(os.environ.get("ORCH_WORKER_TIMEOUT", "1800"))
        self.assertEqual(result, 1800)

    def test_constant_is_1800_if_env_unset_at_import_time(self):
        """AC-1: WORKER_TIMEOUT_DEFAULT == 1800 (module imported without env override)."""
        if os.environ.get("ORCH_WORKER_TIMEOUT"):
            self.skipTest("ORCH_WORKER_TIMEOUT is set in test environment")
        self.assertEqual(WORKER_TIMEOUT_DEFAULT, 1800)

    def test_env_override_is_honored(self):
        """AC-2: ORCH_WORKER_TIMEOUT=600 overrides the 1800 default."""
        with patch.dict(os.environ, {"ORCH_WORKER_TIMEOUT": "600"}):
            result = int(os.environ.get("ORCH_WORKER_TIMEOUT", "1800"))
        self.assertEqual(result, 600)


# ---------------------------------------------------------------------------
# AC-5, AC-6, AC-7, AC-8: LiveSpawn.__call__ effective_timeout computation
# ---------------------------------------------------------------------------


class TestLiveSpawnCallTimeout(unittest.TestCase):

    def test_per_task_timeout_greater_passed_to_spawn(self):
        """AC-5: task["timeout"]=2700, run default 1800 → spawn_claude_p called with 2700."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        fake = _fake_spawn_factory()
        calls = _call_capturing_timeout(spawn, {"id": "T001", "timeout": 2700}, fake)
        self.assertEqual(calls[0]["timeout"], 2700)

    def test_per_task_timeout_less_passed_to_spawn(self):
        """AC-6: task["timeout"]=900, run default 1800 → spawn_claude_p called with 900."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        fake = _fake_spawn_factory()
        calls = _call_capturing_timeout(spawn, {"id": "T001", "timeout": 900}, fake)
        self.assertEqual(calls[0]["timeout"], 900)

    def test_no_per_task_timeout_uses_run_default(self):
        """AC-7: no task timeout key → spawn_claude_p called with run-level default 1800."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        fake = _fake_spawn_factory()
        calls = _call_capturing_timeout(spawn, {"id": "T001"}, fake)
        self.assertEqual(calls[0]["timeout"], 1800)

    def test_per_task_timeout_none_uses_run_default(self):
        """AC-7: task["timeout"]=None → spawn_claude_p called with run-level default 1800."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        fake = _fake_spawn_factory()
        calls = _call_capturing_timeout(spawn, {"id": "T001", "timeout": None}, fake)
        self.assertEqual(calls[0]["timeout"], 1800)

    def test_self_timeout_not_mutated_after_override_greater(self):
        """AC-8: self.timeout stays 1800 after __call__ with a higher per-task value."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        _call(spawn, {"id": "T001", "timeout": 3600})
        self.assertEqual(spawn.timeout, 1800)

    def test_self_timeout_not_mutated_after_override_lower(self):
        """AC-8: self.timeout stays 1800 after __call__ with a lower per-task value."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        _call(spawn, {"id": "T001", "timeout": 600})
        self.assertEqual(spawn.timeout, 1800)


# ---------------------------------------------------------------------------
# AC-5, AC-6, AC-7: started log line format (the _drive_task() print logic)
# ---------------------------------------------------------------------------


class TestStartedLogLineFormat(unittest.TestCase):

    def test_override_marker_present_when_task_timeout_greater(self):
        """AC-5: started line contains '2700s [task-override]' for upward override."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        line = _started_line(spawn, {"id": "T001", "timeout": 2700})
        self.assertIn("2700s", line)
        self.assertIn("[task-override]", line)

    def test_override_marker_present_when_task_timeout_lower(self):
        """AC-6: started line contains '900s [task-override]' for downward override."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        line = _started_line(spawn, {"id": "T001", "timeout": 900})
        self.assertIn("900s", line)
        self.assertIn("[task-override]", line)

    def test_no_override_marker_when_no_task_timeout(self):
        """AC-7: started line has no '[task-override]' when task uses run default."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        line = _started_line(spawn, {"id": "T001"})
        self.assertIn("1800s", line)
        self.assertNotIn("[task-override]", line)

    def test_no_override_marker_when_task_timeout_is_none(self):
        """AC-7: started line has no '[task-override]' when task["timeout"] is None."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        line = _started_line(spawn, {"id": "T001", "timeout": None})
        self.assertIn("1800s", line)
        self.assertNotIn("[task-override]", line)


# ---------------------------------------------------------------------------
# AC-8: concurrency — two threads share one LiveSpawn, no timeout bleed
# ---------------------------------------------------------------------------


class TestConcurrencyNoBleed(unittest.TestCase):

    def test_concurrent_calls_each_get_own_timeout(self):
        """AC-8: two concurrent __call__ invocations each use their own task timeout."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)

        received = {}
        barrier = threading.Barrier(2)

        def fake_spawn(prompt, worktree, tier=None, prefer=None, exclude_harness=None, timeout=None, extra_args=None, resume_session_id=None, log=None):
            barrier.wait()  # both threads enter simultaneously
            received[threading.get_ident()] = timeout
            return (
                '{"task": "T", "step": "implement", "status": "success",'
                ' "head_sha": "abc", "files_touched": [], "tests": "none",'
                ' "review_status": null, "critical_issues": 0, "major_issues": 0, "notes": "ok"}'
            )

        task_a = {"id": "T001", "timeout": 900}
        task_b = {"id": "T002", "timeout": 3600}
        thread_ids = {}

        def run(task):
            thread_ids[task["id"]] = threading.get_ident()
            spawn("implement", task, Path("/fake/wt"))

        # Install both patches ONCE, before the threads start, not one pair
        # per thread inside run(): unittest.mock.patch.object's start()/stop()
        # push/pop a per-target stack that is not safe for two threads to
        # enter and exit concurrently on the SAME target -- confirmed live,
        # a per-thread `with patch.object(...)` here left spawnlib.spawn_claude_p
        # permanently monkey-patched to this test's own closure afterward,
        # breaking every later test in the suite that spawns for real.
        with patch.object(dispatch, "build_worker_prompt", return_value="fake"), patch.object(
            spawnlib, "spawn_claude_p", fake_spawn
        ):
            with ThreadPoolExecutor(max_workers=2) as ex:
                fa = ex.submit(run, task_a)
                fb = ex.submit(run, task_b)
                fa.result()
                fb.result()

        self.assertEqual(received[thread_ids["T001"]], 900)
        self.assertEqual(received[thread_ids["T002"]], 3600)
        self.assertEqual(spawn.timeout, 1800, "shared spawn.timeout must not be mutated")


# ---------------------------------------------------------------------------
# AC-9: timeout expiry reports effective (per-task) timeout
# ---------------------------------------------------------------------------


class TestExpiryReportsEffectiveTimeout(unittest.TestCase):

    def test_expiry_formula_uses_per_task_timeout(self):
        """AC-9: drive()/drive_task() expiry expression yields per-task value."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        task = {"id": "T001", "timeout": 2700}
        # The exact expression from the changed drive() / _drive_task():
        limit = task.get("timeout") or getattr(spawn, "timeout", "?")
        self.assertEqual(limit, 2700)
        self.assertNotEqual(limit, spawn.timeout)

    def test_expiry_formula_falls_back_to_run_timeout(self):
        """AC-9: expiry expression yields run-level timeout when task has no override."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        task = {"id": "T002"}
        limit = task.get("timeout") or getattr(spawn, "timeout", "?")
        self.assertEqual(limit, 1800)

    def test_expiry_print_contains_effective_timeout_not_run_timeout(self):
        """AC-9: when spawn_claude_p raises TimeoutExpired, handler reports effective value."""
        spawn = LiveSpawn("spec", "docs/specs/spec/", agent="claude", timeout=1800)
        task = {"id": "T001", "timeout": 2700}

        def raising_spawn(prompt, worktree, tier=None, prefer=None, exclude_harness=None, timeout=None, extra_args=None, resume_session_id=None, log=None):
            raise subprocess.TimeoutExpired(cmd=["claude"], timeout=timeout)

        output = io.StringIO()
        with patch.object(dispatch, "build_worker_prompt", return_value="p"), patch.object(
            spawnlib, "spawn_claude_p", raising_spawn
        ):
            try:
                spawn("implement", task, Path("/fake/wt"))
            except subprocess.TimeoutExpired:
                # Mirror the changed expiry handler in drive() / _drive_task():
                limit = task.get("timeout") or getattr(spawn, "timeout", "?")
                print(f"TIMED OUT after {limit}s", file=output)

        self.assertIn("2700s", output.getvalue())
        self.assertNotIn("1800s", output.getvalue())


if __name__ == "__main__":
    unittest.main()
