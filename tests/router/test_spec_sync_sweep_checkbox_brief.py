#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_checkbox_brief.py.

Run directly (from this directory): python3 test_spec_sync_sweep_checkbox_brief.py
Or as part of the go skill's suite: python3 -m pytest . -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.spec_sync_sweep_checkbox_brief import file_checkbox_drift_brief
from worktrail.shared.brief_frontmatter import read_frontmatter, validate_brief


MULTI_HITS = [
    {
        "path": "docs/specs/010-alpha/tasks/TASK-001.md",
        "unchecked_count": 2,
        "total_count": 5,
        "sections": ["Acceptance Criteria"],
    },
    {
        "path": "docs/specs/010-alpha/tasks/TASK-002.md",
        "unchecked_count": 1,
        "total_count": 3,
        "sections": ["Definition of Done"],
    },
    {
        "path": "docs/specs/020-beta/tasks/TASK-005.md",
        "unchecked_count": 4,
        "total_count": 4,
        "sections": ["Acceptance Criteria", "Definition of Done"],
    },
]

SINGLE_HIT = [
    {
        "path": "docs/specs/030-gamma/tasks/TASK-009.md",
        "unchecked_count": 1,
        "total_count": 2,
        "sections": [],
    },
]


class FileCheckboxDriftBriefTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue_base = Path(self._tmp.name)
        self.repo = Path("/home/user/projects/some-repo")

    def test_writes_exactly_one_file_for_multiple_hits_multiple_task_files(self) -> None:
        file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_repo_frontmatter_equals_str_repo(self) -> None:
        path = file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        fm = read_frontmatter(path)
        self.assertEqual(fm.get("repo"), str(self.repo))

    def test_body_contains_every_hit_path_counts_and_sections(self) -> None:
        path = file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        content = path.read_text(encoding="utf-8")
        for hit in MULTI_HITS:
            self.assertIn(hit["path"], content)
            self.assertIn(str(hit["unchecked_count"]), content)
            self.assertIn(str(hit["total_count"]), content)
            for section in hit["sections"]:
                self.assertIn(section, content)

    def test_frontmatter_has_drift_source_and_status_queued(self) -> None:
        path = file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        fm = read_frontmatter(path)
        self.assertEqual(fm.get("drift-source"), "checkbox-drift-sweep")
        self.assertEqual(fm.get("status"), "queued")

    def test_written_file_passes_validate_brief(self) -> None:
        path = file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_file_lands_under_queue_not_picked(self) -> None:
        path = file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        self.assertEqual(path.parent, self.queue_base / "queue")
        self.assertFalse((self.queue_base / "picked").exists())

    def test_creates_queue_dir_when_missing(self) -> None:
        self.assertFalse((self.queue_base / "queue").exists())
        file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        self.assertTrue((self.queue_base / "queue").is_dir())

    def test_single_hit_produces_valid_non_empty_body(self) -> None:
        path = file_checkbox_drift_brief(self.repo, SINGLE_HIT, self.queue_base)
        content = path.read_text(encoding="utf-8")
        self.assertIn(SINGLE_HIT[0]["path"], content)
        self.assertIn("(none)", content)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_no_extra_files_created_for_multi_task_file_hits(self) -> None:
        # AC-CHG-005: 3 hits across 3 distinct task files still yields exactly one file.
        task_paths = {hit["path"] for hit in MULTI_HITS}
        self.assertEqual(len(task_paths), 3)
        file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_drift_findings_frontmatter_present_and_correctly_shaped(self) -> None:
        path = file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        fm = read_frontmatter(path)
        findings = fm.get("drift-findings")
        self.assertIsInstance(findings, list)
        self.assertEqual(len(findings), len(MULTI_HITS))
        for hit, finding in zip(MULTI_HITS, findings):
            self.assertEqual(
                finding,
                {
                    "path": hit["path"],
                    "unchecked_count": hit["unchecked_count"],
                    "total_count": hit["total_count"],
                },
            )

    def test_drift_findings_round_trips_and_brief_still_validates(self) -> None:
        path = file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
        fm = read_frontmatter(path)
        findings = fm.get("drift-findings")
        for finding in findings:
            self.assertIsInstance(finding["path"], str)
            self.assertIsInstance(finding["unchecked_count"], int)
            self.assertIsInstance(finding["total_count"], int)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_no_subprocess_or_git_invocation(self) -> None:
        # AC-CHG-008: no git operation (commit, branch, gh pr create) is ever
        # performed by the filer against any repo.
        with mock.patch("subprocess.run") as run, mock.patch(
            "subprocess.Popen"
        ) as popen, mock.patch("subprocess.call") as call, mock.patch(
            "subprocess.check_call"
        ) as check_call, mock.patch("subprocess.check_output") as check_output:
            file_checkbox_drift_brief(self.repo, MULTI_HITS, self.queue_base)
            run.assert_not_called()
            popen.assert_not_called()
            call.assert_not_called()
            check_call.assert_not_called()
            check_output.assert_not_called()


if __name__ == "__main__":
    unittest.main()
