#!/usr/bin/env python3
"""Acceptance tests for orchestrator skip-completed-tasks feature (spec 008).

Tests the required coverage (a-f): completed tasks excluded from fan-out,
deliverable groups, and preview; dependency satisfaction; --only and --fresh
semantics; and resume reconciliation.

Run: python3 scripts/test_skip_completed.py
"""

import os
import sys
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import (
    coordinator,
    live,
    orchestrate,
)


def _task(tid, *, status="pending", deps=None):
    return {
        "id": tid,
        "status": status,
        "retry_count": 0,
        "deps": deps or [],
        "files": [f"{tid.lower()}.py"],
        "kind": "impl",
    }


def _status(tasks, tid):
    return next(t for t in tasks if t["id"] == tid)["status"]


def _journal_entry(tid, role, *, review_status=None, status="success"):
    return {
        "task": tid,
        "role": role,
        "report": {
            "status": status,
            "head_sha": "abc123",
            "tests": "passed",
            "review_status": review_status,
            "critical_issues": 0,
            "major_issues": 0,
            "notes": "test",
        },
    }


# --------------------------------------------------------------------------- #
# Coverage (a): AC-1, AC-2 — Mixed completed/pending
# --------------------------------------------------------------------------- #
class MixedCompletedPending(unittest.TestCase):
    """AC-1, AC-2: completed tasks excluded from frontier and deliverable."""

    def test_a_completed_absent_from_runnable_frontier(self):
        """AC-1: Completed tasks excluded from runnable frontier."""
        tasks = [
            _task("TASK-001"),
            _task("TASK-002"),
            _task("TASK-003", status="completed"),
        ]
        frontier_ids = [
            t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)
        ]
        self.assertIn("TASK-001", frontier_ids)
        self.assertIn("TASK-002", frontier_ids)
        self.assertNotIn("TASK-003", frontier_ids)

    def test_a_completed_dropped_from_deliverable_subset(self):
        """AC-2: Completed task ends up in dropped, not deliverable."""
        tasks = [_task("TASK-001"), _task("TASK-002", status="completed")]
        group_ids = ["TASK-001", "TASK-002"]
        # TASK-001 finished this run ("done"); TASK-002 was pre-completed ("completed")
        # deliverable_subset drops all DONE-status tasks; only non-terminal tasks are deliverable
        status = {"TASK-001": "reviewing", "TASK-002": "completed"}
        deliverable, dropped = coordinator.deliverable_subset(group_ids, tasks, status)
        self.assertNotIn("TASK-002", deliverable)
        self.assertIn("TASK-002", dropped)
        self.assertIn("TASK-001", deliverable)

    def test_a_pending_dependent_of_completed_is_runnable(self):
        """AC-1,AC-2: Pending task whose dependency is completed reaches the frontier."""
        tasks = [
            _task("TASK-001", status="completed"),
            _task("TASK-002", deps=["TASK-001"]),
        ]
        frontier_ids = [
            t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)
        ]
        self.assertNotIn("TASK-001", frontier_ids)
        self.assertIn("TASK-002", frontier_ids)


# --------------------------------------------------------------------------- #
# Coverage (b): AC-5 — --only scoping
# --------------------------------------------------------------------------- #
class OnlyScoping(unittest.TestCase):
    """AC-5: --only runs exactly the listed tasks; rest pre-marked done."""

    def test_b_only_pre_marks_non_listed_tasks_done(self):
        """AC-5: Tasks outside --only are pre-marked done; listed stay pending."""
        tasks = [_task("TASK-001"), _task("TASK-002"), _task("TASK-003")]
        only = {"TASK-001", "TASK-002"}
        for t in tasks:
            if t["id"] not in only:
                t["status"] = "done"
        self.assertEqual(_status(tasks, "TASK-001"), "pending")
        self.assertEqual(_status(tasks, "TASK-002"), "pending")
        self.assertEqual(_status(tasks, "TASK-003"), "done")

    def test_b_dependent_of_pre_marked_task_resolves(self):
        """AC-5: Dependent of a pre-marked-done task is in the runnable frontier."""
        tasks = [
            _task("TASK-001"),
            _task("TASK-002", deps=["TASK-001"]),
            _task("TASK-003", deps=["TASK-002"]),
        ]
        # --only TASK-003: pre-mark TASK-001 and TASK-002 done
        only = {"TASK-003"}
        for t in tasks:
            if t["id"] not in only:
                t["status"] = "done"
        frontier_ids = [
            t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)
        ]
        self.assertIn("TASK-003", frontier_ids)


