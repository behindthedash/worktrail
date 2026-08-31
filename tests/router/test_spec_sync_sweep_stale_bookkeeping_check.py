#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_stale_bookkeeping_check.py.

check_repo_stale_bookkeeping() is a thin translation layer over
dashboard.scan(): it filters scan rows down to stage == "stale-bookkeeping"
and flattens their stale_task_ids into one finding per task. Tests mock
dashboard.scan() directly rather than building real fixture repos, since
dashboard.scan()'s own stale-bookkeeping detection is covered by
dashboard's own test suite.
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

    def test_matching_stage_row_yields_findings(self):
        rows = [
            {
                "id": "001-example",
                "format": "openspec",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (TASK-002)",
                "stale_task_ids": ["TASK-002"],
                "tasks": [
                    {"id": "TASK-002", "files": ["src/foo.py", "src/bar.py"]},
                ],
            }
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(result["repo"], str(self.repo))
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(
            finding,
            {
                "format": "openspec",
                "spec_id": "001-example",
                "task_id": "TASK-002",
                "next_action": "confirm & close (TASK-002)",
                "files": ["src/foo.py", "src/bar.py"],
            },
        )

    def test_multiple_stale_task_ids_yield_one_finding_each(self):
        rows = [
            {
                "id": "002-example",
                "format": "devkit",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (TASK-001, TASK-003)",
                "stale_task_ids": ["TASK-001", "TASK-003"],
            }
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), 2)
        task_ids = {f["task_id"] for f in result["findings"]}
        self.assertEqual(task_ids, {"TASK-001", "TASK-003"})
        for finding in result["findings"]:
            self.assertEqual(finding["format"], "devkit")
            self.assertEqual(finding["spec_id"], "002-example")
            self.assertEqual(finding["files"], [])

    def test_devkit_format_never_looks_up_files_even_if_tasks_present(self):
        rows = [
            {
                "id": "003-example",
                "format": "devkit",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (TASK-001)",
                "stale_task_ids": ["TASK-001"],
                "tasks": [{"id": "TASK-001", "files": ["should/not/appear.py"]}],
            }
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(result["findings"][0]["files"], [])

    def test_openspec_task_id_not_found_in_tasks_list_leaves_files_empty(self):
        rows = [
            {
                "id": "004-example",
                "format": "openspec",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (TASK-999)",
                "stale_task_ids": ["TASK-999"],
                "tasks": [{"id": "TASK-001", "files": ["src/foo.py"]}],
            }
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(result["findings"][0]["files"], [])

    def test_multiple_rows_only_stale_bookkeeping_stage_rows_contribute(self):
        rows = [
            {
                "id": "005-ready",
                "format": "openspec",
                "stage": "ready-to-implement",
                "next_action": "orchestrator",
            },
            {
                "id": "006-stale",
                "format": "openspec",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (TASK-001)",
                "stale_task_ids": ["TASK-001"],
                "tasks": [{"id": "TASK-001", "files": ["src/baz.py"]}],
            },
            {
                "id": "007-done",
                "format": "devkit",
                "stage": "done",
                "next_action": "none (backfill)",
            },
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["spec_id"], "006-stale")

    def test_no_stale_bookkeeping_row_returns_empty_findings_and_no_error(self):
        rows = [
            {
                "id": "001-example",
                "format": "openspec",
                "stage": "ready-to-implement",
                "next_action": "orchestrator",
            },
            {
                "id": "002-example",
                "format": "devkit",
                "stage": "done",
                "next_action": "none (backfill)",
            },
        ]
        with self._scan(rows):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_empty_scan_returns_empty_findings_and_no_error(self):
        with self._scan([]):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_scan_raising_is_captured_as_error_not_propagated(self):
        with mock.patch(
            "worktrail.router.spec_sync_sweep_stale_bookkeeping_check.dashboard.scan",
            side_effect=OSError("simulated unreadable docs/specs tree"),
        ):
            result = check_repo_stale_bookkeeping(self.repo)

        self.assertEqual(result["repo"], str(self.repo))
        self.assertEqual(result["findings"], [])
        self.assertIsNotNone(result["error"])
        self.assertIn("simulated unreadable docs/specs tree", result["error"])

    def test_scan_called_with_docs_specs_subdir_of_repo(self):
        with self._scan([]) as mocked_scan:
            check_repo_stale_bookkeeping(self.repo)

        mocked_scan.assert_called_once_with(self.repo / "docs" / "specs")


if __name__ == "__main__":
    unittest.main()
