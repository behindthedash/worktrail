#!/usr/bin/env python3
"""plan_groups(): a BASE task's pure single-writer dependent chain is absorbed
into BASE instead of becoming its own stacked group.

Reproduces the shape of a real incident on worktrail's own repo (run
go-20260813-194636, group "base" = task 1.1 alone, PR #379): 1.1 qualified for
BASE purely on fan-out (>=2 transitive dependents via 1.2/1.3/3.2), while its
immediate dependent chain 1.2 -> 1.3 -- a serial rewrite of the exact same file
as 1.1, with no branching of its own -- landed in a separate stacked group
("feature-1"). The pipeline scheduler's base-before-dependent gate left that
group idle while base's own single-task PR fought a merge conflict against
advancing `main`; the resolve loop exhausted its strikes before the chain's
group ever got to integrate, orphaning 1.2/1.3's fully-done, reviewed, tested
work and cascading the whole dependent group to quarantine. Recovery required
manually rebuilding the group branch from task 1.3's commit chain.

This absorption pass closes the split that created the race, not the race
itself: a pure same-file continuation buys no parallelism over folding it into
base (it is already strictly serialized behind base by the shared-file edge),
so there is no reason to pay for it with a second PR and a same-file ordering
hazard.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator


def _task(tid, deps=None, files=None, kind="impl", status="pending"):
    return {
        "id": tid,
        "deps": deps or [],
        "files": [] if files == [] else (files or [f"src/{tid.lower()}.ts"]),
        "kind": kind,
        "status": status,
        "reqs": [],
    }


def _group_of(groups):
    return {tid: g["name"] for g in groups for tid in g["tasks"]}


def _incident_shaped_tasks():
    """Mirrors the real incident's task shape (ids match the actual spec)."""
    return [
        _task("1.1", files=["src/worktrail/router/consolidate_cluster.py"]),
        _task(
            "1.2", deps=["1.1"], files=["src/worktrail/router/consolidate_cluster.py"]
        ),
        _task(
            "1.3", deps=["1.2"], files=["src/worktrail/router/consolidate_cluster.py"]
        ),
        _task("2.1", files=["src/worktrail/router/check_brief_staleness.py"]),
        _task("3.1", files=["openspec/specs/stale-brief-precheck/spec.md"]),
        _task(
            "3.2",
            deps=["1.1", "1.2", "1.3"],
            files=["tests/router/test_consolidate_cluster.py"],
        ),
        _task(
            "3.3", deps=["2.1"], files=["tests/router/test_check_brief_staleness.py"]
        ),
    ]


class SerialChainAbsorptionTests(unittest.TestCase):
    def test_serial_same_file_chain_absorbed_into_base(self):
        """Pins the fix: reproduces the real incident's corrected grouping."""
        groups = coordinator.plan_groups(_incident_shaped_tasks())
        gof = _group_of(groups)
        self.assertEqual(gof["1.1"], "base")
        self.assertEqual(gof["1.2"], "base")
        self.assertEqual(gof["1.3"], "base")

    def test_real_fan_out_consumer_still_gets_its_own_stacked_group(self):
        """3.2 depends on the whole chain but writes a different file -- it
        keeps genuine fan-out value as its own group, sequenced after base."""
        groups = coordinator.plan_groups(_incident_shaped_tasks())
        by_name = {g["name"]: g for g in groups}
        owner = next(g for g in groups if "3.2" in g["tasks"])
        self.assertNotEqual(owner["name"], "base")
        self.assertIn("base", owner["depends_on"])
        self.assertNotIn("3.2", by_name["base"]["tasks"])

    def test_unrelated_groups_unaffected(self):
        groups = coordinator.plan_groups(_incident_shaped_tasks())
        gof = _group_of(groups)
        self.assertNotEqual(gof["2.1"], "base")
        self.assertNotEqual(gof["3.1"], "base")
        self.assertEqual(
            gof["2.1"], gof["3.3"], "3.3 depends only on 2.1 -- same group"
        )