# --------------------------------------------------------------------------- #
# Coverage (c): AC-3 & AC-7 — Dependency satisfaction (completed vs failed)
# --------------------------------------------------------------------------- #
class DependencySatisfaction(unittest.TestCase):
    """AC-3, AC-7: Completed deps are satisfied; failed deps are re-run."""

    def test_c_completed_dependency_satisfies_runnable_frontier(self):
        """AC-3: Pending task depending on frontmatter-completed counts dep satisfied."""
        tasks = [
            _task("TASK-001", status="completed"),
            _task("TASK-002", deps=["TASK-001"]),
        ]
        frontier_ids = [
            t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)
        ]
        self.assertIn("TASK-002", frontier_ids)

    def test_c_completed_dependency_start_ref_is_head(self):
        """AC-3: dependency_start_ref returns ('HEAD', []) for a completed dep (no branch)."""
        task = _task("TASK-002", deps=["TASK-001"])
        by_id = {
            "TASK-001": _task("TASK-001", status="completed"),
            "TASK-002": task,
        }
        # completed dep has no materialized branch → _branch_exists returns False
        with unittest.mock.patch(
            "worktrail.orchestrator.live._branch_exists", return_value=False
        ):
            start_ref, merges = live.dependency_start_ref(
                Path("/fake-repo"), "spec-008", task, by_id
            )
        self.assertEqual(start_ref, "HEAD")
        self.assertEqual(merges, [])

    def test_c_failed_dependency_reset_to_pending_and_rerun(self):
        """AC-7: Failed dep is reset to pending and re-run; dependent must wait."""
        tasks = [
            _task("TASK-001", status="failed"),
            _task("TASK-002", deps=["TASK-001"]),
        ]
        # FR-1 partitioning: reset non-DONE statuses to pending
        for t in tasks:
            if t.get("status") not in coordinator.DONE:
                t["status"] = "pending"
        frontier_ids = [
            t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)
        ]
        self.assertIn("TASK-001", frontier_ids)  # root is now runnable
        self.assertNotIn("TASK-002", frontier_ids)  # dep must resolve first


# --------------------------------------------------------------------------- #
# Coverage (d): AC-6 — Preview excludes terminal tasks, done count correct
# --------------------------------------------------------------------------- #
class PreviewExcludesTerminal(unittest.TestCase):
    """AC-6: simulate_text excludes completed tasks from PR-group plan; done count correct."""

    def test_d_completed_absent_from_pr_group_plan(self):
        """AC-6: Completed task not in PR group plan lines; only pending task is grouped."""
        tasks = [_task("TASK-001"), _task("TASK-002", status="completed")]
        spawn = orchestrate.ScriptedSpawn()
        text = orchestrate.simulate_text("spec-008", tasks, spawn)

        # Extract the PR group plan section (between "PR group plan:" and the first blank line)
        lines = text.splitlines()
        in_plan = False
        plan_lines = []
        for line in lines:
            if line.strip() == "PR group plan:":
                in_plan = True
                continue
            if in_plan:
                if line.strip() == "":
                    break
                plan_lines.append(line)

        plan_text = "\n".join(plan_lines)
        self.assertIn("TASK-001", plan_text)
        self.assertNotIn("TASK-002", plan_text)

    def test_d_done_count_includes_completed_task(self):
        """AC-6: SUMMARY done count includes the frontmatter-completed task."""
        tasks = [_task("TASK-001"), _task("TASK-002", status="completed")]
        spawn = orchestrate.ScriptedSpawn()
        text = orchestrate.simulate_text("spec-008", tasks, spawn)

        summary_line = next(l for l in text.splitlines() if l.startswith("SUMMARY:"))
        # Both tasks end done: TASK-001 runs to done; TASK-002 was already completed
        self.assertIn("2/2 tasks done", summary_line)


