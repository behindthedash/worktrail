#!/usr/bin/env python3
"""Unit tests for spec_sync_sweep.py.

Run directly (from this directory): python3 test_spec_sync_sweep.py
Or as part of the go skill's suite: python3 -m pytest . -q
"""

from __future__ import annotations

import fcntl
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from worktrail.router import spec_sync_sweep as sss


def _git_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, text=True, check=True)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_task(spec_dir: Path, task_id: str, status: str) -> None:
    _write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"""---
id: {task_id}
title: "Test task"
spec: docs/specs/000-fixture/fixture.md
status: {status}
dependencies: []
---

## Definition of Done (DoD)
- [x] done
""",
    )


def _make_parent_spec(spec_dir: Path, status: str) -> None:
    _write(
        spec_dir / "2026-01-01--fixture.md",
        f"""# Functional Specification: Fixture

**Spec ID**: {spec_dir.name}
**Date**: 2026-01-01
**Status**: {status}
**Version**: 1.0
""",
    )


def _make_task_summary(spec_dir: Path, rows: dict[str, str]) -> None:
    lines = [
        "# Task Plan: Fixture",
        "",
        "| Task | Title | Dependencies | Status |",
        "|---|---|---|---|",
    ]
    for task_id, status in rows.items():
        lines.append(f"| {task_id} | Test | None | {status} |")
    _write(spec_dir / "2026-01-01--fixture--tasks.md", "\n".join(lines) + "\n")


def _make_drifted_repo(root: Path, name: str) -> Path:
    """A git repo with a docs/specs/ tree whose task-plan summary disagrees
    with task frontmatter, producing exactly one drift finding."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task(spec_dir, "TASK-001", "completed")
    _make_task_summary(spec_dir, {"TASK-001": "pending"})
    _make_parent_spec(spec_dir, "Implemented")
    return repo


def _make_clean_repo(root: Path, name: str) -> Path:
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / "001-fixture"
    _make_task(spec_dir, "TASK-001", "completed")
    _make_task_summary(spec_dir, {"TASK-001": "completed"})
    _make_parent_spec(spec_dir, "Implemented")
    return repo


def _make_checkbox_drift_task(spec_dir: Path, task_id: str) -> None:
    """A `status: completed` task file with an unchecked box under an
    audited section — triggers a checkbox-drift finding but, being outside
    any task-plan summary table, never a spec-sync-drift finding."""
    _write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"""---
id: {task_id}
title: "Checkbox drift fixture"
spec: docs/specs/000-fixture/fixture.md
status: completed
dependencies: []
---

## Acceptance Criteria
- [x] done thing
- [ ] undone thing
""",
    )


def _make_checkbox_drifted_repo(root: Path, name: str) -> Path:
    """A git repo with checkbox-drift hits and zero spec-sync-drift findings."""
    repo = root / name
    _git_init(repo)
    _make_checkbox_drift_task(repo / "docs" / "specs" / "002-checkbox", "TASK-001")
    return repo


def _make_drifted_and_checkbox_drifted_repo(root: Path, name: str) -> Path:
    """A git repo with both spec-sync-drift and checkbox-drift findings."""
    repo = _make_drifted_repo(root, name)
    _make_checkbox_drift_task(repo / "docs" / "specs" / "002-checkbox", "TASK-001")
    return repo


def _seed_existing_brief(
    queue_base: Path, repo: Path, drift_source: str, brief_id: str = "existing"
) -> None:
    queue_dir = queue_base / "queue"
    queue_dir.mkdir(parents=True, exist_ok=True)
    _write(
        queue_dir / f"{brief_id}.md",
        f"""---
id: {brief_id}
status: queued
focus: pre-existing drift
repo: {repo}
drift-source: {drift_source}
---

## Focus

pre-existing drift
""",
    )


class RunSweepOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repos_root = self.root / "repos"
        self.queue_base = self.root / "queue-base"
        self.lock_path = self.root / "sweep.lock"

    def test_clean_and_drifted_repo_are_classified_and_drifted_is_filed(self) -> None:
        clean = _make_clean_repo(self.repos_root, "clean-repo")
        drifted = _make_drifted_repo(self.repos_root, "drifted-repo")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertFalse(record["skipped_overlap"])
        self.assertIn(str(clean), record["checked"])
        self.assertIn(str(drifted), record["checked"])
        self.assertIn(str(drifted), record["drifted"])
        self.assertIn(str(drifted), record["filed"])
        self.assertNotIn(str(clean), record["drifted"])
        self.assertNotIn(str(clean), record["filed"])
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_repo_with_check_error_is_recorded_as_failed_and_others_still_processed(self) -> None:
        _make_clean_repo(self.repos_root, "clean-repo")
        errored = _make_drifted_repo(self.repos_root, "errored-repo")

        def fake_check(repo: Path):
            if repo.name == "errored-repo":
                return {"repo": str(repo), "findings": [], "error": "simulated failure"}
            return _real_check_repo_drift(repo)

        # Patch at the module level used inside run_sweep.
        with mock.patch("worktrail.router.spec_sync_sweep.check_repo_drift", side_effect=fake_check):
            record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(errored), record["checked"])
        self.assertIn(str(errored), record["failed"])
        self.assertNotIn(str(errored), record["drifted"])
        self.assertNotIn(str(errored), record["filed"])
        # The other repo in the same run is still fully processed.
        self.assertEqual(len(record["checked"]), 2)

    def test_drifted_repo_with_existing_unresolved_brief_is_skipped_not_filed(self) -> None:
        drifted = _make_drifted_repo(self.repos_root, "drifted-repo")

        queue_dir = self.queue_base / "queue"
        queue_dir.mkdir(parents=True)
        _write(
            queue_dir / "existing.md",
            f"""---
