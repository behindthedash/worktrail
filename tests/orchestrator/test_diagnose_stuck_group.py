#!/usr/bin/env python3
"""`diagnose_stuck_group` must name the actual blocking chain, not just say
"fan-out incomplete (run budget or error)" with zero signal.

Regression coverage for worktrail-go brief 20260824-165458. The pre-fix
quarantine message gave no indication of WHY a non-terminal group's fan-out
stalled -- no PIPELINE TICK ever printed, no mention of which task/edge
blocked the frontier. The most costly shape of this (worktrail-go brief
20260824-084313) is a tail-kind (e2e/cleanup) task blocking a same-group
dependent: a tail task only runs in the separate tail dispatch phase *after*
the main fan-out has already given up on it, so a task depending on one can
never enter `runnable_frontier` during the main loop and the group ends up
quarantined with no clue why.

Run: python3 -m pytest tests/orchestrator/test_diagnose_stuck_group.py
"""

from __future__ import annotations

import unittest

from worktrail.orchestrator import coordinator
from worktrail.orchestrator.live import diagnose_stuck_group

TERMINAL = coordinator.DONE | coordinator.FAILED_STATUSES


def _group(name, tasks):
    return {"name": name, "tasks": tasks, "depends_on": []}


class DiagnoseStuckGroup(unittest.TestCase):
    def test_all_terminal_returns_empty(self):
        by_id = {"1.1": {"status": "done"}, "1.2": {"status": "failed"}}
        self.assertEqual(
            diagnose_stuck_group(_group("base", ["1.1", "1.2"]), by_id, TERMINAL), ""
        )

    def test_tail_kind_blocker_named_explicitly(self):
        """THE DEFECT this fixes: task 1.2 depends on tail-kind task 1.1, which
        only runs in the tail phase after fan-out -- so 1.2 is stuck 'pending'
        forever during the main loop. The diagnosis must name 1.1 as a tail
        blocker, not just report 1.2 as generically stuck."""
        by_id = {
            "1.1": {"status": "pending", "kind": "cleanup", "deps": []},
            "1.2": {"status": "pending", "kind": "impl", "deps": ["1.1"]},
        }
        msg = diagnose_stuck_group(_group("feature-1", ["1.1", "1.2"]), by_id, TERMINAL)
        self.assertIn("1.2", msg)
        self.assertIn("tail task(s) 1.1", msg)
        self.assertIn("tail phase after fan-out", msg)

    def test_transitive_chain_walks_to_tail_root(self):
        """1.3 depends on 1.2, which depends on tail-kind 1.1 -- the root cause
        is 1.1, not the immediate (also-pending) 1.2."""
        by_id = {
            "1.1": {"status": "pending", "kind": "e2e", "deps": []},
            "1.2": {"status": "pending", "kind": "impl", "deps": ["1.1"]},
            "1.3": {"status": "pending", "kind": "impl", "deps": ["1.2"]},
        }
        msg = diagnose_stuck_group(
            _group("feature-1", ["1.1", "1.2", "1.3"]), by_id, TERMINAL
        )
        self.assertIn("task 1.3 blocked", msg)
        self.assertIn("tail task(s) 1.1", msg)

    def test_non_tail_unmet_dep_named_with_status(self):
        """A stuck task whose blocker is an ordinary (non-tail) task that never
        finished is reported with that task's id and status -- not silently
        conflated with the tail-blocker case. Uses a still-'implementing'
        blocker: 'escalated' is terminal per FAILED_STATUSES but not DONE, so
        it would (correctly) also count as an unmet dep -- this test isolates
        the plain non-done, non-tail case instead."""
        by_id = {
            "1.1": {"status": "implementing", "kind": "impl", "deps": []},
            "1.2": {"status": "pending", "kind": "impl", "deps": ["1.1"]},
        }
        msg = diagnose_stuck_group(_group("feature-1", ["1.1", "1.2"]), by_id, TERMINAL)
        self.assertIn("task 1.2 blocked", msg)
        self.assertIn("1.1", msg)
        self.assertIn("implementing", msg)

    def test_stuck_task_with_no_unmet_deps_reports_status(self):
        """A task that never entered the frontier despite having no unmet
        deps (e.g. file-collision starvation, a max_workers cap) is still
        reported -- with its own status -- rather than silently omitted."""
        by_id = {"1.1": {"status": "pending", "kind": "impl", "deps": []}}
        msg = diagnose_stuck_group(_group("feature-1", ["1.1"]), by_id, TERMINAL)
        self.assertIn("task 1.1 blocked", msg)
        self.assertIn("no unmet deps", msg)
        self.assertIn("pending", msg)

    def test_member_missing_from_by_id_is_still_reported(self):
        """Mirrors group_is_terminal's own fail-closed membership-drift
        handling: a group member absent from the task table must not be
        silently skipped by the diagnosis either."""
        by_id = {"1.1": {"status": "done"}}
        msg = diagnose_stuck_group(_group("feature-1", ["1.1", "9.9"]), by_id, TERMINAL)
        self.assertIn("9.9", msg)


if __name__ == "__main__":
    unittest.main()
