#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_stale_bookkeeping_check.py.

Mocks dashboard.scan() directly rather than constructing real fixture repos
that reach the "stale-bookkeeping" stage (that requires git-tracked/base-
branch state dashboard.scan() itself probes) -- this module's own job is
just filtering scan()'s rows and shaping findings, which is covered without
that.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.spec_sync_sweep_stale_bookkeeping_check import (
    check_repo_stale_bookkeeping,
)


class CheckRepoStaleBookkeepingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _scan(self, rows):
        return mock.patch(
            "worktrail.router.spec_sync_sweep_stale_bookkeeping_check.dashboard.scan",
            return_value=rows,
        )

    def test_no_stale_rows_returns_empty_findings_and_no_error(self):
        rows = [{"id": "001-example", "stage": "ready-to-implement"}]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)
        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_devkit_row_emits_one_finding_per_stale_task_id_with_empty_files(self):
        rows = [
            {
                "id": "001-example",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (files already merged on base)",
                "stale_task_ids": ["TASK-001", "TASK-002"],
                "tasks": {"pending": 2},
            }
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), 2)
        self.assertEqual(
            result["findings"],
            [
                {
                    "format": "devkit",
                    "spec_id": "001-example",
                    "task_id": "TASK-001",
                    "next_action": "confirm & close (files already merged on base)",
                    "files": [],
                },
                {
                    "format": "devkit",
                    "spec_id": "001-example",
                    "task_id": "TASK-002",
                    "next_action": "confirm & close (files already merged on base)",
                    "files": [],
                },
            ],
        )

    def test_openspec_row_emits_finding_with_files_from_matching_task(self):
        rows = [
            {
                "id": "add-widget",
                "format": "openspec",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (TASK-1)",
                "stale_task_ids": ["1.1"],
                "tasks": [
                    {"id": "1.1", "files": ["src/widget.py", "tests/test_widget.py"]},
                    {"id": "1.2", "files": ["src/other.py"]},
                ],
            }
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(
            result["findings"][0],
            {
                "format": "openspec",
                "spec_id": "add-widget",
                "task_id": "1.1",
                "next_action": "confirm & close (TASK-1)",
                "files": ["src/widget.py", "tests/test_widget.py"],
            },
        )

    def test_openspec_row_task_with_no_files_yields_empty_list(self):
        rows = [
            {
                "id": "add-widget",
                "format": "openspec",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close",
                "stale_task_ids": ["1.1"],
                "tasks": [{"id": "1.1", "files": []}],
            }
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(result["findings"][0]["files"], [])

    def test_non_stale_rows_are_ignored(self):
        rows = [
            {"id": "001-example", "stage": "ready-to-implement"},
            {
                "id": "002-example",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close",
                "stale_task_ids": ["TASK-001"],
            },
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["spec_id"], "002-example")

    def test_exception_is_captured_as_error_not_propagated(self):
        with mock.patch(
            "worktrail.router.spec_sync_sweep_stale_bookkeeping_check.dashboard.scan",
            side_effect=OSError("simulated unreadable docs/specs tree"),
        ):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(result["repo"], str(self.repo))
        self.assertEqual(result["findings"], [])
        self.assertIsNotNone(result["error"])
        self.assertIn("simulated unreadable docs/specs tree", result["error"])


if __name__ == "__main__":
    unittest.main()
