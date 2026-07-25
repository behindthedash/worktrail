#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_brief.py.

Run directly (from this directory): python3 test_spec_sync_sweep_brief.py
Or as part of the go skill's suite: python3 -m pytest . -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router.spec_sync_sweep_brief import file_drift_brief
from worktrail.shared.brief_frontmatter import read_frontmatter, validate_brief


MULTI_FINDINGS = [
    {"spec": "010-alpha", "reason": "task file missing from tasks/"},
    {"spec": "010-alpha", "reason": "status regressed from done to pending"},
    {"spec": "020-beta", "reason": "knowledge-graph.json references a deleted spec"},
]

SINGLE_FINDING = [
    {"spec": "030-gamma", "reason": "spec.md missing Clarifications section"},
]


class FileDriftBriefTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.queue_base = Path(self._tmp.name)
        self.repo = Path("/home/user/projects/some-repo")

    def test_writes_exactly_one_file_for_multiple_findings_multiple_specs(self) -> None:
        file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_repo_frontmatter_equals_str_repo(self) -> None:
        path = file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        fm = read_frontmatter(path)
        self.assertEqual(fm.get("repo"), str(self.repo))

    def test_body_contains_every_finding_spec_and_reason(self) -> None:
        path = file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        content = path.read_text(encoding="utf-8")
        for finding in MULTI_FINDINGS:
            self.assertIn(finding["spec"], content)
            self.assertIn(finding["reason"], content)

    def test_frontmatter_has_drift_source_and_status_queued(self) -> None:
        path = file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        fm = read_frontmatter(path)
        self.assertEqual(fm.get("drift-source"), "spec-sync-sweep")
        self.assertEqual(fm.get("status"), "queued")

    def test_written_file_passes_validate_brief(self) -> None:
        path = file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_file_lands_under_queue_not_picked(self) -> None:
        path = file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        self.assertEqual(path.parent, self.queue_base / "queue")
        self.assertFalse((self.queue_base / "picked").exists())

    def test_creates_queue_dir_when_missing(self) -> None:
        self.assertFalse((self.queue_base / "queue").exists())
        file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        self.assertTrue((self.queue_base / "queue").is_dir())

    def test_single_finding_produces_valid_non_empty_body(self) -> None:
        path = file_drift_brief(self.repo, SINGLE_FINDING, self.queue_base)
        content = path.read_text(encoding="utf-8")
        self.assertIn(SINGLE_FINDING[0]["spec"], content)
        self.assertIn(SINGLE_FINDING[0]["reason"], content)
        ok, reason = validate_brief(path, required=("id", "status", "focus"))
        self.assertTrue(ok, reason)

    def test_no_extra_files_created_for_multi_spec_findings(self) -> None:
        # AC-008: 3 entries across 2 distinct specs still yields exactly one file.
        spec_names = {f["spec"] for f in MULTI_FINDINGS}
        self.assertEqual(len(spec_names), 2)
        file_drift_brief(self.repo, MULTI_FINDINGS, self.queue_base)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)


if __name__ == "__main__":
    unittest.main()
