#!/usr/bin/env python3
"""plan_groups(): shared-file edges make group PRs conflict-free (design P1).

A group is the PR unit. Before these edges, two dependency-independent tasks
writing the same file landed in *different* groups, so two concurrent PRs both
edited that file and collided at merge. `runnable_frontier`/`disjoint_batches`
prevented the concurrent write inside a run but said nothing about the PR
partition -- that gap is what these tests pin shut.

The repo's golden fixture (`sample-spec`) declares no shared files at all, so it
exercises none of this. That is why the property is asserted directly here
rather than left to a re-baselined golden.
"""

import itertools
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


def _cross_group_collisions(tasks):
    """Pairs in different, non-sequenced groups that write a common file."""
    groups = coordinator.plan_groups(tasks)
    by_id = {t["id"]: t for t in tasks}
    gof = _group_of(groups)
    stacked = {g["name"]: bool(g["depends_on"]) for g in groups}
    out = []
    for a, b in itertools.combinations(sorted(gof), 2):
        ga, gb = gof[a], gof[b]
        if ga == gb:
            continue
        # base merges first, so a group stacked on base is sequenced after it
        if "base" in (ga, gb) and stacked.get(gb if ga == "base" else ga):
            continue
        shared = coordinator._norm_files(by_id[a].get("files")) & coordinator._norm_files(
            by_id[b].get("files")
        )
        if shared:
            out.append((a, b, sorted(shared)))
    return out


class SharedFileEdgeTests(unittest.TestCase):
    def test_file_overlap_merges_dependency_independent_tasks(self):
        """The core case. Neither task depends on the other, so dependency
        unioning alone puts them in separate groups -- and two PRs then race on
        `src/shared.ts`."""
        tasks = [
            _task("TASK-001", files=["src/shared.ts", "src/a.ts"]),
            _task("TASK-002", files=["src/shared.ts", "src/b.ts"]),
        ]
        groups = coordinator.plan_groups(tasks)
        self.assertEqual(len(groups), 1, groups)
        self.assertEqual(groups[0]["tasks"], ["TASK-001", "TASK-002"])

    def test_disjoint_tasks_stay_in_separate_groups(self):
        """The guard against over-merging: file edges must not become a blunt
        instrument that serialises everything."""
        tasks = [_task("TASK-001", files=["src/a.ts"]), _task("TASK-002", files=["src/b.ts"])]
        self.assertEqual(len(coordinator.plan_groups(tasks)), 2)

    def test_tasks_without_file_scope_are_not_welded_together(self):
        """The obvious over-correction. An empty file set is "unknown", not "the
        same file as every other unknown" -- treating them as equal would
        collapse any spec whose tasks omit `files:` into one group."""
        tasks = [_task("TASK-001", files=[]), _task("TASK-002", files=[])]
        self.assertEqual(len(coordinator.plan_groups(tasks)), 2)

    def test_path_spelling_does_not_defeat_the_edge(self):
        """Must use the same normalisation as the collision check, or the group
        partition and `runnable_frontier` disagree about what a file is."""
        tasks = [
            _task("TASK-001", files=["./src/shared.ts"]),
            _task("TASK-002", files=["src/shared.ts"]),
        ]
        self.assertEqual(len(coordinator.plan_groups(tasks)), 1)

    def test_transitive_merge_through_a_middle_task(self):
        """A and C share nothing directly; both share with B, so all three must
        land together -- otherwise B's group races whichever of A/C is elsewhere."""
        tasks = [
            _task("TASK-001", files=["src/a.ts", "src/ab.ts"]),
            _task("TASK-002", files=["src/ab.ts", "src/bc.ts"]),
            _task("TASK-003", files=["src/bc.ts", "src/c.ts"]),
        ]
        groups = coordinator.plan_groups(tasks)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["tasks"]), 3)


class BaseInteractionTests(unittest.TestCase):
    def _base_spec(self, feat_files):
        """TASK-001 is a base task: a root with >= 2 transitive dependents."""
        return [
            _task("TASK-001", files=["src/base.ts", "package.json"]),
            _task("TASK-002", deps=["TASK-001"], files=["src/b.ts"]),
            _task("TASK-003", deps=["TASK-001"], files=["src/c.ts"]),
            _task("TASK-004", files=feat_files),
        ]

    def test_a_group_sharing_a_file_with_base_stacks_on_base(self):
        """Verified real, not hypothetical: datalena spec 027's feature-2 writes
        `package.json`, which base also writes, while depending on nothing in
        base. Without this it is a concurrent PR against the same file."""
        groups = coordinator.plan_groups(self._base_spec(["package.json", "src/d.ts"]))
        by_name = {g["name"]: g for g in groups}
        self.assertIn("base", by_name)
        owner = next(g for g in groups if "TASK-004" in g["tasks"])
        self.assertNotEqual(owner["name"], "base", "must sequence, not absorb into base")
        self.assertEqual(owner["depends_on"], ["base"])

    def test_a_group_disjoint_from_base_does_not_stack(self):
        groups = coordinator.plan_groups(self._base_spec(["src/d.ts"]))
        owner = next(g for g in groups if "TASK-004" in g["tasks"])
        self.assertEqual(owner["depends_on"], [])


class TailTests(unittest.TestCase):
    def test_tail_tasks_are_excluded_from_file_unioning(self):
        """Tails are held out of the fan-out on `kind` and integrated last, so
        their file overlap is not a concurrency hazard. Including them would
        union every group they touch -- an e2e task typically references most of
        the spec's files."""
        tasks = [
            _task("TASK-001", files=["src/a.ts"]),
            _task("TASK-002", files=["src/b.ts"]),
            _task("TASK-003", kind="e2e", files=["src/a.ts", "src/b.ts"]),
        ]
        groups = coordinator.plan_groups(tasks)
        self.assertEqual(len(groups), 2, groups)
        self.assertNotIn("TASK-003", _group_of(groups))


class NoCrossGroupCollisionsTests(unittest.TestCase):
    """The property the whole change exists to establish, on shapes chosen to
    break it."""

    def test_fan_out_with_a_shared_hub_file(self):
        tasks = [
            _task("TASK-001", files=["src/core.ts"]),
            _task("TASK-002", deps=["TASK-001"], files=["src/b.ts", "src/router.ts"]),
            _task("TASK-003", deps=["TASK-001"], files=["src/c.ts", "src/router.ts"]),
            _task("TASK-004", files=["src/d.ts", "src/router.ts"]),
            _task("TASK-005", kind="cleanup", files=["src/router.ts"]),
        ]
        self.assertEqual(_cross_group_collisions(tasks), [])

    def test_wide_independent_fan_out_with_pairwise_overlaps(self):
        tasks = [_task(f"TASK-{i:03d}", files=[f"src/{i}.ts", f"src/pair{i // 2}.ts"]) for i in range(1, 9)]
        self.assertEqual(_cross_group_collisions(tasks), [])

    def test_all_disjoint_stays_maximally_parallel(self):
        """The property holds trivially here; the point is that it holds WITHOUT
        sacrificing group count."""
        tasks = [_task(f"TASK-{i:03d}", files=[f"src/{i}.ts"]) for i in range(1, 7)]
        self.assertEqual(_cross_group_collisions(tasks), [])
        self.assertEqual(len(coordinator.plan_groups(tasks)), 6)


if __name__ == "__main__":
    unittest.main()
