#!/usr/bin/env python3
"""Unit tests for check_spec_sync.py.

Run directly (from this directory): python3 test_check_spec_sync.py
Or as part of the go skill's suite: python3 -m pytest . -q

The "drift" fixtures below reproduce the exact historical regression this
guard was written to catch (gracefully-giving-back's spec
026-authenticated-feedback-capture between PR #546 and PR #553: every
TASK-*.md said status: completed while the task-plan summary table and the
parent spec's Status header still described a pre-implementation state).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_spec_sync import check_spec


def write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_task(spec_dir: Path, task_id: str, status: str) -> None:
    write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"""---
id: {task_id}
title: "Test task"
spec: docs/specs/000-fixture/fixture.md
status: {status}
dependencies: []
---

## Definition of Done
- [x] done
""",
    )


class CheckSpecSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec_dir = Path(self.tmp.name) / "000-fixture"
        self.spec_dir.mkdir(parents=True)

    def parent_spec(self, status: str) -> None:
        write(
            self.spec_dir / "2026-01-01--fixture.md",
            f"""# Functional Specification: Fixture

**Spec ID**: 000-fixture
**Date**: 2026-01-01
**Status**: {status}
**Version**: 1.0
""",
        )

    def parent_spec_colon_inside_bold(self, status: str) -> None:
        # The "**Status:** X" convention (colon inside the closing bold
        # markers) used by datalena specs 062-073, e.g.
        # 062-agent-manifest-registry -- distinct from the "**Status**: X"
        # form above.
        write(
            self.spec_dir / "2026-01-01--fixture.md",
            f"""# Functional Specification: Fixture

**Spec ID:** 000-fixture
**Date:** 2026-01-01
**Status:** {status}
**Version:** 1.0
""",
        )

    def task_summary(self, rows: dict[str, str]) -> None:
        lines = [
            "# Task Plan: Fixture",
            "",
            "| Task | Title | Dependencies | Status |",
            "|---|---|---|---|",
        ]
        for task_id, status in rows.items():
            lines.append(f"| {task_id} | Test | None | {status} |")
        write(self.spec_dir / "2026-01-01--fixture--tasks.md", "\n".join(lines) + "\n")

    def legacy_task_index(self, rows: dict[str, str]) -> None:
        lines = [
            "# Task List: Fixture",
            "",
            "| Task ID | Title | Technical Focus | Status | Dependencies |",
            "|---|---|---|---|---|",
        ]
        for task_id, box in rows.items():
            lines.append(f"| [{task_id}](tasks/{task_id}.md) | Test | x | {box} | - |")
        write(self.spec_dir / "2026-01-01--fixture--tasks.md", "\n".join(lines) + "\n")

    def test_clean_spec_passes(self):
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec("Implemented")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_regression_fixture_reproduces_historical_drift(self):
        # Mirrors 026-authenticated-feedback-capture at commit d9fc9aa: all
        # tasks completed, summary table + parent spec still pre-implementation.
        make_task(self.spec_dir, "TASK-001", "completed")
        make_task(self.spec_dir, "TASK-002", "completed")
        self.task_summary({"TASK-001": "pending", "TASK-002": "pending"})
        self.parent_spec("Ready for Implementation")
        failures = check_spec(self.spec_dir)
        self.assertEqual(len(failures), 3)  # 2 task-row mismatches + 1 parent-status mismatch
        self.assertTrue(any("TASK-001" in f and "pending" in f for f in failures))
        self.assertTrue(any("TASK-002" in f and "pending" in f for f in failures))
        self.assertTrue(any("Ready for Implementation" in f for f in failures))

    def test_colon_inside_bold_status_header_is_detected(self):
        # Regression fixture for the "**Status:** X" form (colon inside the
        # closing bold markers) -- previously silently skipped Check B
        # entirely (parent_spec_status() returned None), giving zero
        # coverage to every spec using this equally common convention (e.g.
        # datalena's 062-073 agent-native platform epics).
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec_colon_inside_bold("Ready for Implementation")
        failures = check_spec(self.spec_dir)
        self.assertEqual(len(failures), 1)
        self.assertIn("Ready for Implementation", failures[0])

    def test_legacy_checkbox_table_is_skipped_not_flagged(self):
        make_task(self.spec_dir, "TASK-001", "completed")
        self.legacy_task_index({"TASK-001": "[ ]"})
        self.parent_spec("Implemented")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_in_progress_spec_is_not_flagged(self):
        # Real work-in-progress: one task still pending. Parent Status says
        # Draft, which is correct for an unfinished spec -- must not fire.
        make_task(self.spec_dir, "TASK-001", "completed")
        make_task(self.spec_dir, "TASK-002", "pending")
        self.task_summary({"TASK-001": "completed", "TASK-002": "pending"})
        self.parent_spec("Draft")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_backfill_status_is_not_flagged(self):
        # Disallow-list, not an allow-list: legitimate non-standard status
        # values (e.g. "Backfill") must pass even though every task is done.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec("Backfill")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_no_tasks_dir_is_skipped(self):
        self.parent_spec("Draft")
        self.assertEqual(check_spec(self.spec_dir), [])


if __name__ == "__main__":
    unittest.main()