id: existing
status: queued
focus: pre-existing drift
repo: {drifted}
drift-source: spec-sync-sweep
---

## Focus

pre-existing drift
""",
        )

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(drifted), record["drifted"])
        self.assertIn(str(drifted), record["skipped_existing"])
        self.assertNotIn(str(drifted), record["filed"])
        files = list(queue_dir.glob("*.md"))
        self.assertEqual(len(files), 1)  # no new brief filed

    def test_held_lock_returns_skipped_overlap_and_calls_no_discovery(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(self.lock_path, "a+")
        self.addCleanup(holder.close)
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with mock.patch("worktrail.router.spec_sync_sweep.discover_repos_with_specs") as spy:
            record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)
            spy.assert_not_called()

        self.assertEqual(
            record,
            {
                "skipped_overlap": True,
                "checked": [],
                "drifted": [],
                "filed": [],
                "skipped_existing": [],
                "failed": [],
                "checkbox_drifted": [],
                "checkbox_filed": [],
                "checkbox_skipped_existing": [],
                "checkbox_failed": [],
            },
        )

    def test_empty_repos_root_returns_complete_all_empty_record(self) -> None:
        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)
        self.assertEqual(
            record,
            {
                "skipped_overlap": False,
                "checked": [],
                "drifted": [],
                "filed": [],
                "skipped_existing": [],
                "failed": [],
                "checkbox_drifted": [],
                "checkbox_filed": [],
                "checkbox_skipped_existing": [],
                "checkbox_failed": [],
            },
        )

    def test_two_consecutive_calls_file_only_one_brief_total(self) -> None:
        _make_drifted_repo(self.repos_root, "drifted-repo")

        first = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)
        second = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertEqual(len(first["filed"]), 1)
        self.assertEqual(len(second["filed"]), 0)
        self.assertEqual(len(second["skipped_existing"]), 1)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)

    def test_checkbox_drifted_repo_with_no_existing_brief_is_drifted_and_filed(self) -> None:
        checkbox_drifted = _make_checkbox_drifted_repo(self.repos_root, "checkbox-repo")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(checkbox_drifted), record["checkbox_drifted"])
        self.assertIn(str(checkbox_drifted), record["checkbox_filed"])
        self.assertNotIn(str(checkbox_drifted), record["checkbox_skipped_existing"])
        self.assertNotIn(str(checkbox_drifted), record["checkbox_failed"])
        # Zero spec-sync-drift findings for this repo (AC-CHG-001).
        self.assertNotIn(str(checkbox_drifted), record["drifted"])
        self.assertNotIn(str(checkbox_drifted), record["filed"])

    def test_checkbox_check_error_recorded_in_checkbox_failed_others_still_run(self) -> None:
        _make_clean_repo(self.repos_root, "clean-repo")
        errored = _make_drifted_repo(self.repos_root, "errored-repo")
        other_checkbox = _make_checkbox_drifted_repo(self.repos_root, "checkbox-repo")

        def fake_checkbox_check(repo: Path):
            if repo.name == "errored-repo":
                return {"repo": str(repo), "findings": [], "error": "simulated checkbox failure"}
            from worktrail.router.spec_sync_sweep_checkbox_check import check_repo_checkbox_drift as real

            return real(repo)

        with mock.patch(
            "worktrail.router.spec_sync_sweep.check_repo_checkbox_drift", side_effect=fake_checkbox_check
        ):
            record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(errored), record["checkbox_failed"])
        self.assertNotIn(str(errored), record["checkbox_drifted"])
        self.assertNotIn(str(errored), record["checkbox_filed"])
        # That repo's own spec-sync-drift check still ran and completed normally.
        self.assertIn(str(errored), record["drifted"])
        self.assertIn(str(errored), record["filed"])
        # Every other repo's checkbox-drift check still ran in the same call.
        self.assertIn(str(other_checkbox), record["checkbox_filed"])
        self.assertEqual(len(record["checked"]), 3)

    def test_checkbox_drifted_repo_with_existing_checkbox_brief_is_skipped_independently(
        self,
    ) -> None:
        checkbox_drifted = _make_checkbox_drifted_repo(self.repos_root, "checkbox-repo")
        _seed_existing_brief(self.queue_base, checkbox_drifted, "checkbox-drift-sweep")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(checkbox_drifted), record["checkbox_drifted"])
        self.assertIn(str(checkbox_drifted), record["checkbox_skipped_existing"])
        self.assertNotIn(str(checkbox_drifted), record["checkbox_filed"])
        # No spec-sync-drift item for this repo either way.
        self.assertNotIn(str(checkbox_drifted), record["drifted"])
        self.assertNotIn(str(checkbox_drifted), record["skipped_existing"])
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)  # no new checkbox-drift brief filed

    def test_repo_with_outstanding_spec_sync_brief_still_gets_new_checkbox_brief(self) -> None:
        both = _make_drifted_and_checkbox_drifted_repo(self.repos_root, "both-repo")
        _seed_existing_brief(self.queue_base, both, "spec-sync-sweep")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        # Spec-sync-drift: already outstanding, not re-filed.
        self.assertIn(str(both), record["drifted"])
        self.assertIn(str(both), record["skipped_existing"])
        self.assertNotIn(str(both), record["filed"])
        # Checkbox-drift: fresh hits, no existing checkbox-drift brief -> filed.
        self.assertIn(str(both), record["checkbox_drifted"])
        self.assertIn(str(both), record["checkbox_filed"])
        self.assertNotIn(str(both), record["checkbox_skipped_existing"])

    def test_two_consecutive_calls_file_only_one_brief_per_check_type(self) -> None:
        _make_drifted_and_checkbox_drifted_repo(self.repos_root, "both-repo")

        first = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)
        second = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertEqual(len(first["filed"]), 1)
        self.assertEqual(len(first["checkbox_filed"]), 1)
        self.assertEqual(len(second["filed"]), 0)
        self.assertEqual(len(second["checkbox_filed"]), 0)
        self.assertEqual(len(second["skipped_existing"]), 1)
        self.assertEqual(len(second["checkbox_skipped_existing"]), 1)
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 2)  # one spec-sync-drift brief, one checkbox-drift brief

    def test_no_checkbox_drift_anywhere_returns_empty_checkbox_lists(self) -> None:
        _make_clean_repo(self.repos_root, "clean-repo")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertEqual(record["checkbox_drifted"], [])
        self.assertEqual(record["checkbox_filed"], [])
        self.assertEqual(record["checkbox_skipped_existing"], [])
        self.assertEqual(record["checkbox_failed"], [])

    def test_held_lock_yields_empty_checkbox_lists_too(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(self.lock_path, "a+")
        self.addCleanup(holder.close)
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertTrue(record["skipped_overlap"])
        self.assertEqual(record["checkbox_drifted"], [])
        self.assertEqual(record["checkbox_filed"], [])
        self.assertEqual(record["checkbox_skipped_existing"], [])
        self.assertEqual(record["checkbox_failed"], [])


def _real_check_repo_drift(repo: Path):
    from worktrail.router.spec_sync_sweep_check import check_repo_drift as real

    return real(repo)


class MainCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.repos_root = self.root / "repos"
        self.repos_root.mkdir()
        self.queue_dir = self.root / "queue-base"
        self.lock_path = self.root / "sweep.lock"

    def test_json_output_matches_record_shape_and_exit_zero(self) -> None:
        buf = StringIO()
        with redirect_stdout(buf):
            rc = sss.main(
                [
                    "--repos-root",
                    str(self.repos_root),
                    "--queue-dir",
                    str(self.queue_dir),
                    "--lock-file",
                    str(self.lock_path),
                    "--json",
                ]
            )
        self.assertEqual(rc, 0)
        payload = json.loads(buf.getvalue())
        self.assertEqual(
            set(payload.keys()),
            {
                "skipped_overlap",
                "checked",
                "drifted",
                "filed",
                "skipped_existing",
                "failed",
                "checkbox_drifted",
                "checkbox_filed",
                "checkbox_skipped_existing",
                "checkbox_failed",
            },
        )

    def test_unwritable_queue_dir_returns_nonzero_exit_distinct_from_per_repo_failure(self) -> None:
        _make_drifted_repo(self.repos_root, "drifted-repo")
        self.queue_dir.mkdir(parents=True)
        self.queue_dir.chmod(0o500)  # read + execute only, no write
        self.addCleanup(lambda: self.queue_dir.chmod(0o700))

        buf = StringIO()
        with redirect_stdout(buf):
            rc = sss.main(
                [
                    "--repos-root",
                    str(self.repos_root),
                    "--queue-dir",
                    str(self.queue_dir),
                    "--lock-file",
                    str(self.lock_path),
                    "--json",
                ]
            )
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
