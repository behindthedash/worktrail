#!/usr/bin/env python3
"""`_format_migration_quarantine_warning()`: explicit warning when a
migration-touching group is quarantined but other groups still proceeded to
PR without a declared dependency on it.

Reproduces the shape of the real incident (datalena run go-20260817-162424,
spec 099 retire-root-organization-id-sentinel): BASE absorbed the migration
task via `migration_patterns`, but BASE's own integration smoke test failed
and it was quarantined. Only groups with a declared `deps`/shared-file edge on
BASE cascade-quarantine (`plan_groups`'s own documented limitation -- see
"Why migration tasks are forced into BASE" in coordinator.py); a group whose
code merely consumes the new schema, with no such edge, still opened a PR with
no migration in its branch ancestry, and nothing warned. This is brief
20260817-174854's "at minimum" fix direction: an explicit WARNING.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator
from worktrail.orchestrator import live


def _task(tid, deps=None, files=None):
    return {
        "id": tid,
        "deps": deps or [],
        "files": files or [f"src/{tid.lower()}.ts"],
        "kind": "impl",
        "status": "done",
        "reqs": [],
    }


def _datalena_shaped_tasks():
    """BASE contains the migration; a sibling group's task consumes the new
    column in application code with no declared `deps`/shared-file edge on
    the migration task -- the exact undetected-coupling shape from the
    incident."""
    return [
        _task("1.1", files=["alembic/versions/20260817_retire_sentinel.py"]),
        _task("2.1", deps=["1.1"], files=["api/app/services/org_a.py"]),
        _task("2.2", deps=["1.1"], files=["api/app/services/org_b.py"]),
        # No `deps` on 1.1 and no shared file -- the undetected coupling.
        _task("3.1", files=["api/app/routers/unrelated_consumer.py"]),
    ]


class MigrationQuarantineWarningTests(unittest.TestCase):
    def setUp(self):
        self.patterns = ["alembic/versions/*.py"]
        self.tasks = _datalena_shaped_tasks()
        self.groups = coordinator.plan_groups(self.tasks, migration_patterns=self.patterns)

    def test_warns_when_base_quarantined_and_sibling_proceeded(self):
        quarantined = {"base": "integration smoke test failed"}
        note = live._format_migration_quarantine_warning(
            self.groups, self.tasks, self.patterns, quarantined
        )
        self.assertIsNotNone(note)
        self.assertIn("MIGRATION SAFETY", note)
        self.assertIn("base", note)
        # 3.1's group (undeclared coupling) proceeded and must be named.
        undeclared_group = next(g["name"] for g in self.groups if "3.1" in g["tasks"])
        self.assertIn(undeclared_group, note)

    def test_no_warning_when_nothing_quarantined(self):
        note = live._format_migration_quarantine_warning(self.groups, self.tasks, self.patterns, {})
        self.assertIsNone(note)

    def test_no_warning_without_migration_patterns(self):
        quarantined = {"base": "integration smoke test failed"}
        note = live._format_migration_quarantine_warning(self.groups, self.tasks, None, quarantined)
        self.assertIsNone(note)
        note = live._format_migration_quarantine_warning(self.groups, self.tasks, (), quarantined)
        self.assertIsNone(note)

    def test_no_warning_when_quarantined_group_has_no_migration_task(self):
        """A non-migration group quarantined for unrelated reasons must not
        trigger this warning -- it's specifically about the migration-carrying
        group being unavailable while siblings proceed."""
        non_migration_group = next(g["name"] for g in self.groups if g["name"] != "base")
        quarantined = {non_migration_group: "merge conflict"}
        note = live._format_migration_quarantine_warning(
            self.groups, self.tasks, self.patterns, quarantined
        )
        self.assertIsNone(note)

    def test_no_warning_when_every_group_quarantined(self):
        """Nothing 'proceeded' without the migration if the whole run is
        quarantined -- no dangling PR to warn about."""
        quarantined = {g["name"]: "run aborted" for g in self.groups}
        note = live._format_migration_quarantine_warning(
            self.groups, self.tasks, self.patterns, quarantined
        )
        self.assertIsNone(note)

    def test_cascaded_dependent_quarantine_leaves_only_undeclared_sibling(self):
        """2.1/2.2's group stacks on base (declared dep on 1.1) and would
        normally cascade-quarantine per live.py's own dependency check; only
        3.1's group (the undeclared coupling) is what this warning exists
        to catch."""
        dependent_group = next(g["name"] for g in self.groups if "2.1" in g["tasks"])
        undeclared_group = next(g["name"] for g in self.groups if "3.1" in g["tasks"])
        quarantined = {
            "base": "integration smoke test failed",
            dependent_group: f"base group 'base' quarantined",
        }
        note = live._format_migration_quarantine_warning(
            self.groups, self.tasks, self.patterns, quarantined
        )
        self.assertIsNotNone(note)
        proceeded_list = note.split("with no declared dependency on it:", 1)[1]
        self.assertIn(undeclared_group, proceeded_list)
        self.assertNotIn(dependent_group, proceeded_list)


if __name__ == "__main__":
    unittest.main()