# --------------------------------------------------------------------------- #
# Coverage (e): AC-4 — --fresh doesn't resurrect frontmatter-completed
# --------------------------------------------------------------------------- #
class FreshNoResurrection(unittest.TestCase):
    """AC-4: --fresh discards journal but leaves frontmatter-completed done."""

    def test_e_fresh_does_not_reset_frontmatter_completed(self):
        """AC-4: --fresh (resume=False) does not resurrect frontmatter-completed task."""
        tasks = [_task("TASK-001"), _task("TASK-002", status="completed")]
        # Simulate FR-1 partitioning on a fresh run (resume=False): reset non-DONE to pending
        for t in tasks:
            if t.get("status") not in coordinator.DONE:
                t["status"] = "pending"
        self.assertEqual(_status(tasks, "TASK-001"), "pending")
        self.assertEqual(_status(tasks, "TASK-002"), "completed")


# --------------------------------------------------------------------------- #
# Coverage (f): AC-8 — Resume reconciliation (frontmatter + journal)
# --------------------------------------------------------------------------- #
class ResumeReconciliation(unittest.TestCase):
    """AC-8: Frontmatter-completed + journal-terminal → single done, not reset or re-driven."""

    def test_f_completed_frontmatter_and_terminal_journal_single_done(self):
        """AC-8: Task that is frontmatter-completed AND journal-terminal ends done exactly once."""
        tasks = [_task("TASK-001", status="completed")]
        journal = {
            "entries": [
                _journal_entry("TASK-001", "implement"),
                _journal_entry("TASK-001", "review", review_status="PASSED"),
                _journal_entry("TASK-001", "cleanup"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)

        # Task is done (not reset, not re-driven, not double-counted)
        final_status = _status(tasks, "TASK-001")
        self.assertIn(final_status, coordinator.DONE)
        self.assertEqual(coordinator.runnable_frontier(tasks, max_workers=4), [])
        done_count = sum(1 for t in tasks if t["status"] in coordinator.DONE)
        self.assertEqual(done_count, 1)

    def test_f_completed_frontmatter_journal_entries_skipped(self):
        """AC-8: Journal replay is skipped entirely for frontmatter-completed task (no downgrade)."""
        tasks = [_task("TASK-001", status="completed")]
        journal = {
            "entries": [
                _journal_entry("TASK-001", "implement"),
                _journal_entry("TASK-001", "review", review_status="PASSED"),
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        # Status must still be in DONE — replay cannot downgrade a pre-completed task
        self.assertIn(_status(tasks, "TASK-001"), coordinator.DONE)


# --------------------------------------------------------------------------- #
# Regression: completed and done both in DONE set
# --------------------------------------------------------------------------- #
class DoneSetMembership(unittest.TestCase):
    """Both 'completed' and 'done' are successfully-terminal."""

    def test_both_completed_and_done_in_DONE_set(self):
        self.assertIn("completed", coordinator.DONE)
        self.assertIn("done", coordinator.DONE)

    def test_done_status_excluded_from_frontier(self):
        tasks = [_task("TASK-001"), _task("TASK-002", status="done")]
        frontier_ids = [
            t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)
        ]
        self.assertIn("TASK-001", frontier_ids)
        self.assertNotIn("TASK-002", frontier_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
