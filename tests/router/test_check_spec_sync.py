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

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_spec_sync import check_spec, fix_spec


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


def make_task_with_files(
    spec_dir: Path,
    task_id: str,
    status: str,
    files: list[str],
    kind: str | None = None,
    exempt: list[str] | None = None,
) -> None:
    files_block = "\n".join(f"  - {f}" for f in files)
    kind_line = f"kind: {kind}\n" if kind is not None else ""
    exempt_block = ""
    if exempt is not None:
        exempt_lines = "\n".join(f"  - {f}" for f in exempt)
        exempt_block = f"files-sync-exempt:\n{exempt_lines}\n"
    write(
        spec_dir / "tasks" / f"{task_id}.md",
        f"""---
id: {task_id}
title: "Test task"
spec: docs/specs/000-fixture/fixture.md
status: {status}
dependencies: []
files:
{files_block}
{kind_line}{exempt_block}---

## Definition of Done
- [x] done
""",
    )


def init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=root, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)


def git_commit_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "test commit"], cwd=root, check=True)


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
        self.assertEqual(
            len(failures), 3
        )  # 2 task-row mismatches + 1 parent-status mismatch
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

    def test_e2e_verification_notes_never_shadows_parent_spec(self):
        # Regression fixture for gracefully-giving-back spec
        # 027-feedback-capture-package: the same find_parent_spec()
        # lexicographic-last hazard as user-request.md/brainstorming-notes.md
        # above, but for e2e-verification-notes.md, which also carries no
        # Status header by design and also sorts after a dated spec filename.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec("Implemented")
        write(self.spec_dir / "e2e-verification-notes.md", "Verified 2026-07-18.\n")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_unlisted_aux_doc_without_status_never_shadows_parent_spec(self):
        # Regression fixture for datalena spec 099-recursive-organization-model
        # (2026-08-22): `org-units-dependency-inventory.md` -- not in
        # AUX_FILENAMES, no Status header, sorts after the dated spec file --
        # shadowed the real parent spec and failed every datalena PR gate after
        # PR #2477 merged. find_parent_spec() now prefers candidates that carry
        # a Status header, so no allow-list entry is needed per new filename.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        self.parent_spec("Complete")
        write(self.spec_dir / "org-units-dependency-inventory.md", "# Inventory\n")
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_missing_status_header_still_flagged_when_no_candidate_has_one(self):
        # The Status-header preference must not hide a genuinely missing header:
        # with no candidate carrying one, Check B still fires on the
        # lexicographically last candidate as before.
        make_task(self.spec_dir, "TASK-001", "completed")
        self.task_summary({"TASK-001": "completed"})
        write(self.spec_dir / "2026-01-01--fixture.md", "# Spec without status\n")
        write(self.spec_dir / "zz-notes.md", "# Notes\n")
        failures = check_spec(self.spec_dir)
        self.assertEqual(len(failures), 1)
        self.assertIn("no Status header", failures[0])

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
        for status in (
            "Shipped",
            "Complete",
            "Completed",
            "Implemented (PRs #1450, #1451)",
        ):
            with self.subTest(status=status):
                make_task(self.spec_dir, "TASK-001", "completed")
                self.task_summary({"TASK-001": "completed"})
                self.parent_spec(status)
                self.assertEqual(check_spec(self.spec_dir), [])


class FixSpecTests(unittest.TestCase):
    """--fix / fix_spec(): Check B's STALE_PARENT_STATUSES case only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spec_dir = Path(self.tmp.name) / "000-fixture"
        self.spec_dir.mkdir(parents=True)

    def parent_spec_path(self) -> Path:
        return self.spec_dir / "2026-01-01--fixture.md"

    def parent_spec(self, status: str, colon_inside_bold: bool = False) -> None:
        status_line = (
            f"**Status:** {status}" if colon_inside_bold else f"**Status**: {status}"
        )
        write(
            self.parent_spec_path(),
            f"""# Functional Specification: Fixture

