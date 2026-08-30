#!/usr/bin/env python3
"""Extra coverage for coordinator.py: simulate, compute_levels, CLI, in-flight file locks."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator


def _task(tid, deps=None, files=None, kind="impl", status="pending"):
    return {
        "id": tid,
        "deps": deps or [],
        "files": files or [f"src/{tid.lower()}.ts"],
        "kind": kind,
        "status": status,
        "reqs": [],
    }


class SimulateTests(unittest.TestCase):
    def test_linear_chain_batches_sequentially(self):
        tasks = [
            _task("TASK-001"),
            _task("TASK-002", deps=["TASK-001"]),
            _task("TASK-003", deps=["TASK-002"]),
        ]
        batches, tail = coordinator.simulate(tasks, max_workers=3)
        self.assertEqual(batches, [["TASK-001"], ["TASK-002"], ["TASK-003"]])
        self.assertEqual(tail, [])

    def test_parallel_independent_tasks_same_batch(self):
        tasks = [_task("TASK-001"), _task("TASK-002"), _task("TASK-003")]
        batches, tail = coordinator.simulate(tasks, max_workers=3)
        self.assertEqual(len(batches), 1)
        self.assertEqual(sorted(batches[0]), ["TASK-001", "TASK-002", "TASK-003"])

    def test_tail_tasks_excluded_from_batches(self):
        tasks = [
            _task("TASK-001"),
            _task("TASK-002", kind="e2e"),
            _task("TASK-003", kind="cleanup"),
        ]
        batches, tail = coordinator.simulate(tasks, max_workers=3)
        for batch in batches:
            self.assertNotIn("TASK-002", batch)
            self.assertNotIn("TASK-003", batch)
        self.assertIn("TASK-002", tail)
        self.assertIn("TASK-003", tail)

    def test_simulate_does_not_mutate_original(self):
        tasks = [_task("TASK-001")]
        coordinator.simulate(tasks, max_workers=2)
        self.assertEqual(tasks[0]["status"], "pending")


class ComputeLevelsTests(unittest.TestCase):
    def test_root_task_is_level_0(self):
        tasks = [_task("TASK-001")]
        levels = coordinator.compute_levels(tasks)
        self.assertEqual(levels["TASK-001"], 0)

    def test_dependent_is_one_level_deeper(self):
        tasks = [_task("TASK-001"), _task("TASK-002", deps=["TASK-001"])]
        levels = coordinator.compute_levels(tasks)
        self.assertEqual(levels["TASK-002"], 1)

    def test_cycle_raises_value_error(self):
        tasks = [
            {
                "id": "A",
                "deps": ["B"],
                "files": [],
                "kind": "impl",
                "status": "pending",
            },
            {
                "id": "B",
                "deps": ["A"],
                "files": [],
                "kind": "impl",
                "status": "pending",
            },
        ]
        with self.assertRaises(ValueError) as cm:
            coordinator.compute_levels(tasks)
        self.assertIn("cycle", str(cm.exception).lower())


class RunnableFrontierInflightTests(unittest.TestCase):
    def test_in_flight_file_lock_defers_task(self):
        tasks = [
            {
                "id": "T1",
                "deps": [],
                "files": ["src/shared.ts"],
                "kind": "impl",
                "status": "implementing",
            },
            {
                "id": "T2",
                "deps": [],
                "files": ["src/shared.ts"],
                "kind": "impl",
                "status": "pending",
            },
        ]
        frontier = coordinator.runnable_frontier(tasks, max_workers=4)
        ids = [t["id"] for t in frontier]
        self.assertNotIn("T2", ids)

    def test_in_flight_task_contributes_to_worker_cap(self):
        tasks = [
            {
                "id": "T1",
                "deps": [],
                "files": ["src/a.ts"],
                "kind": "impl",
                "status": "implementing",
            },
            {
                "id": "T2",
                "deps": [],
                "files": ["src/b.ts"],
                "kind": "impl",
                "status": "pending",
            },
            {
                "id": "T3",
                "deps": [],
                "files": ["src/c.ts"],
                "kind": "impl",
                "status": "pending",
            },
        ]
        # max_workers=2: T1 in-flight + 1 pending = cap reached
        frontier = coordinator.runnable_frontier(tasks, max_workers=2)
        self.assertEqual(len(frontier), 1)


class RunnableFrontierExternalDepsTests(unittest.TestCase):
    """AC-004, AC-012, AC-013, AC-019: `external_deps_ok` gates the frontier
    alongside same-spec `deps`, without coordinator.py touching the filesystem."""

    def test_unsatisfied_external_deps_excluded_even_with_deps_done(self):
        tasks = [
            _task("TASK-001", status="done"),
            {**_task("TASK-002", deps=["TASK-001"]), "external_deps_ok": False},
        ]
        ids = [t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)]
        self.assertNotIn("TASK-002", ids)

    def test_satisfied_or_absent_external_deps_ok_is_no_regression(self):
        tasks = [
            _task("TASK-001", status="done"),
            {**_task("TASK-002", deps=["TASK-001"]), "external_deps_ok": True},
            _task("TASK-003", deps=["TASK-001"]),  # field absent -> defaults True
        ]
        ids = [t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)]
        self.assertIn("TASK-002", ids)
        self.assertIn("TASK-003", ids)

    def test_unsatisfied_deps_and_unsatisfied_external_deps_both_exclude(self):
        tasks = [
            _task("TASK-001", status="pending"),
            {**_task("TASK-002", deps=["TASK-001"]), "external_deps_ok": False},
        ]
        ids = [t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)]
        self.assertNotIn("TASK-002", ids)

    def test_external_deps_flip_makes_task_runnable_on_later_tick(self):
        tasks = [
            _task("TASK-001", status="done"),
            {**_task("TASK-002", deps=["TASK-001"]), "external_deps_ok": False},
        ]
        first = [t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)]
        self.assertNotIn("TASK-002", first)

        tasks[1]["external_deps_ok"] = True  # simulate a later tick's fresh annotation
        second = [t["id"] for t in coordinator.runnable_frontier(tasks, max_workers=4)]
        self.assertIn("TASK-002", second)


class CoordinatorCLITests(unittest.TestCase):
    def test_demo_exits_zero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = coordinator.main(["demo"])
        self.assertEqual(rc, 0)
        self.assertIn("TASK", buf.getvalue())

    def test_frontier_cli(self):
        tasks = [_task("TASK-001"), _task("TASK-002", deps=["TASK-001"])]
        data = {"tasks": tasks}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            fname = f.name
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = coordinator.main(["frontier", "--file", fname])
            self.assertEqual(rc, 0)
            self.assertIn("TASK-001", buf.getvalue())
        finally:
            os.unlink(fname)

    def test_groups_cli(self):
        tasks = [
            _task("TASK-001"),
            _task("TASK-002", deps=["TASK-001"]),
            _task("TASK-003", deps=["TASK-001"]),
        ]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"tasks": tasks}, f)
            fname = f.name
        try:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = coordinator.main(["groups", "--file", fname])
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertIn("groups", out)
        finally:
            os.unlink(fname)

    def test_load_bare_list(self):
        tasks = [_task("TASK-001")]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(tasks, f)  # bare list, not wrapped in {"tasks": [...]}
            fname = f.name
        try:
            loaded = coordinator._load(fname)
            self.assertEqual(loaded[0]["id"], "TASK-001")
        finally:
            os.unlink(fname)


class DisjointBatchesTests(unittest.TestCase):
    """item 13: mid-flight tasks resume in parallel batches that are file-disjoint and
    capped at max_workers, so two tasks never write the same file at once."""

    def _ids(self, batches):
        return [[t["id"] for t in b] for b in batches]

    def test_file_disjoint_tasks_share_a_batch(self):
        tasks = [
            _task("A", files=["a.ts"]),
            _task("B", files=["b.ts"]),
            _task("C", files=["c.ts"]),
        ]
        self.assertEqual(
            self._ids(coordinator.disjoint_batches(tasks, 3)), [["A", "B", "C"]]
        )

    def test_same_file_tasks_split_across_batches(self):
        tasks = [_task("A", files=["shared.ts"]), _task("B", files=["shared.ts"])]
        self.assertEqual(
            self._ids(coordinator.disjoint_batches(tasks, 3)), [["A"], ["B"]]
        )

    def test_capped_at_max_workers(self):
        tasks = [_task(x, files=[f"{x}.ts"]) for x in ("A", "B", "C", "D")]
        self.assertEqual(
            self._ids(coordinator.disjoint_batches(tasks, 2)), [["A", "B"], ["C", "D"]]
        )

    def test_partial_file_overlap_defers_to_next_batch(self):
        # A and B share hub.ts; C is disjoint from A so it joins A's batch.
        tasks = [
            _task("A", files=["a.ts", "hub.ts"]),
            _task("B", files=["b.ts", "hub.ts"]),
            _task("C", files=["c.ts"]),
        ]
        self.assertEqual(
            self._ids(coordinator.disjoint_batches(tasks, 3)), [["A", "C"], ["B"]]
        )

    def test_fileless_tasks_never_conflict(self):
        tasks = [_task("A", files=[]), _task("B", files=[])]
        self.assertEqual(
            self._ids(coordinator.disjoint_batches(tasks, 3)), [["A", "B"]]
        )

    def test_empty_input(self):
        self.assertEqual(coordinator.disjoint_batches([], 3), [])

    def test_zero_max_workers_treated_as_one(self):
        tasks = [_task("A", files=["a.ts"]), _task("B", files=["b.ts"])]
        self.assertEqual(
            self._ids(coordinator.disjoint_batches(tasks, 0)), [["A"], ["B"]]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
