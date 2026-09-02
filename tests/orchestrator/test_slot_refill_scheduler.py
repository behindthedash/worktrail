"""Regression test: the pipeline fan-out refills a freed worker slot immediately.

Defect (run orchestrator-throughput, 2026-09-02): `_pipeline_scheduler` computed a
frontier of up to `max_workers` tasks, ran them in a pool, and blocked on every
future before computing the next frontier. A slot freed by a fast task stayed idle
until the slowest task of the same tick -- including its review/fix strikes --
finished. Two of three slots idled 35+ minutes with four ready tasks waiting.

Fix: dispatch on every completion (`FIRST_COMPLETED`), re-running
`runnable_frontier`, which already excludes in-flight tasks and locks their files.
"""

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_budget_resume import FakeSpawn, _FakeVerifier, _fm, _init_repo

from worktrail.orchestrator import live


class _TimedSpawn(FakeSpawn):
    """FakeSpawn whose implement step sleeps per task and records start/end times."""

    def __init__(self, implement_delay_s: dict):
        super().__init__()
        self.implement_delay_s = implement_delay_s
        self.started_at: dict = {}
        self.ended_at: dict = {}
        self._tlock = threading.Lock()

    def __call__(self, role, task, wt):
        tid = task["id"]
        if role == "implement":
            with self._tlock:
                self.started_at[tid] = time.monotonic()
            time.sleep(self.implement_delay_s.get(tid, 0.0))
        result = super().__call__(role, task, wt)
        if role == "implement":
            with self._tlock:
                self.ended_at[tid] = time.monotonic()
        return result


class SlotRefillingFanout(unittest.TestCase):
    def test_freed_slot_is_refilled_before_slow_task_finishes(self):
        # Three independent tasks, two slots. TASK-001 is slow; TASK-002 is fast.
        # A slot-refilling scheduler starts TASK-003 as soon as TASK-002 frees its
        # slot, i.e. while TASK-001 is still implementing. The old tick-synchronous
        # loop only started TASK-003 after TASK-001 finished.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm("TASK-002", "src/task-002.txt"),
                    "TASK-003": _fm("TASK-003", "src/task-003.txt"),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            fake = _TimedSpawn({"TASK-001": 4.0, "TASK-002": 0.05, "TASK-003": 0.05})
            live._pipeline_scheduler(
                repo=repo,
                spec_rel="docs/specs/001-x",
                remote="origin",
                base="main",
                model="haiku",
                max_workers=2,
                timeout=60,
                resume=False,
                only=None,
                role_models=None,
                run_budget=None,
                journal_path=journal,
                run_id="pipe-refill",
                _spawn=fake,
                _integrate_one=lambda *a, **kw: None,
                _make_verifier=lambda: _FakeVerifier(),
            )
            self.assertEqual(set(fake.started_at), {"TASK-001", "TASK-002", "TASK-003"})
            self.assertLess(
                fake.started_at["TASK-003"],
                fake.ended_at["TASK-001"],
                "TASK-003 must start while TASK-001 is still running "
                "(freed slot refilled), not after the whole tick drains",
            )

    def test_never_exceeds_max_workers_and_respects_deps(self):
        # Four tasks, two slots: 3 depends on 1. Concurrency must never exceed the
        # cap, and 3 must not start before 1 ends even when a slot is free.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm("TASK-002", "src/task-002.txt"),
                    "TASK-003": _fm("TASK-003", "src/task-003.txt", deps="TASK-001"),
                    "TASK-004": _fm("TASK-004", "src/task-004.txt"),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            delays = {
                "TASK-001": 1.0,
                "TASK-002": 0.2,
                "TASK-003": 0.05,
                "TASK-004": 0.2,
            }
            fake = _TimedSpawn(delays)
            live._pipeline_scheduler(
                repo=repo,
                spec_rel="docs/specs/001-x",
                remote="origin",
                base="main",
                model="haiku",
                max_workers=2,
                timeout=60,
                resume=False,
                only=None,
                role_models=None,
                run_budget=None,
                journal_path=journal,
                run_id="pipe-cap",
                _spawn=fake,
                _integrate_one=lambda *a, **kw: None,
                _make_verifier=lambda: _FakeVerifier(),
            )
            self.assertEqual(len(fake.started_at), 4)
            self.assertGreaterEqual(
                fake.started_at["TASK-003"], fake.ended_at["TASK-001"]
            )
            # Concurrency cap: at no instant were more than two implement steps
            # in flight (sweep every start against every other task's window).
            for tid, s in fake.started_at.items():
                overlapping = sum(
                    1
                    for other, s2 in fake.started_at.items()
                    if other != tid and s2 <= s < fake.ended_at[other]
                )
                self.assertLessEqual(
                    overlapping, 1, f"{tid} started with {overlapping} others in flight"
                )
            head = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(head.returncode, 0)