**Spec ID**: 000-fixture
**Date**: 2026-01-01
{status_line}
**Version**: 1.0
""",
        )

    def test_fix_flips_stale_status_to_implemented(self):
        make_task(self.spec_dir, "TASK-001", "completed")
        self.parent_spec("Draft")
        result = fix_spec(self.spec_dir)
        self.assertEqual(len(result), 1)
        self.assertIn("Draft", result[0])
        self.assertIn("Implemented", result[0])
        self.assertIn("**Status**: Implemented", self.parent_spec_path().read_text())
        # Re-running check_spec() confirms the drift is gone post-fix.
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_fix_preserves_colon_inside_bold_convention(self):
        make_task(self.spec_dir, "TASK-001", "completed")
        self.parent_spec("Ready for Implementation", colon_inside_bold=True)
        result = fix_spec(self.spec_dir)
        self.assertEqual(len(result), 1)
        text = self.parent_spec_path().read_text()
        self.assertIn("**Status:** Implemented", text)
        self.assertNotIn("**Status**: Implemented", text)

    def test_fix_does_not_touch_missing_status_header(self):
        make_task(self.spec_dir, "TASK-001", "completed")
        write(
            self.parent_spec_path(),
            "# Functional Specification: Fixture\n\n"
            "**Spec ID**: 000-fixture\n**Date**: 2026-01-01\n**Version**: 1.0\n",
        )
        before = self.parent_spec_path().read_text()
        self.assertEqual(fix_spec(self.spec_dir), [])
        self.assertEqual(self.parent_spec_path().read_text(), before)
        # The missing-header drift is still reported -- never auto-fixed.
        failures = check_spec(self.spec_dir)
        self.assertEqual(len(failures), 1)
        self.assertIn("no Status header", failures[0])

    def test_fix_no_op_when_status_not_stale(self):
        make_task(self.spec_dir, "TASK-001", "completed")
        self.parent_spec("Backfill")
        self.assertEqual(fix_spec(self.spec_dir), [])
        self.assertIn("**Status**: Backfill", self.parent_spec_path().read_text())

    def test_fix_no_op_when_not_all_terminal(self):
        make_task(self.spec_dir, "TASK-001", "pending")
        self.parent_spec("Draft")
        self.assertEqual(fix_spec(self.spec_dir), [])
        self.assertIn("**Status**: Draft", self.parent_spec_path().read_text())

    def test_fix_does_not_touch_check_a_summary_drift(self):
        # Check A (task-plan summary table) is a separate, unrelated file --
        # --fix must never rewrite it, only the parent spec's Status header.
        make_task(self.spec_dir, "TASK-001", "completed")
        summary_path = self.spec_dir / "2026-01-01--fixture--tasks.md"
        write(
            summary_path,
            "# Task Plan: Fixture\n\n"
            "| Task | Title | Dependencies | Status |\n"
            "|---|---|---|---|\n"
            "| TASK-001 | Test | None | pending |\n",
        )
        self.parent_spec("Draft")
        before_summary = summary_path.read_text()
        result = fix_spec(self.spec_dir)
        self.assertEqual(len(result), 1)  # only the Status header was fixed
        self.assertEqual(summary_path.read_text(), before_summary)
        # Check A drift is still reported after the fix.
        failures = check_spec(self.spec_dir)
        self.assertEqual(len(failures), 1)
        self.assertIn("TASK-001", failures[0])
        self.assertIn("pending", failures[0])


class CheckSpecSyncFilesTrackedTests(unittest.TestCase):
    """Check C: files: entries for completed impl tasks must be git-tracked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        init_git_repo(self.repo)
        self.spec_dir = self.repo / "docs" / "specs" / "000-fixture"
        self.spec_dir.mkdir(parents=True)
        write(
            self.spec_dir / "2026-01-01--fixture.md",
            "# Functional Specification: Fixture\n\n"
            "**Spec ID**: 000-fixture\n**Date**: 2026-01-01\n"
            "**Status**: Implemented\n**Version**: 1.0\n",
        )

    def test_skipped_entirely_without_repo(self):
        make_task_with_files(
            self.spec_dir, "TASK-001", "completed", files=["src/does/not/exist.py"]
        )
        self.assertEqual(check_spec(self.spec_dir), [])

    def test_passes_when_file_is_git_tracked(self):
        tracked = self.repo / "src" / "real.py"
        write(tracked, "# real\n")
        git_commit_all(self.repo)
        make_task_with_files(
            self.spec_dir, "TASK-001", "completed", files=["src/real.py"]
        )
        self.assertEqual(check_spec(self.spec_dir, repo=self.repo), [])

    def test_flags_untracked_file(self):
        git_commit_all(self.repo)  # empty commit so the repo isn't a bare unborn HEAD
        make_task_with_files(
            self.spec_dir, "TASK-001", "completed", files=["src/never/committed.py"]
        )
        failures = check_spec(self.spec_dir, repo=self.repo)
        self.assertEqual(len(failures), 1)
        self.assertIn("src/never/committed.py", failures[0])
        self.assertIn("not git-tracked", failures[0])

    def test_passes_when_directory_entry_fully_tracked(self):
        # A files: entry naming a directory (trailing slash) whose contents are
        # entirely git-tracked must not be flagged. git ls-files never echoes back
        # the literal directory-path string, only the individual file paths under
        # it, so a naive membership check against that string always fails even
        # when every file underneath is tracked.
        write(self.repo / "drizzle" / "0000_init.sql", "-- migration\n")
        write(self.repo / "drizzle" / "meta" / "_journal.json", "{}\n")
        git_commit_all(self.repo)
        make_task_with_files(self.spec_dir, "TASK-001", "completed", files=["drizzle/"])
        self.assertEqual(check_spec(self.spec_dir, repo=self.repo), [])

    def test_flags_directory_entry_with_untracked_contents(self):
        # A directory entry should still be flagged when it has no tracked files
        # under it at all (e.g. entirely gitignored or never committed).
        write(self.repo / ".gitignore", "untracked_dir/\n")
        git_commit_all(self.repo)
        (self.repo / "untracked_dir").mkdir()
        (self.repo / "untracked_dir" / "scratch.sql").write_text("-- scratch\n")
        make_task_with_files(
            self.spec_dir, "TASK-001", "completed", files=["untracked_dir/"]
        )
        failures = check_spec(self.spec_dir, repo=self.repo)
        self.assertEqual(len(failures), 1)
        self.assertIn("untracked_dir/", failures[0])
        self.assertIn("not git-tracked", failures[0])

    def test_skips_non_repo_relative_entries(self):
        git_commit_all(self.repo)
        make_task_with_files(
            self.spec_dir,
            "TASK-001",
            "completed",
            files=["~/bin/deploy.sh", "/etc/hosts", "crontab (user-level)"],
        )
        self.assertEqual(check_spec(self.spec_dir, repo=self.repo), [])

    def test_only_applies_to_completed_impl_tasks(self):
        git_commit_all(self.repo)
        make_task_with_files(
            self.spec_dir, "TASK-001", "pending", files=["src/never/committed.py"]
        )
        make_task_with_files(
            self.spec_dir,
            "TASK-002",
            "completed",
            files=["src/other/missing.py"],
            kind="tail",
        )
        self.assertEqual(check_spec(self.spec_dir, repo=self.repo), [])

    def test_defaults_kind_to_impl_when_omitted(self):
        git_commit_all(self.repo)
        make_task_with_files(
            self.spec_dir, "TASK-001", "completed", files=["src/never/committed.py"]
        )
        failures = check_spec(self.spec_dir, repo=self.repo)
        self.assertEqual(len(failures), 1)
        self.assertIn("src/never/committed.py", failures[0])

    def test_files_sync_exempt_silences_matching_entry(self):
        git_commit_all(self.repo)
        make_task_with_files(
            self.spec_dir,
            "TASK-001",
            "completed",
            files=["src/never/committed.py", "src/also/missing.py"],
            exempt=["src/never/committed.py"],
        )
        failures = check_spec(self.spec_dir, repo=self.repo)
        self.assertEqual(len(failures), 1)
        self.assertIn("src/also/missing.py", failures[0])

    def test_files_sync_exempt_entry_not_in_files_is_inert(self):
        # An exemption naming a path that isn't (or is no longer) in files:
        # is not an error -- it simply has nothing to exempt.
        git_commit_all(self.repo)
        make_task_with_files(
            self.spec_dir,
            "TASK-001",
            "completed",
            files=["src/never/committed.py"],
            exempt=["src/some/other/path.py"],
        )
        failures = check_spec(self.spec_dir, repo=self.repo)
        self.assertEqual(len(failures), 1)
        self.assertIn("src/never/committed.py", failures[0])


if __name__ == "__main__":
    unittest.main()
