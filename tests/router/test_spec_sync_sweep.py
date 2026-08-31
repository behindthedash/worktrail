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
    subprocess.run(
        ["git", "init", "-q"], cwd=repo, capture_output=True, text=True, check=True
    )


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


def _seed_resolved_brief(
    queue_base: Path, repo: Path, drift_source: str, brief_id: str = "resolved"
) -> None:
    """A `picked/` brief with `status: done` — resolved, so it must not
    suppress a fresh brief for the same repo/drift-source (REQ-017)."""
    picked_dir = queue_base / "picked"
    picked_dir.mkdir(parents=True, exist_ok=True)
    _write(
        picked_dir / f"{brief_id}.md",
        f"""---
id: {brief_id}
status: done
focus: resolved drift
repo: {repo}
drift-source: {drift_source}
---

## Focus

resolved drift
""",
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _make_stale_bookkeeping_task(
    spec_dir: Path, task_id: str, shipped_file: str
) -> None:
    """A `status: pending`, `kind: impl` task whose sole `files:` entry is
    (later) committed to git — everything a genuinely unimplemented task
    needs, except the code already shipped. Deliberately not written to a
    task-plan summary table or an audited checkbox section, so it can never
    also register as a spec-sync-drift or checkbox-drift finding."""
    _write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"""---
id: {task_id}
status: pending
kind: impl
files: [{shipped_file}]
dependencies: []
---
# {task_id}
""",
    )


def _make_stale_bookkeeping_repo(
    root: Path,
    name: str,
    spec_num: str = "003",
    task_id: str = "TASK-001",
    shipped_file: str = "src/shipped.py",
) -> Path:
    """A git repo whose only spec dir has a pending `kind: impl` task whose
    file is already merged on the base branch — a genuine stale-bookkeeping
    finding (`dashboard.detect_stage` returns `stage: "stale-bookkeeping"`),
    with zero spec-sync-drift or checkbox-drift findings anywhere in the
    repo. Mirrors `tests/router/test_dashboard.py::StaleBookkeeping`'s fixture
    recipe exactly (commit spec first, write task uncommitted, commit the
    shipped file after)."""
    repo = root / name
    _git_init(repo)
    spec_dir = repo / "docs" / "specs" / f"{spec_num}-stale"
    _make_parent_spec(spec_dir, "Draft")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "spec created")

    _make_stale_bookkeeping_task(spec_dir, task_id, shipped_file)

    shipped_path = repo / shipped_file
    shipped_path.parent.mkdir(parents=True, exist_ok=True)
    shipped_path.write_text("shipped\n", encoding="utf-8")
    _git(repo, "add", shipped_file)
    _git(repo, "commit", "-qm", "ship")
    return repo


