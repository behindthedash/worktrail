#!/usr/bin/env python3
"""plan_groups(): migration tasks are folded into BASE (`migration_patterns`).

Reproduces the shape of a real incident (datalena's embed-widget-auth-hardening
run, PRs #2138/#2139/#2144): a migration task (1.2) with only one dependent
(its own round-trip test, 5.4) does not clear the >=2-transitive-dependents bar
for BASE, and none of the consumer tasks (2.1 et al) declared a `deps` edge on
it -- they only depend on the model-defining task (1.1), which shares no `files`
entry with the migration either. `plan_groups()`'s existing dependency graph and
shared-file union-find therefore placed the migration in its own group,
independent of the consumer group; when that group was later quarantined on an
unrelated flaky test, the consumer group merged anyway and left `dev` broken
for any already-migrated database.

`migration_patterns` closes the specific failure mode (not the missing-deps-edge
root cause): any task whose declared files match a configured glob is folded
into BASE, so it can never be split into an independently-quarantinable group.
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


def _embed_widget_shaped_tasks():
    """Mirrors the real incident's task shape (numbers match the actual spec)."""
    return [
        _task("1.1", files=["api/app/models/embed.py"]),
        _task("1.2", files=["api/migrations/versions/20260806_01_embed_widget_app.py"]),
        _task("2.1", deps=["1.1"], files=["api/app/services/embed_widget_applications.py"]),
        _task("2.2", deps=["1.1"], files=["api/app/services/embed.py"]),
        _task("3.1", deps=["1.1"], files=["api/app/routers/embed_secure.py"]),
        _task("3.2", deps=["2.1", "3.1"], files=["api/app/routers/embed_secure.py"]),
        _task(
            "5.4",
            deps=["1.2"],
            files=["api/tests/migrations/test_20260806_01_embed_widget_app.py"],
        ),
    ]


class TouchesMigrationTests(unittest.TestCase):
    def test_matches_declared_file_against_pattern(self):
        t = _task("1.2", files=["api/migrations/versions/foo.py"])
        self.assertTrue(coordinator._touches_migration(t, ["api/migrations/versions/*.py"]))

    def test_no_match_for_unrelated_file(self):
        t = _task("2.1", files=["api/app/services/embed_widget_applications.py"])
        self.assertFalse(coordinator._touches_migration(t, ["api/migrations/versions/*.py"]))

    def test_empty_patterns_never_match(self):
        t = _task("1.2", files=["api/migrations/versions/foo.py"])
        self.assertFalse(coordinator._touches_migration(t, []))


class MigrationDefaultBehaviorTests(unittest.TestCase):
    def test_omitted_patterns_is_a_no_op(self):
        """No behavior change for existing callers that never pass the new kwarg."""
        tasks = _embed_widget_shaped_tasks()
        self.assertEqual(coordinator.plan_groups(tasks), coordinator.plan_groups(tasks, ()))

    def test_without_patterns_migration_lands_outside_base(self):
        """Pins the pre-fix shape: reproduces the actual incident's grouping."""
        tasks = _embed_widget_shaped_tasks()
        groups = coordinator.plan_groups(tasks)
        gof = _group_of(groups)
        self.assertIn("1.1", gof)
        self.assertNotEqual(gof["1.2"], gof["1.1"], "migration should NOT be in base pre-fix")
        # The consumer task never declared a dep on the migration, so it
        # is not even in the migration's own feature group.
        self.assertNotEqual(gof["1.2"], gof["2.1"])


class MigrationPatternsFixTests(unittest.TestCase):
    def test_migration_task_folded_into_base(self):
        tasks = _embed_widget_shaped_tasks()
        groups = coordinator.plan_groups(tasks, migration_patterns=["api/migrations/versions/*.py"])
        gof = _group_of(groups)
        self.assertEqual(gof["1.2"], "base")
        self.assertEqual(gof["1.1"], "base")

    def test_migration_only_dependent_stays_with_it_via_deps(self):
        """5.4 (migration test) depends on 1.2 -- it should now stack on base too,
        since 1.2 is now base and 5.4's only in-impl dep is 1.2."""
        tasks = _embed_widget_shaped_tasks()
        groups = coordinator.plan_groups(tasks, migration_patterns=["api/migrations/versions/*.py"])
        by_name = {g["name"]: g for g in groups}
        five_four_group = next(g for g in groups if "5.4" in g["tasks"])
        self.assertIn("base", five_four_group["depends_on"])
        self.assertNotIn("5.4", by_name["base"]["tasks"])

    def test_consumer_group_unaffected_still_stacks_on_base(self):
        tasks = _embed_widget_shaped_tasks()
        groups = coordinator.plan_groups(tasks, migration_patterns=["api/migrations/versions/*.py"])
        consumer_group = next(g for g in groups if "2.1" in g["tasks"])
        self.assertIn("base", consumer_group["depends_on"])

    def test_degenerate_single_migration_task_gets_its_own_base(self):
        """No organic base (no root fans out to >=2 dependents) -- a lone
        migration-only run still lands in a well-formed `base` group."""
        tasks = [_task("1.1", files=["api/migrations/versions/only.py"])]
        groups = coordinator.plan_groups(tasks, migration_patterns=["api/migrations/versions/*.py"])
        gof = _group_of(groups)
        self.assertEqual(gof["1.1"], "base")

    def test_non_matching_patterns_leave_grouping_unchanged(self):
        tasks = _embed_widget_shaped_tasks()
        default_groups = coordinator.plan_groups(tasks)
        patterned_groups = coordinator.plan_groups(tasks, migration_patterns=["nope/**/*.sql"])
        self.assertEqual(_group_of(default_groups), _group_of(patterned_groups))

    def test_tail_tasks_never_matched(self):
        """A migration glob should never pull an e2e/cleanup tail task into base --
        plan_groups() already excludes TAIL_KINDS before grouping runs at all."""
        tasks = _embed_widget_shaped_tasks() + [
            _task("6.1", deps=["3.2"], files=["api/migrations/versions/unrelated.py"], kind="e2e")
        ]
        groups = coordinator.plan_groups(tasks, migration_patterns=["api/migrations/versions/*.py"])
        for g in groups:
            self.assertNotIn("6.1", g["tasks"])


class GroupContainsMigrationTaskTests(unittest.TestCase):
    def test_base_group_with_folded_migration_task_is_true(self):
        tasks = _embed_widget_shaped_tasks()
        patterns = ["api/migrations/versions/*.py"]
        groups = coordinator.plan_groups(tasks, migration_patterns=patterns)
        base = next(g for g in groups if g["name"] == "base")
        self.assertTrue(coordinator.group_contains_migration_task(base, tasks, patterns))

    def test_non_migration_group_is_false(self):
        tasks = _embed_widget_shaped_tasks()
        patterns = ["api/migrations/versions/*.py"]
        groups = coordinator.plan_groups(tasks, migration_patterns=patterns)
        consumer_group = next(g for g in groups if "3.2" in g["tasks"])
        self.assertFalse(
            coordinator.group_contains_migration_task(consumer_group, tasks, patterns)
        )

    def test_empty_patterns_is_always_false(self):
        tasks = _embed_widget_shaped_tasks()
        groups = coordinator.plan_groups(tasks)
        base = next(g for g in groups if g["name"] == "base")
        self.assertFalse(coordinator.group_contains_migration_task(base, tasks, []))


if __name__ == "__main__":
    unittest.main()
