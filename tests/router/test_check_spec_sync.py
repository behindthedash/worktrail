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

    def test_missing_status_header_is_flagged_when_all_terminal(self):
        # Regression fixture for devops PR #184 / spec 004-governance-automation:
        # all tasks completed, but the parent spec markdown file has no
        # "**Status**:" line at all (parent_spec_status() returns None).
        # Previously silently skipped -- Check B only fired when a status
        # value was found and matched the disallow-list.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        write(
            self.spec_dir / "2026-01-01--fixture.md",
            "# Functional Specification: Fixture\n\n"
            "**Spec ID**: 000-fixture\n**Date**: 2026-01-01\n**Version**: 1.0\n",
        )
        failures = check_spec(self.spec_dir)
        self.assertEqual(len(failures), 1)
        self.assertIn("no Status header", failures[0])

    def test_missing_status_header_not_flagged_when_in_progress(self):
        # A spec with no Status header at all but still-pending tasks must
        # not be flagged -- the check stays gated on all_terminal, same as
        # every other Check B case.
        make_task(self.spec_dir, "TASK-001", "pending")
        self.task_summary({"TASK-001": "pending"})
        write(
            self.spec_dir / "2026-01-01--fixture.md",
            "# Functional Specification: Fixture\n\n"
            "**Spec ID**: 000-fixture\n**Date**: 2026-01-01\n**Version**: 1.0\n",
        )
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_user_request_capture_never_shadows_parent_spec(self):
        # Regression fixture for worktrail's own 001-task-ac-verification-gate
        # (2026-08-13): find_parent_spec() takes the lexicographically last
        # candidate, so the devkit capture artifact user-request.md (which
        # carries no Status header by design) shadowed spec.md and flagged a
        # phantom "no Status header" drift on a correctly-stamped spec --
        # fleet-wide, since every devkit spec dir ships a user-request.md.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec("Completed")
        write(self.spec_dir / "user-request.md", "Please build the fixture.\n")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_user_request_alone_is_not_a_parent_spec(self):
        # A spec dir whose only top-level markdown is the capture artifact has
        # no parent spec to check -- skip, don't flag the capture file.
        make_task(self.spec_dir, "TASK-001", "completed")
        write(self.spec_dir / "user-request.md", "Please build the fixture.\n")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_brainstorming_notes_never_shadows_parent_spec(self):
        # Regression fixture for behindthedash spec 001-release-notes-self-audit
        # (2026-08-14): the same find_parent_spec() lexicographic-last hazard as
        # user-request.md above, but for brainstorming-notes.md, which carries no
        # Status header by design and sorts after a dated spec filename (digits
        # sort before lowercase letters in ASCII). Every PR in behindthedash
        # false-positived on this until AUX_FILENAMES included it.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec("Completed")
        write(self.spec_dir / "brainstorming-notes.md", "Some brainstorming.\n")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_ready_to_implement_near_miss_is_flagged(self):
        # Regression fixture for devops PR #184 / spec
        # 102-fleet-dependabot-classifier: "Ready to implement" is a
        # near-miss of the disallow-list's "ready for implementation" and
        # was previously missed by exact-phrase matching.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec("Ready to implement")
        failures = check_spec(self.spec_dir)
        self.assertEqual(len(failures), 1)
        self.assertIn("Ready to implement", failures[0])

    def test_shipped_and_complete_statuses_still_pass(self):
        # Verified against the real fleet (survey across all repos'
        # docs/specs/ Status headers, 2026-08-13): "Shipped" and "Complete"/
        # "Completed" are legitimate terminal statuses in active use
        # (datalena, others) that are not "Implemented" or "Backfill". The
        # disallow-list approach -- not an allow-list -- is what keeps these
        # passing; a naive allow-list of {Implemented, Backfill} would
        # regress them into false positives fleet-wide.
        for status in ("Shipped", "Complete", "Completed", "Implemented (PRs #1450, #1451)"):
            with self.subTest(status=status):
                make_task(self.spec_dir, "TASK-001", "completed")
                self.task_summary({"TASK-001": "completed"})
                self.parent_spec(status)
                self.assertEqual(check_spec(self.spec_dir), [])


if __name__ == "__main__":
    unittest.main()