def _make_all_three_drifted_repo(root: Path, name: str) -> Path:
    """A git repo drifted on spec-sync, checkbox, AND stale-bookkeeping
    checks simultaneously, in three independent spec dirs."""
    repo = _make_drifted_and_checkbox_drifted_repo(root, name)
    spec_dir = repo / "docs" / "specs" / "003-stale"
    _make_parent_spec(spec_dir, "Draft")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "stale spec created")
    _make_stale_bookkeeping_task(spec_dir, "TASK-001", "src/shipped.py")
    shipped_path = repo / "src" / "shipped.py"
    shipped_path.parent.mkdir(parents=True, exist_ok=True)
    shipped_path.write_text("shipped\n", encoding="utf-8")
    _git(repo, "add", "src/shipped.py")
    _git(repo, "commit", "-qm", "ship")
    return repo


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

    def test_repo_with_check_error_is_recorded_as_failed_and_others_still_processed(
        self,
    ) -> None:
        _make_clean_repo(self.repos_root, "clean-repo")
        errored = _make_drifted_repo(self.repos_root, "errored-repo")

        def fake_check(repo: Path):
            if repo.name == "errored-repo":
                return {"repo": str(repo), "findings": [], "error": "simulated failure"}
            return _real_check_repo_drift(repo)

        # Patch at the module level used inside run_sweep.
        with mock.patch(
            "worktrail.router.spec_sync_sweep.check_repo_drift", side_effect=fake_check
        ):
            record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(errored), record["checked"])
        self.assertIn(str(errored), record["failed"])
        self.assertNotIn(str(errored), record["drifted"])
        self.assertNotIn(str(errored), record["filed"])
        # The other repo in the same run is still fully processed.
        self.assertEqual(len(record["checked"]), 2)

    def test_drifted_repo_with_existing_unresolved_brief_is_skipped_not_filed(
        self,
    ) -> None:
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
        holder = open(self.lock_path, "a+")  # noqa: SIM115 -- held across the surrounding scope as a lock file
        self.addCleanup(holder.close)
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        with mock.patch(
            "worktrail.router.spec_sync_sweep.discover_repos_with_specs"
        ) as spy:
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
                "stale_bookkeeping_drifted": [],
                "stale_bookkeeping_filed": [],
                "stale_bookkeeping_skipped_existing": [],
                "stale_bookkeeping_failed": [],
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
                "stale_bookkeeping_drifted": [],
                "stale_bookkeeping_filed": [],
                "stale_bookkeeping_skipped_existing": [],
                "stale_bookkeeping_failed": [],
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

    def test_checkbox_drifted_repo_with_no_existing_brief_is_drifted_and_filed(
        self,
    ) -> None:
        checkbox_drifted = _make_checkbox_drifted_repo(self.repos_root, "checkbox-repo")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(checkbox_drifted), record["checkbox_drifted"])
        self.assertIn(str(checkbox_drifted), record["checkbox_filed"])
        self.assertNotIn(str(checkbox_drifted), record["checkbox_skipped_existing"])
        self.assertNotIn(str(checkbox_drifted), record["checkbox_failed"])
        # Zero spec-sync-drift findings for this repo (AC-CHG-001).
        self.assertNotIn(str(checkbox_drifted), record["drifted"])
        self.assertNotIn(str(checkbox_drifted), record["filed"])

    def test_checkbox_check_error_recorded_in_checkbox_failed_others_still_run(
        self,
    ) -> None:
        _make_clean_repo(self.repos_root, "clean-repo")
        errored = _make_drifted_repo(self.repos_root, "errored-repo")
        other_checkbox = _make_checkbox_drifted_repo(self.repos_root, "checkbox-repo")

        def fake_checkbox_check(repo: Path):
            if repo.name == "errored-repo":
                return {
                    "repo": str(repo),
                    "findings": [],
                    "error": "simulated checkbox failure",
                }
            from worktrail.router.spec_sync_sweep_checkbox_check import (
                check_repo_checkbox_drift as real,
            )

            return real(repo)

        with mock.patch(
            "worktrail.router.spec_sync_sweep.check_repo_checkbox_drift",
            side_effect=fake_checkbox_check,
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

    def test_repo_with_outstanding_spec_sync_brief_still_gets_new_checkbox_brief(
        self,
    ) -> None:
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
        self.assertEqual(
            len(files), 2
        )  # one spec-sync-drift brief, one checkbox-drift brief

    def test_no_checkbox_drift_anywhere_returns_empty_checkbox_lists(self) -> None:
        _make_clean_repo(self.repos_root, "clean-repo")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertEqual(record["checkbox_drifted"], [])
        self.assertEqual(record["checkbox_filed"], [])
        self.assertEqual(record["checkbox_skipped_existing"], [])
        self.assertEqual(record["checkbox_failed"], [])

    def test_held_lock_yields_empty_checkbox_lists_too(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        holder = open(self.lock_path, "a+")  # noqa: SIM115 -- held across the surrounding scope as a lock file
        self.addCleanup(holder.close)
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertTrue(record["skipped_overlap"])
        self.assertEqual(record["checkbox_drifted"], [])
        self.assertEqual(record["checkbox_filed"], [])
        self.assertEqual(record["checkbox_skipped_existing"], [])
        self.assertEqual(record["checkbox_failed"], [])

    def test_stale_bookkeeping_only_repo_gets_exactly_one_stale_brief_and_no_others(
        self,
    ) -> None:
        stale = _make_stale_bookkeeping_repo(self.repos_root, "stale-repo")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(stale), record["stale_bookkeeping_drifted"])
        self.assertIn(str(stale), record["stale_bookkeeping_filed"])
        self.assertNotIn(str(stale), record["stale_bookkeeping_skipped_existing"])
        self.assertNotIn(str(stale), record["stale_bookkeeping_failed"])
        # Zero spec-sync-drift and checkbox-drift findings for this repo.
        self.assertNotIn(str(stale), record["drifted"])
        self.assertNotIn(str(stale), record["filed"])
        self.assertNotIn(str(stale), record["checkbox_drifted"])
        self.assertNotIn(str(stale), record["checkbox_filed"])
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)
        brief = files[0].read_text(encoding="utf-8")
        self.assertIn("drift-source: stale-bookkeeping-sweep", brief)

    def test_repo_drifted_on_all_three_checks_gets_all_three_briefs(self) -> None:
        all_three = _make_all_three_drifted_repo(self.repos_root, "all-three-repo")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(all_three), record["drifted"])
        self.assertIn(str(all_three), record["filed"])
        self.assertIn(str(all_three), record["checkbox_drifted"])
        self.assertIn(str(all_three), record["checkbox_filed"])
        self.assertIn(str(all_three), record["stale_bookkeeping_drifted"])
        self.assertIn(str(all_three), record["stale_bookkeeping_filed"])
        # Each check's dedup lookup governs its own brief independently.
        self.assertNotIn(str(all_three), record["skipped_existing"])
        self.assertNotIn(str(all_three), record["checkbox_skipped_existing"])
        self.assertNotIn(str(all_three), record["stale_bookkeeping_skipped_existing"])
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 3)
        sources = {
            line
            for f in files
            for line in f.read_text(encoding="utf-8").splitlines()
            if line.startswith("drift-source:")
        }
        self.assertEqual(
            sources,
            {
                "drift-source: spec-sync-sweep",
                "drift-source: checkbox-drift-sweep",
                "drift-source: stale-bookkeeping-sweep",
            },
        )

    def test_stale_bookkeeping_check_error_does_not_block_repos_other_checks(
        self,
    ) -> None:
        errored = _make_all_three_drifted_repo(self.repos_root, "errored-repo")
        other_stale = _make_stale_bookkeeping_repo(self.repos_root, "other-stale-repo")

        def fake_stale_check(repo: Path):
            if repo.name == "errored-repo":
                return {"repo": str(repo), "findings": [], "error": "simulated failure"}
            from worktrail.router.spec_sync_sweep_stale_bookkeeping_check import (
                check_repo_stale_bookkeeping as real,
            )

            return real(repo)

        with mock.patch(
            "worktrail.router.spec_sync_sweep.check_repo_stale_bookkeeping",
            side_effect=fake_stale_check,
        ):
            record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(errored), record["stale_bookkeeping_failed"])
        self.assertNotIn(str(errored), record["stale_bookkeeping_drifted"])
        self.assertNotIn(str(errored), record["stale_bookkeeping_filed"])
        # That repo's own other two checks still ran and completed normally.
        self.assertIn(str(errored), record["drifted"])
        self.assertIn(str(errored), record["filed"])
        self.assertIn(str(errored), record["checkbox_drifted"])
        self.assertIn(str(errored), record["checkbox_filed"])
        # Every other repo's stale-bookkeeping check still ran in the same call.
        self.assertIn(str(other_stale), record["stale_bookkeeping_filed"])
        self.assertEqual(len(record["checked"]), 2)

    def test_stale_bookkeeping_existing_unresolved_suppresses_but_resolved_does_not(
        self,
    ) -> None:
        stale = _make_stale_bookkeeping_repo(self.repos_root, "stale-repo")
        _seed_existing_brief(self.queue_base, stale, "stale-bookkeeping-sweep")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(stale), record["stale_bookkeeping_drifted"])
        self.assertIn(str(stale), record["stale_bookkeeping_skipped_existing"])
        self.assertNotIn(str(stale), record["stale_bookkeeping_filed"])
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(len(files), 1)  # no new stale-bookkeeping brief filed

    def test_stale_bookkeeping_resolved_done_brief_does_not_suppress_new_one(
        self,
    ) -> None:
        stale = _make_stale_bookkeeping_repo(self.repos_root, "stale-repo")
        _seed_resolved_brief(self.queue_base, stale, "stale-bookkeeping-sweep")

        record = sss.run_sweep(self.repos_root, self.queue_base, self.lock_path)

        self.assertIn(str(stale), record["stale_bookkeeping_drifted"])
        self.assertIn(str(stale), record["stale_bookkeeping_filed"])
        self.assertNotIn(str(stale), record["stale_bookkeeping_skipped_existing"])
        files = list((self.queue_base / "queue").glob("*.md"))
        self.assertEqual(
            len(files), 1
        )  # freshly filed, resolved brief did not block it


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
                "stale_bookkeeping_drifted",
                "stale_bookkeeping_filed",
                "stale_bookkeeping_skipped_existing",
                "stale_bookkeeping_failed",
            },
        )

    def test_unwritable_queue_dir_returns_nonzero_exit_distinct_from_per_repo_failure(
        self,
    ) -> None:
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
