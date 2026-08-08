#!/usr/bin/env python3
"""Regression tests for run-budget + resume (handoff 20260610-031500).

Two defects:

  1. Bug: on budget exhaustion, live_run_real journaled EVERY not-yet-started task
     as failed. On resume, reconcile_from_journal read those failed entries and
     marked the tasks FAILED in-memory, so the fan-out looked complete and the
     run jumped straight to integrate (which SPLIT-quarantined all of them).

     Fix: budget exhaustion records a top-level `budget_stopped_at` marker and
     leaves pending/in-flight tasks alone. Resume continues the fan-out.

  2. Bug: the budget clock counted every second between run_start and now,
     including session-limit sleeps inside spawn_claude_p (a ~4h reset window
     burned the entire 4h budget even though no work was happening).

     Fix: spawn_claude_p reports `paused_s` (cumulative session-limit sleep) in
     SpawnResult. The orchestrator subtracts sum(_budget_pauses) from elapsed
     wall-clock when comparing to the budget.

Run: python3 scripts/test_budget_resume.py
"""

import datetime
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402


def _init_repo(root: Path, tasks_frontmatter: dict) -> Path:
    repo = root / "repo"
    (repo / "docs" / "specs" / "001-x" / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    for tid, fm in tasks_frontmatter.items():
        (repo / "docs" / "specs" / "001-x" / "tasks" / f"{tid}.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, capture_output=True
    )
    return repo


def _fm(tid, files, deps="", **extra):
    lines = [
        f"id: {tid}",
        "status: pending",
        f"dependencies: [{deps}]",
        f"files: [{files}]",
        "kind: impl",
    ]
    for k, v in extra.items():
        lines.append(f"{k}: {v}")
    return "---\n" + "\n".join(lines) + "\n---\n\nbody\n"


class FakeSpawn:
    """Commits per task and returns a valid report-back. Optional per-call pause
    to simulate session-limit sleeps (used for tests that exercise the paused-time
    accumulator without going through spawnlib). Thread-safe."""

    def __init__(self, paused_s_per_task=None):
        self.calls = []
        self.lock = threading.Lock()
        self.paused_s_per_task = paused_s_per_task or {}

    def __call__(self, role, task, wt):
        tid = task["id"]
        with self.lock:
            self.calls.append((tid, role))
        if role in ("implement", "fix"):
            f = Path(wt) / "src" / f"{tid.lower()}.txt"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(f"{tid} {role}\n")
            subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
            subprocess.run(
                ["git", "-C", str(wt), "commit", "-q", "-m", f"{role} {tid}"],
                check=True, capture_output=True,
            )
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()[:8] or "00000000"
        rs = '"PASSED"' if role == "review" else "null"
        paused = self.paused_s_per_task.get(tid, 0.0)
        return spawnlib.SpawnResult(
            text=(
                f'```json\n{{"task":"{tid}","step":"{role}","status":"success",'
                f'"head_sha":"{sha}","review_status":{rs}}}\n```'
            ),
            usage={},
            paused_s=paused,
        )


# =========================================================================== #
# Test 1: budget exhaustion leaves pending tasks pending (not failed)
# =========================================================================== #
class BudgetExhaustionLeavesTasksPending(unittest.TestCase):
    def test_live_run_real_marks_budget_stopped_not_failed(self):
        # 2 dependent tasks + near-zero budget: TASK-001 runs (tick 1 completes),
        # then budget is exceeded before tick 2 can dispatch TASK-002.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm("TASK-002", "src/task-002.txt", deps="TASK-001"),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            fake = FakeSpawn()
            res = live.live_run_real(
                repo, "docs/specs/001-x",
                max_workers=2, out_cassette=journal,
                run_id="budget-test", spawn=fake,
                run_budget=0.001,  # near-zero: fires after tick 1
            )
            by_id = {t["id"]: t for t in res["tasks"]}
            # TASK-001 completed normally. TASK-002 must be PENDING (not failed)
            # so a resume picks it up instead of quarantine-jumping to integrate.
            self.assertEqual(by_id["TASK-001"]["status"], "done")
            self.assertEqual(by_id["TASK-002"]["status"], "pending")
            # Journal must carry the top-level budget_stopped_at marker for audit.
            data = json.loads(Path(journal).read_text())
            self.assertIn("budget_stopped_at", data)
            # And NO per-task failure entries for TASK-002 (the bug): only the
            # successful entries from TASK-001 should be present.
            task_ids = {e["task"] for e in data["entries"]}
            self.assertNotIn("TASK-002", task_ids)


# =========================================================================== #
# Test 2: resume after budget stop continues fan-out
# =========================================================================== #
class ResumeAfterBudgetStop(unittest.TestCase):
    def test_resume_dispatches_tasks_left_pending(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm("TASK-002", "src/task-002.txt", deps="TASK-001"),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            fake1 = FakeSpawn()
            # Phase 1: tiny budget -> TASK-001 runs, stops before TASK-002.
            live.live_run_real(
                repo, "docs/specs/001-x",
                max_workers=2, out_cassette=journal,
                run_id="budget-test", spawn=fake1,
                run_budget=0.001, resume=True,
            )
            # Phase 2: resume with unlimited budget -> TASK-002 must be dispatched.
            fake2 = FakeSpawn()
            res = live.live_run_real(
                repo, "docs/specs/001-x",
                max_workers=2, out_cassette=journal,
                run_id="budget-test", spawn=fake2,
                run_budget=0,  # 0 disables budget (unlimited)
                resume=True,
            )
            by_id = {t["id"]: t for t in res["tasks"]}
            # Both tasks done: resume completed the fan-out instead of skipping.
            self.assertEqual(by_id["TASK-001"]["status"], "done")
            self.assertEqual(by_id["TASK-002"]["status"], "done")
            # fake2 should have been called for TASK-002 (resume dispatched it).
            phase2_tasks = {tid for (tid, _) in fake2.calls}
            self.assertIn("TASK-002", phase2_tasks)


# =========================================================================== #
# Test 3: spawnlib paused_s is correctly accumulated
# =========================================================================== #
class SpawnlibPausedSeconds(unittest.TestCase):
    def setUp(self):
        self._orig = spawnlib.subprocess.run

    def tearDown(self):
        spawnlib.subprocess.run = self._orig

    def test_no_session_limit_returns_zero_paused(self):
        Proc = namedtuple("Proc", "returncode stdout stderr")
        spawnlib.subprocess.run = lambda *a, **k: Proc(0, "ok output", "")
        out = spawnlib.spawn_claude_p("p", "/tmp", retries=1, sleep=lambda *_: None)
        self.assertEqual(out.paused_s, 0.0)

    def test_session_limit_sleep_accumulates_paused(self):
        # Use a fixed fake datetime so the reset window is deterministic.
        Proc = namedtuple("Proc", "returncode stdout stderr")
        limit_msg = "You've hit your session limit. Your limit resets at 10:05am."

        # Script: 1 session-limit response, then 1 success.
        outcomes = [
            Proc(0, limit_msg, ""),
            Proc(0, "ok output", ""),
        ]
        fake_i = iter(outcomes)
        spawnlib.subprocess.run = lambda *a, **k: next(fake_i)

        # Inject a now that's BEFORE 10:05am so the parse works without wrap-to-tomorrow.
        fixed_now = datetime.datetime(2026, 6, 10, 10, 0, 0)

        # Patch parse_session_limit_reset default now via a wrapper.
        orig_parse = spawnlib.parse_session_limit_reset

        def _parse(text, now=None):
            return orig_parse(text, now=fixed_now)

        spawnlib.parse_session_limit_reset = _parse
        try:
            slept = []
            out = spawnlib.spawn_claude_p(
                "p", "/tmp", retries=1, sleep=lambda s: slept.append(s),
                session_limit_waits=2,
            )
        finally:
            spawnlib.parse_session_limit_reset = orig_parse

        self.assertEqual(out.text, "ok output")
        # paused_s matches the cumulative sleep amount.
        self.assertGreater(out.paused_s, 0.0)
        self.assertAlmostEqual(out.paused_s, sum(slept), places=1)

    def test_multiple_session_limit_waits_accumulate(self):
        Proc = namedtuple("Proc", "returncode stdout stderr")
        # 3 session-limit hits then success: all 3 sleeps accumulate.
        outcomes = [
            Proc(0, "hit your session limit, resets 10:05am", ""),
            Proc(0, "hit your session limit, resets 10:05am", ""),
            Proc(0, "hit your session limit, resets 10:05am", ""),
            Proc(0, "ok output", ""),
        ]
        fake_i = iter(outcomes)
        spawnlib.subprocess.run = lambda *a, **k: next(fake_i)

        fixed_now = datetime.datetime(2026, 6, 10, 10, 0, 0)
        orig_parse = spawnlib.parse_session_limit_reset

        def _parse(text, now=None):
            return orig_parse(text, now=fixed_now)

        spawnlib.parse_session_limit_reset = _parse
        try:
            slept = []
            out = spawnlib.spawn_claude_p(
                "p", "/tmp", retries=1, sleep=lambda s: slept.append(s),
                session_limit_waits=5,
            )
        finally:
            spawnlib.parse_session_limit_reset = orig_parse

        self.assertEqual(out.text, "ok output")
        self.assertEqual(len(slept), 3)
        self.assertAlmostEqual(out.paused_s, sum(slept), places=1)


# =========================================================================== #
# Test 4: budget clock excludes session-limit sleeps
# =========================================================================== #
class BudgetClockExcludesPausedTime(unittest.TestCase):
    def test_budget_does_not_fire_when_spawn_reports_large_pause(self):
        # Setup: 3 chained tasks (2 ticks). fake spawn returns paused_s=1000.0
        # on the first call. The budget is 10s but the paused time makes
        # effective_elapsed tiny, so the budget should NOT fire and all tasks
        # complete.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm("TASK-002", "src/task-002.txt", deps="TASK-001"),
                    "TASK-003": _fm("TASK-003", "src/task-003.txt", deps="TASK-001"),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            # Every spawn reports 1000s of paused time: without the fix this would
            # blow the 10s budget by tick 2.
            fake = FakeSpawn(paused_s_per_task={"TASK-001": 1000.0, "TASK-002": 1000.0, "TASK-003": 1000.0})
            res = live.live_run_real(
                repo, "docs/specs/001-x",
                max_workers=3, out_cassette=journal,
                run_id="test-pause", spawn=fake,
                run_budget=10,  # 10s budget, but paused time excludes ~3000s
            )
            by_id = {t["id"]: t for t in res["tasks"]}
            # No budget_stopped_at: all tasks completed within effective elapsed.
            data = json.loads(Path(journal).read_text())
            self.assertNotIn("budget_stopped_at", data)
            self.assertEqual(by_id["TASK-001"]["status"], "done")
            self.assertEqual(by_id["TASK-002"]["status"], "done")
            self.assertEqual(by_id["TASK-003"]["status"], "done")


# =========================================================================== #
# Test 5: pipeline scheduler respects same budget-stop fix
# =========================================================================== #
class PipelineBudgetStopLeavesTasksPending(unittest.TestCase):
    def test_pipeline_run_does_not_journal_pending_tasks_failed(self):
        # 2 dependent tasks + near-zero budget. Either tick 1 runs (TASK-001
        # succeeds) before the budget fires, or the budget fires before any tick
        # completes. Either way: TASK-002 must NOT be journaled as failed,
        # budget_stopped_at must be set.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(
                Path(tmp),
                {
                    "TASK-001": _fm("TASK-001", "src/task-001.txt"),
                    "TASK-002": _fm("TASK-002", "src/task-002.txt", deps="TASK-001"),
                },
            )
            journal = str(Path(tmp) / "run-001-x.json")
            fake = FakeSpawn()
            live._pipeline_scheduler(
                repo=repo, spec_rel="docs/specs/001-x",
                remote="origin", base="main", model="haiku",
                max_workers=2, timeout=60, resume=False,
                only=None, role_models=None,
                run_budget=0.001,
                journal_path=journal, run_id="pipe-bud",
                _spawn=fake,
                _integrate_one=lambda *a, **kw: None,
                _make_verifier=lambda: _FakeVerifier(),
            )
            data = json.loads(Path(journal).read_text())
            # Top-level marker present -- budget stop was recorded for audit.
            self.assertIn("budget_stopped_at", data)
            # The bug we fixed: no per-task failure entries for TASK-002 (the
            # pending one). The journal entries must only contain successful
            # work, not a "run budget exceeded" failure record.
            task_ids = {e["task"] for e in data.get("entries", [])}
            self.assertNotIn("TASK-002", task_ids)
            # Confirm no entry carries the telltale failure reason either.
            reasons = [e.get("report", {}).get("notes", "") for e in data.get("entries", [])]
            self.assertFalse(
                any("run budget" in r for r in reasons),
                f"journal entries must not carry run-budget failure reason; got: {reasons}",
            )
            # Any group quarantined purely by the budget stop must carry the
            # structured budget_exhausted reason (safely resumable), not a
            # generic/failure category -- quarantine_selfcheck.py and the
            # dashboard rely on this to skip human triage for these groups.
            for group in data.get("groups", {}).values():
                if group.get("state") == "QUARANTINED":
                    self.assertEqual(group.get("quarantine_reason"), "budget_exhausted")


class _FakeVerifier:
    """Minimal verifier stand-in for pipeline tests."""

    def verify_one(self, *a, **kw):
        pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