class _FakeDrive:
    """Stand-in for `_safe_drive`: sleeps per task, records start/end, marks done."""

    def __init__(self, delays: dict):
        self.delays = delays
        self.started_at: dict = {}
        self.ended_at: dict = {}
        self._lock = threading.Lock()

    def __call__(self, task: dict) -> None:
        with self._lock:
            self.started_at[task["id"]] = time.monotonic()
        time.sleep(self.delays.get(task["id"], 0.0))
        with self._lock:
            task["status"] = "done"
            self.ended_at[task["id"]] = time.monotonic()


class ResumeInFlightConcurrently(unittest.TestCase):
    def test_in_flight_tasks_resume_in_parallel_with_fanout(self):
        # Two journal-replayed in-flight tasks and one pending task, two slots.
        # Before: in-flight tasks were driven one at a time, and fan-out only
        # began after both finished. After: both resume concurrently and the
        # pending task starts as soon as a slot frees.
        tasks = [
            {"id": "T1", "status": "reviewing", "files": ["a.py"], "deps": []},
            {"id": "T2", "status": "fixing", "files": ["b.py"], "deps": []},
            {"id": "T3", "status": "pending", "files": ["c.py"], "deps": []},
        ]
        by_id = {t["id"]: t for t in tasks}
        drive = _FakeDrive({"T1": 1.5, "T2": 0.1, "T3": 0.1})
        in_flight = [t for t in tasks if t["status"] in ("reviewing", "fixing")]
        live._slot_refilling_fanout(
            tasks,
            by_id,
            2,
            drive,
            threading.Lock(),
            lambda: None,
            lambda tick: False,
            initial_in_flight=in_flight,
        )
        self.assertEqual(set(drive.started_at), {"T1", "T2", "T3"})
        # In-flight tasks started together (not T2 after T1 ended).
        self.assertLess(drive.started_at["T2"], drive.ended_at["T1"])
        # Pending fan-out began while the slow in-flight task was still running.
        self.assertLess(drive.started_at["T3"], drive.ended_at["T1"])
        self.assertTrue(all(t["status"] == "done" for t in tasks))

    def test_should_stop_halts_dispatch_but_lets_in_flight_finish(self):
        tasks = [
            {"id": "T1", "status": "pending", "files": ["a.py"], "deps": []},
            {"id": "T2", "status": "pending", "files": ["b.py"], "deps": []},
        ]
        by_id = {t["id"]: t for t in tasks}
        drive = _FakeDrive({"T1": 0.3, "T2": 0.3})
        calls = []

        def should_stop(tick):
            calls.append(tick)
            return tick >= 1  # allow exactly one dispatch round

        ticks = live._slot_refilling_fanout(
            tasks, by_id, 1, drive, threading.Lock(), lambda: None, should_stop
        )
        self.assertEqual(ticks, 1)
        self.assertEqual(set(drive.started_at), {"T1"})
        self.assertEqual(tasks[0]["status"], "done")  # ran to a terminal state
        self.assertEqual(tasks[1]["status"], "pending")  # never dispatched


class LegacyEngineRefillsSlots(unittest.TestCase):
    def test_live_run_real_refills_freed_slot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm("TASK-002", "src/task-002.txt"),
                    "TASK-003": _fm("TASK-003", "src/task-003.txt"),
                },
            )
            fake = _TimedSpawn({"TASK-001": 4.0, "TASK-002": 0.05, "TASK-003": 0.05})
            live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=2,
                out_cassette=str(Path(tmp) / "run-001-x.json"),
                run_id="legacy-refill",
                spawn=fake,
            )
            self.assertEqual(set(fake.started_at), {"TASK-001", "TASK-002", "TASK-003"})
            self.assertLess(fake.started_at["TASK-003"], fake.ended_at["TASK-001"])


class ResolveMaxWorkers(unittest.TestCase):
    def _tasks(self, n):
        return [
            {"id": f"T{i}", "status": "pending", "files": [f"f{i}.py"], "deps": []}
            for i in range(n)
        ]

    def test_explicit_value_wins(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp), {"TASK-001": _fm("TASK-001", "a.txt")})
            self.assertEqual(live._resolve_max_workers(repo, self._tasks(7), 2), 2)

    def test_default_is_plan_width_capped_by_policy(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp), {"TASK-001": _fm("TASK-001", "a.txt")})
            # No policy file: width 7 capped at the default 6.
            self.assertEqual(live._resolve_max_workers(repo, self._tasks(7), None), 6)
            # Narrow plan: width wins.
            self.assertEqual(live._resolve_max_workers(repo, self._tasks(2), None), 2)
            (repo / ".worktrail").mkdir()
            (repo / ".worktrail" / "policy.yaml").write_text(
                "max_parallel_workers: 3\n"
            )
            self.assertEqual(live._resolve_max_workers(repo, self._tasks(7), None), 3)

    def test_policy_max_workers_beats_width(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp), {"TASK-001": _fm("TASK-001", "a.txt")})
            (repo / ".worktrail").mkdir()
            (repo / ".worktrail" / "policy.yaml").write_text("max_workers: 4\n")
            self.assertEqual(live._resolve_max_workers(repo, self._tasks(7), None), 4)