class NonContinuationsAreNotAbsorbedTests(unittest.TestCase):
    def _base_spec(self, feat_files):
        """TASK-001 is a base task: a root with >= 2 transitive dependents."""
        return [
            _task("TASK-001", files=["src/base.ts", "package.json"]),
            _task("TASK-002", deps=["TASK-001"], files=["src/b.ts"]),
            _task("TASK-003", deps=["TASK-001"], files=["src/c.ts"]),
            _task("TASK-004", files=feat_files),
        ]

    def test_dependent_with_unrelated_files_is_not_absorbed(self):
        """TASK-002/003 depend on base but write files base never declares --
        new scope, not a continuation. Existing stacking behavior is unchanged
        (covered directly in test_plan_groups_file_edges.py); this only pins
        that the new absorption pass does not additionally pull them in."""
        groups = coordinator.plan_groups(self._base_spec(["src/d.ts"]))
        gof = _group_of(groups)
        self.assertNotEqual(gof["TASK-002"], "base")
        self.assertNotEqual(gof["TASK-003"], "base")

    def test_dependent_with_an_extra_dep_is_not_absorbed(self):
        """A real join point (more than one in-impl dep) is never a pure
        continuation, even when its files are a subset of base's."""
        tasks = [
            _task("TASK-001", files=["src/shared.ts"]),
            _task("TASK-005", files=["src/other.ts"]),
            _task(
                "TASK-002",
                deps=["TASK-001", "TASK-005"],
                files=["src/shared.ts"],
            ),
        ]
        groups = coordinator.plan_groups(tasks)
        gof = _group_of(groups)
        self.assertNotEqual(gof.get("TASK-002"), "base")

    def test_same_file_fork_absorbs_neither_sibling(self):
        """Two dependents of the same base task both writing a subset of
        base's files, with no dependency between them, is a genuine fork --
        not a chain. There is no safe ordering to prefer between the two, so
        neither is absorbed; grouping falls back to its pre-existing
        (safe) behavior."""
        tasks = [
            _task("TASK-001", files=["src/shared.ts", "src/other.ts"]),
            _task("TASK-002", deps=["TASK-001"], files=["src/shared.ts"]),
            _task("TASK-003", deps=["TASK-001"], files=["src/shared.ts"]),
        ]
        groups = coordinator.plan_groups(tasks)
        gof = _group_of(groups)
        self.assertNotEqual(gof["TASK-002"], "base")
        self.assertNotEqual(gof["TASK-003"], "base")

    def test_empty_file_scope_dependent_is_not_absorbed(self):
        """Unknown file scope is never treated as 'subset of base' -- matches
        the existing 'no file scope is not the same as shared scope' rule."""
        tasks = [
            _task("TASK-001", files=["src/shared.ts"]),
            _task("TASK-002", deps=["TASK-001"], files=[]),
        ]
        groups = coordinator.plan_groups(tasks)
        gof = _group_of(groups)
        self.assertNotEqual(gof["TASK-002"], "base")

    def test_no_organic_base_is_a_no_op(self):
        """No root fans out to >=2 dependents -- nothing to absorb into."""
        tasks = [
            _task("TASK-001", files=["src/a.ts"]),
            _task("TASK-002", deps=["TASK-001"], files=["src/a.ts"]),
        ]
        groups = coordinator.plan_groups(tasks)
        gof = _group_of(groups)
        self.assertNotIn("base", gof.values())

    def test_tail_tasks_never_absorbed(self):
        tasks = _incident_shaped_tasks() + [
            _task(
                "4.1",
                deps=["3.2"],
                files=["src/worktrail/router/consolidate_cluster.py"],
                kind="e2e",
            )
        ]
        groups = coordinator.plan_groups(tasks)
        for g in groups:
            self.assertNotIn("4.1", g["tasks"])


if __name__ == "__main__":
    unittest.main()
