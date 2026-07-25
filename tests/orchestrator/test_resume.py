#!/usr/bin/env python3
"""Tests for resumable full-real: journal replay reconstructs task state.

Covers the gap that made a harness/process kill catastrophic -- the real-repo
fan-out kept progress only in memory (`out_cassette=None`), so an interrupted
run lost all completed tasks and could not resume. `live.reconcile_from_journal`
replays the incremental run journal so a re-run skips finished roles and
continues mid-flight ones from exactly where they stopped.

Run: python3 scripts/test_resume.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402
from worktrail.orchestrator import live  # noqa: E402


def _entry(tid, role, *, review_status=None, status="success", sha="abc123"):
    """Build a journal entry in the exact shape live_run_real's record() writes."""
    return {
        "task": tid,
        "role": role,
        "report": {
            "status": status,
            "head_sha": sha,
            "tests": "passed",
            "review_status": review_status,
            "critical_issues": 0,
            "major_issues": 0,
            "notes": "test",
        },
    }


def _fresh(ids):
    return [
        {"id": i, "status": "pending", "retry_count": 0, "deps": [], "files": [f"{i}.py"]}
        for i in ids
    ]


def _status(tasks, tid):
    return next(t for t in tasks if t["id"] == tid)["status"]


class ReconcileFromJournal(unittest.TestCase):
    def test_completed_task_becomes_done_and_skipped(self):
        tasks = _fresh(["TASK-001"])
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="PASSED"),
                _entry("TASK-001", "cleanup"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(_status(tasks, "TASK-001"), "done")
        # a done task is not pending -> the runnable frontier never re-spawns it
        self.assertEqual(coordinator.runnable_frontier(tasks, 4), [])

    def test_implement_then_review_passed_lands_cleaning(self):
        tasks = _fresh(["TASK-001"])
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="PASSED"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        # mid-flight: not pending, not terminal -> live_run_real continues it
        self.assertEqual(_status(tasks, "TASK-001"), "cleaning")
        self.assertIn(_status(tasks, "TASK-001"), coordinator.IN_FLIGHT)

    def test_implement_only_lands_reviewing(self):
        tasks = _fresh(["TASK-001"])
        journal = {"entries": [_entry("TASK-001", "implement")]}
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(_status(tasks, "TASK-001"), "reviewing")

    def test_review_failed_increments_retry_and_goes_fixing(self):
        tasks = _fresh(["TASK-001"])
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="FAILED"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        t = next(x for x in tasks if x["id"] == "TASK-001")
        self.assertEqual(t["status"], "fixing")
        self.assertEqual(t["retry_count"], 1)

    def test_partial_fanout_only_replays_recorded_tasks(self):
        # the incident shape: 001 fully done, 006 through review, others untouched
        tasks = _fresh(["TASK-001", "TASK-002", "TASK-006"])
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="PASSED"),
                _entry("TASK-001", "cleanup"),
                _entry("TASK-006", "implement"),
                _entry("TASK-006", "review", review_status="PASSED"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(_status(tasks, "TASK-001"), "done")
        self.assertEqual(_status(tasks, "TASK-006"), "cleaning")
        self.assertEqual(_status(tasks, "TASK-002"), "pending")  # never started

    def test_unknown_task_entry_is_skipped_not_fatal(self):
        tasks = _fresh(["TASK-001"])
        journal = {
            "entries": [
                _entry("TASK-999", "implement"),  # not in this spec slice
                _entry("TASK-001", "implement"),
            ]
        }
        # must not raise; TASK-001 still reconciled
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(_status(tasks, "TASK-001"), "reviewing")

    def test_returns_same_entries_for_monotonic_append(self):
        tasks = _fresh(["TASK-001"])
        entries_in = [_entry("TASK-001", "implement")]
        returned = live.reconcile_from_journal(tasks, {"entries": entries_in})
        # caller seeds its live `entries` list with this so record() appends to
        # the existing history instead of truncating it
        self.assertEqual(returned, entries_in)

    def test_empty_or_missing_entries_is_noop(self):
        tasks = _fresh(["TASK-001"])
        self.assertEqual(live.reconcile_from_journal(tasks, {}), [])
        self.assertEqual(_status(tasks, "TASK-001"), "pending")

    def test_safety_net_event_marker_is_skipped_not_replayed(self):
        """A `dependency_file_drift` observability marker (appended alongside a
        real role entry when `_require_dependency_files` downgrades to a WARN)
        must never be replayed through `dispatch.apply_report` -- it has no
        `role`/`report` shape and must not affect the owning task's status."""
        tasks = _fresh(["TASK-002"])
        journal = {
            "entries": [
                {
                    "event": "dependency_file_drift",
                    "task": "TASK-002",
                    "dep_id": "TASK-001",
                    "declared_path": "helper_v1.py",
                    "dep_head_sha": "abc123",
                    "at": 1.0,
                },
                _entry("TASK-002", "implement"),
            ]
        }
        # must not raise, and the real role entry after it still reconciles normally
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(_status(tasks, "TASK-002"), "reviewing")

    def test_frontmatter_completed_not_downgraded_by_journal(self):
        """AC-002: Frontmatter-pre-marked-done task is never demoted by journal replay.

        Scenario: A task is marked 'completed' in the spec folder (frontmatter),
        and the journal has entries from a prior run. Reconciliation MUST skip
        journal replay for that task so it stays done and is never re-driven.
        """
        tasks = [
            {
                "id": "TASK-001",
                "status": "completed",
                "retry_count": 0,
                "deps": [],
                "files": ["TASK-001.py"],
            }
        ]
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="PASSED"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        # Must stay completed (in coordinator.DONE), not be downgraded to "cleaning"
        self.assertIn(_status(tasks, "TASK-001"), coordinator.DONE)
        self.assertEqual(_status(tasks, "TASK-001"), "completed")
        # must not re-enter the drive loop
        self.assertNotIn(_status(tasks, "TASK-001"), coordinator.IN_FLIGHT)

    def test_completed_and_journal_terminal_count_done_once(self):
        """AC-001: Frontmatter-completed + journal-terminal = done exactly once.

        Reconciliation must leave the task done without downgrade or double-count.
        """
        tasks = [
            {
                "id": "TASK-001",
                "status": "completed",
                "retry_count": 0,
                "deps": [],
                "files": ["TASK-001.py"],
            }
        ]
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="PASSED"),
                _entry("TASK-001", "cleanup"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(_status(tasks, "TASK-001"), "completed")
        # done count uses coordinator.DONE (includes both "done" and "completed")
        done_count = sum(1 for t in tasks if t["status"] in coordinator.DONE)
        self.assertEqual(done_count, 1)


class InterleavedJournalResumeTest(unittest.TestCase):
    """AC-013: Replaying a journal with interleaved entries and groups records
    correctly reconstructs per-task status (entries-only) without interference
    from the groups field."""

    def test_entries_replay_with_interleaved_groups(self):
        """Journal with both entries and groups: task status comes from entries."""
        tasks = _fresh(["TASK-001", "TASK-002"])
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="PASSED"),
                _entry("TASK-001", "cleanup"),
                # TASK-002 only partially done (interleaved after base group was recorded)
                _entry("TASK-002", "implement"),
            ],
            "groups": {
                "base": {
                    "pr_url": "http://pr/base",
                    "head_branch": "run/base",
                    "state": "MERGED",
                }
            },
        }
        entries = live.reconcile_from_journal(tasks, journal)

        # TASK-001 fully done; TASK-002 mid-flight (reviewing)
        self.assertEqual(_status(tasks, "TASK-001"), "done")
        self.assertEqual(_status(tasks, "TASK-002"), "reviewing")
        self.assertIn(_status(tasks, "TASK-002"), coordinator.IN_FLIGHT)
        # returned entries list is the original 4 entries for monotonic appending
        self.assertEqual(len(entries), 4)

    def test_groups_field_does_not_alter_task_replay(self):
        """A MERGED group in journal['groups'] does not affect entry-based replay."""
        tasks = _fresh(["TASK-001"])
        journal = {
            "entries": [_entry("TASK-001", "implement")],
            "groups": {
                "base": {"pr_url": "http://pr/base", "head_branch": "run/base", "state": "MERGED"}
            },
        }
        live.reconcile_from_journal(tasks, journal)
        # reconcile_from_journal reads entries only; groups must not affect task status
        self.assertEqual(_status(tasks, "TASK-001"), "reviewing")

    def test_groups_missing_from_journal_is_noop(self):
        """A journal without a 'groups' key replays entries as normal."""
        tasks = _fresh(["TASK-001"])
        journal = {
            "entries": [
                _entry("TASK-001", "implement"),
                _entry("TASK-001", "review", review_status="PASSED"),
                _entry("TASK-001", "cleanup"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        self.assertEqual(_status(tasks, "TASK-001"), "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
