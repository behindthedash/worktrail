#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_stale_bookkeeping_brief.py.

Run directly (from this directory): python3 test_spec_sync_sweep_stale_bookkeeping_brief.py
Or as part of the go skill's suite: python3 -m pytest . -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.spec_sync_sweep_stale_bookkeeping_brief import (
    file_stale_bookkeeping_brief,
)
from worktrail.shared.brief_frontmatter import read_frontmatter, validate_brief

MULTI_FINDINGS = [
    {
        "format": "openspec",
        "spec_id": "stale-bookkeeping-sweep-check",
        "task_id": "1.1",
        "next_action": "close out task 1.1",
        "files": ["src/worktrail/router/spec_sync_sweep_stale_bookkeeping_check.py"],
    },
    {
        "format": "devkit",
        "spec_id": "001-task-ac-verification-gate",
        "task_id": "TASK-003",
        "next_action": "mark TASK-003 done",
        "files": [],
    },
]

SINGLE_FINDING = [
    {
        "format": "openspec",
        "spec_id": "some-change",
        "task_id": "2.4",
        "next_action": "reconcile 2.4",
        "files": [],
    },
]


class FileStaleBookkeepingBriefTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue_base = Path(self._tmp.name)
        self.repo = Path("/home/user/projects/some-repo")

    def test_writes_exactly_one_file_for_multiple_findings(self) -> None:
        file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_repo_frontmatter_equals_str_repo(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        fm = read_frontmatter(path)
        self.assertEqual(fm.get("repo"), str(self.repo))

    def test_body_contains_every_finding_spec_task_and_next_action(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        content = path.read_text(encoding="utf-8")
        for finding in MULTI_FINDINGS:
            self.assertIn(finding["spec_id"], content)
            self.assertIn(finding["task_id"], content)
            self.assertIn(finding["next_action"], content)

    def test_frontmatter_has_drift_source_and_status_queued(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        fm = read_frontmatter(path)
        self.assertEqual(fm.get("drift-source"), "stale-bookkeeping-sweep")
        self.assertEqual(fm.get("status"), "queued")

    def test_written_file_passes_validate_brief(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_file_lands_under_queue_not_picked(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        self.assertEqual(path.parent, self.queue_base / "queue")
        self.assertFalse((self.queue_base / "picked").exists())

    def test_creates_queue_dir_when_missing(self) -> None:
        self.assertFalse((self.queue_base / "queue").exists())
        file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        self.assertTrue((self.queue_base / "queue").is_dir())

    def test_single_finding_produces_valid_non_empty_body(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, SINGLE_FINDING, self.queue_base)
        content = path.read_text(encoding="utf-8")
        self.assertIn(SINGLE_FINDING[0]["spec_id"], content)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_no_extra_files_created_for_multiple_findings(self) -> None:
        spec_ids = {finding["spec_id"] for finding in MULTI_FINDINGS}
        self.assertEqual(len(spec_ids), 2)
        file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_drift_findings_frontmatter_present_and_correctly_shaped(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        fm = read_frontmatter(path)
        findings = fm.get("drift-findings")
        self.assertIsInstance(findings, list)
        self.assertEqual(len(findings), len(MULTI_FINDINGS))
        for expected, finding in zip(MULTI_FINDINGS, findings):
            self.assertEqual(
                finding,
                {
                    "format": expected["format"],
                    "spec_id": expected["spec_id"],
                    "task_id": expected["task_id"],
                    "next_action": expected["next_action"],
                    "files": expected["files"],
                },
            )

    def test_drift_findings_round_trips_and_brief_still_validates(self) -> None:
        path = file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        fm = read_frontmatter(path)
        findings = fm.get("drift-findings")
        for finding in findings:
            self.assertIsInstance(finding["format"], str)
            self.assertIsInstance(finding["spec_id"], str)
            self.assertIsInstance(finding["task_id"], str)
            self.assertIsInstance(finding["files"], list)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_no_subprocess_or_git_invocation(self) -> None:
        with (
            mock.patch("subprocess.run") as run,
            mock.patch("subprocess.Popen") as popen,
            mock.patch("subprocess.call") as call,
            mock.patch("subprocess.check_call") as check_call,
            mock.patch("subprocess.check_output") as check_output,
        ):
            file_stale_bookkeeping_brief(self.repo, MULTI_FINDINGS, self.queue_base)
            run.assert_not_called()
            popen.assert_not_called()
            call.assert_not_called()
            check_call.assert_not_called()
            check_output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
