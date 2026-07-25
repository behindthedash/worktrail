#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_check.py.

Run directly (from this directory): python3 test_spec_sync_sweep_check.py
Or as part of the go skill's suite: python3 -m pytest . -q

Fixtures mirror test_check_spec_sync.py's shape (a real docs/specs/<id>/
tree with TASK-*.md, a "*--tasks.md" summary table, and a parent spec
markdown file) so the integration test can compare check_repo_drift()
against calling check_spec() directly on the same spec directory.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.check_spec_sync import check_spec
from worktrail.router.spec_sync_sweep_check import check_repo_drift


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


def make_parent_spec(spec_dir: Path, status: str) -> None:
    write(
        spec_dir / "2026-01-01--fixture.md",
        f"""# Functional Specification: Fixture

**Spec ID**: {spec_dir.name}
**Date**: 2026-01-01
**Status**: {status}
**Version**: 1.0
""",
    )


def make_task_summary(spec_dir: Path, rows: dict[str, str]) -> None:
    lines = [
        "# Task Plan: Fixture",
        "",
        "| Task | Title | Dependencies | Status |",
        "|---|---|---|---|",
    ]
    for task_id, status in rows.items():
        lines.append(f"| {task_id} | Test | None | {status} |")
    write(spec_dir / "2026-01-01--fixture--tasks.md", "\n".join(lines) + "\n")


def make_drifting_spec(specs_root: Path, spec_name: str) -> Path:
    """A spec whose task-plan summary disagrees with task frontmatter --
    produces exactly one check_spec() failure message."""
    spec_dir = specs_root / spec_name
    make_task(spec_dir, "TASK-001", "completed")
    make_task_summary(spec_dir, {"TASK-001": "pending"})
    make_parent_spec(spec_dir, "Implemented")
    return spec_dir


def make_clean_spec(specs_root: Path, spec_name: str) -> Path:
    spec_dir = specs_root / spec_name
    make_task(spec_dir, "TASK-001", "completed")
    make_task_summary(spec_dir, {"TASK-001": "completed"})
    make_parent_spec(spec_dir, "Implemented")
    return spec_dir


class CheckRepoDriftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.specs_root = self.repo / "docs" / "specs"

    def test_no_drift_returns_empty_findings_and_no_error(self):
        make_clean_spec(self.specs_root, "001-fixture-one")
        result = check_repo_drift(self.repo)
        self.assertEqual(result, {"repo": str(self.repo), "findings": [], "error": None})

    def test_single_spec_drift_returns_one_tagged_finding(self):
        spec_dir = make_drifting_spec(self.specs_root, "001-fixture-one")
        expected_reason = check_spec(spec_dir)
        self.assertEqual(len(expected_reason), 1)

        result = check_repo_drift(self.repo)
        self.assertIsNone(result["error"])
        self.assertEqual(result["repo"], str(self.repo))
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(finding["spec"], "001-fixture-one")
        self.assertEqual(finding["reason"], expected_reason[0])

    def test_multi_spec_drift_returns_one_finding_per_affected_spec(self):
        make_drifting_spec(self.specs_root, "001-fixture-one")
        make_drifting_spec(self.specs_root, "002-fixture-two")
        make_clean_spec(self.specs_root, "003-fixture-clean")

        result = check_repo_drift(self.repo)
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), 2)
        specs_named = {f["spec"] for f in result["findings"]}
        self.assertEqual(specs_named, {"001-fixture-one", "002-fixture-two"})
        for finding in result["findings"]:
            self.assertTrue(finding["reason"])

    def test_exception_is_captured_as_error_not_propagated(self):
        make_clean_spec(self.specs_root, "001-fixture-one")
        with mock.patch(
            "worktrail.router.spec_sync_sweep_check.check_spec",
            side_effect=OSError("simulated unreadable spec directory"),
        ):
            result = check_repo_drift(self.repo)
        self.assertEqual(result["repo"], str(self.repo))
        self.assertEqual(result["findings"], [])
        self.assertIsNotNone(result["error"])
        self.assertIn("simulated unreadable spec directory", result["error"])

    def test_no_numbered_spec_subdirs_is_not_an_error(self):
        self.specs_root.mkdir(parents=True)
        (self.specs_root / "README.md").write_text("not a spec dir\n", encoding="utf-8")
        result = check_repo_drift(self.repo)
        self.assertEqual(result, {"repo": str(self.repo), "findings": [], "error": None})

    def test_missing_docs_specs_dir_is_not_an_error(self):
        result = check_repo_drift(self.repo)
        self.assertEqual(result, {"repo": str(self.repo), "findings": [], "error": None})

    def test_never_mutates_checked_repo(self):
        make_drifting_spec(self.specs_root, "001-fixture-one")

        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(self.specs_root.rglob("*"))
            if p.is_file()
        }

        check_repo_drift(self.repo)

        after = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(self.specs_root.rglob("*"))
            if p.is_file()
        }
        self.assertEqual(before, after)


class CheckRepoDriftIntegrationTests(unittest.TestCase):
    """Real check_spec_sync.check_spec integration: check_repo_drift()'s
    per-spec findings must match calling check_spec() directly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.specs_root = self.repo / "docs" / "specs"

    def test_findings_match_direct_check_spec_call(self):
        spec_dir = make_drifting_spec(self.specs_root, "026-fixture")
        direct_failures = check_spec(spec_dir)

        result = check_repo_drift(self.repo)

        self.assertIsNone(result["error"])
        reported_reasons = [f["reason"] for f in result["findings"] if f["spec"] == "026-fixture"]
        self.assertEqual(reported_reasons, direct_failures)


if __name__ == "__main__":
    unittest.main()
