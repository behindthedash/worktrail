#!/usr/bin/env python3
"""Regression tests for the orchestrator feedback fixes.

Covers the behaviours reported from the first real parallel run:
  - item 3: informational / non-required CI checks must not gate a merge
            (verify.classify_checks / _is_informational)
  - item 5: a `--only` subset must not break dependency resolution -- deps
            outside the subset are pre-marked done so the frontier still runs
            their dependents (coordinator.runnable_frontier + DONE invariant)

Run: python3 scripts/test_feedback_fixes.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402
from worktrail.orchestrator import verify  # noqa: E402


class ClassifyChecksInformational(unittest.TestCase):
    def test_informational_named_check_does_not_gate(self):
        rollup = [
            {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
            {"name": "E2E (informational)", "status": "COMPLETED",
             "conclusion": "FAILURE"},
        ]
        pending, failing = verify.classify_checks(rollup)
        self.assertFalse(pending)
        self.assertEqual(failing, [])

    def test_non_required_check_does_not_gate(self):
        rollup = [
            {"name": "lint", "status": "COMPLETED", "conclusion": "FAILURE",
             "isRequired": False},
        ]
        pending, failing = verify.classify_checks(rollup)
        self.assertEqual(failing, [])

    def test_required_failure_still_gates(self):
        rollup = [
            {"name": "unit", "status": "COMPLETED", "conclusion": "FAILURE",
             "isRequired": True},
        ]
        pending, failing = verify.classify_checks(rollup)
        self.assertFalse(pending)
        self.assertEqual(failing, ["unit"])

    def test_informational_in_progress_not_pending(self):
        # an informational check still running must not make the gate "pending"
        rollup = [
            {"name": "E2E (informational)", "status": "IN_PROGRESS"},
            {"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"},
        ]
        pending, failing = verify.classify_checks(rollup)
        self.assertFalse(pending)
        self.assertEqual(failing, [])

    def test_is_informational_predicate(self):
        self.assertTrue(verify._is_informational({"isRequired": False}, "x"))
        self.assertTrue(verify._is_informational({}, "E2E (informational)"))
        self.assertFalse(verify._is_informational({}, "build"))
        self.assertFalse(verify._is_informational({"isRequired": True}, "build"))


class OnlyFilterDepResolution(unittest.TestCase):
    """The `--only` fix pre-marks out-of-subset deps as done instead of
    dropping them. Verify the frontier honours that (and would NOT if the dep
    were simply absent)."""

    def test_done_dependency_unblocks_dependent(self):
        self.assertIn("done", coordinator.DONE)  # invariant the fix relies on
        tasks = [
            {"id": "TASK-001", "status": "done", "deps": [], "files": ["a.py"]},
            {"id": "TASK-002", "status": "pending", "deps": ["TASK-001"],
             "files": ["b.py"]},
        ]
        frontier = coordinator.runnable_frontier(tasks, max_workers=4)
        self.assertEqual([t["id"] for t in frontier], ["TASK-002"])

    def test_dropped_dependency_would_block_dependent(self):
        # the OLD (buggy) behaviour: dep removed from the list entirely.
        tasks = [
            {"id": "TASK-002", "status": "pending", "deps": ["TASK-001"],
             "files": ["b.py"]},
        ]
        frontier = coordinator.runnable_frontier(tasks, max_workers=4)
        self.assertEqual(frontier, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
