#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep_checkbox_check.py.

Run directly (from this directory): python3 test_spec_sync_sweep_checkbox_check.py
Or as part of the go skill's suite: python3 -m pytest . -q

Fixtures mirror scripts/tests/test_audit_completed_task_checkboxes.py's shape
(a real docs/specs/<id>/tasks/TASK-*.md tree, status: completed frontmatter,
body checkboxes) so the integration test can compare
check_repo_checkbox_drift() against calling audit_repo() directly on the
same fixture repo.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router.spec_sync_sweep_checkbox_check import (
    audit_repo,
    check_repo_checkbox_drift,
)


def _write_task(tasks_dir: Path, name: str, status: str, body: str) -> Path:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    path = tasks_dir / name
    path.write_text(
        f"---\nid: {name.removesuffix('.md')}\nstatus: {status}\n---\n{body}"
    )
    return path


class CheckRepoCheckboxDriftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.specs_root = self.repo / "docs" / "specs"

    def test_no_drift_returns_empty_findings_and_no_error(self):
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "## Acceptance Criteria\n- [x] done thing\n- [x] also done\n",
        )
        result = check_repo_checkbox_drift(self.repo)
        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_single_drift_task_returns_one_finding_with_correct_fields(self):
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "## Acceptance Criteria\n- [x] done thing\n- [ ] undone thing\n",
        )

        result = check_repo_checkbox_drift(self.repo)

        self.assertIsNone(result["error"])
        self.assertEqual(result["repo"], str(self.repo))
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(
            finding["path"], str(Path("docs/specs/001-example/tasks/TASK-001.md"))
        )
        self.assertEqual(finding["unchecked_count"], 1)
        self.assertEqual(finding["total_count"], 2)
        self.assertEqual(finding["sections"], ["Acceptance Criteria"])

    def test_multi_file_drift_returns_one_finding_per_affected_task_file(self):
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "## Acceptance Criteria\n- [ ] undone thing\n",
        )
        _write_task(
            self.specs_root
            / "001-example"
            / "changes"
            / "2026-07-01--fixture"
            / "tasks",
            "TASK-CHG-001.md",
            "completed",
            "## Definition of Done (DoD)\n- [ ] left unchecked\n",
        )
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-002.md",
            "completed",
            "## Acceptance Criteria\n- [x] fully checked\n",
        )

        result = check_repo_checkbox_drift(self.repo)

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), 2)
        paths = {f["path"] for f in result["findings"]}
        self.assertEqual(
            paths,
            {
                str(Path("docs/specs/001-example/tasks/TASK-001.md")),
                str(
                    Path(
                        "docs/specs/001-example/changes/2026-07-01--fixture/tasks/TASK-CHG-001.md"
                    )
                ),
            },
        )

    def test_exception_is_captured_as_error_not_propagated(self):
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "## Acceptance Criteria\n- [x] done thing\n",
        )
        with mock.patch(
            "worktrail.router.spec_sync_sweep_checkbox_check.audit_repo",
            side_effect=OSError("simulated unreadable docs/specs tree"),
        ):
            result = check_repo_checkbox_drift(self.repo)

        self.assertEqual(result["repo"], str(self.repo))
        self.assertEqual(result["findings"], [])
        self.assertIsNotNone(result["error"])
        self.assertIn("simulated unreadable docs/specs tree", result["error"])

    def test_no_task_files_is_not_an_error(self):
        self.specs_root.mkdir(parents=True)
        (self.specs_root / "README.md").write_text(
            "not a task file\n", encoding="utf-8"
        )
        result = check_repo_checkbox_drift(self.repo)
        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_missing_docs_specs_dir_is_not_an_error(self):
        result = check_repo_checkbox_drift(self.repo)
        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_h1_level_checkboxes_with_no_subheadings_not_flagged(self):
        # devops TASK-001/TASK-002 shape: fully-checked checklist directly
        # under the H1 title, no ## Acceptance Criteria / ## Definition of
        # Done (DoD) subheadings present anywhere in the body.
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "# TASK-001\n\n- [x] step one\n- [x] step two\n",
        )
        result = check_repo_checkbox_drift(self.repo)
        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_checklist_outside_body_entirely_not_flagged(self):
        # mailbox-service TASK-002/003/004 shape: the checklist lives in
        # frontmatter (success-criteria:), so the body has zero checkboxes
        # under the named sections AND zero checkboxes anywhere -- nothing
        # to audit, not drift.
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "# TASK-001\n\nSee frontmatter `success-criteria:` for the checklist.\n",
        )
        result = check_repo_checkbox_drift(self.repo)
        self.assertEqual(
            result, {"repo": str(self.repo), "findings": [], "error": None}
        )

    def test_never_mutates_checked_repo(self):
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "## Acceptance Criteria\n- [ ] undone thing\n",
        )

        before = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(self.specs_root.rglob("*"))
            if p.is_file()
        }

        check_repo_checkbox_drift(self.repo)

        after = {
            p: (p.read_bytes(), p.stat().st_mtime_ns)
            for p in sorted(self.specs_root.rglob("*"))
            if p.is_file()
        }
        self.assertEqual(before, after)


class CheckRepoCheckboxDriftIntegrationTests(unittest.TestCase):
    """Real audit_completed_task_checkboxes.audit_repo integration:
    check_repo_checkbox_drift()'s findings must match calling audit_repo()
    directly on the same fixture repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.specs_root = self.repo / "docs" / "specs"

    def test_findings_match_direct_audit_repo_call(self):
        _write_task(
            self.specs_root / "001-example" / "tasks",
            "TASK-001.md",
            "completed",
            "## Acceptance Criteria\n- [x] done thing\n- [ ] undone thing\n",
        )
        _write_task(
            self.specs_root
            / "001-example"
            / "changes"
            / "2026-07-01--fixture"
            / "tasks",
            "TASK-CHG-001.md",
            "completed",
            "## Definition of Done (DoD)\n- [ ] left unchecked\n",
        )

        direct_hits = audit_repo(self.repo)
        result = check_repo_checkbox_drift(self.repo)

        self.assertIsNone(result["error"])
        self.assertEqual(len(result["findings"]), len(direct_hits))
        expected = {
            (
                str(hit.path.relative_to(self.repo)),
                hit.unchecked_count,
                hit.total_count,
                tuple(hit.sections),
            )
            for hit in direct_hits
        }
        actual = {
            (f["path"], f["unchecked_count"], f["total_count"], tuple(f["sections"]))
            for f in result["findings"]
        }
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
