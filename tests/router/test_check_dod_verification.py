#!/usr/bin/env python3
"""Unit tests for check_dod_verification.py."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_dod_verification import (
    check_changed_specs, check_task_file, derive_dod_checks, run_check,
)


class RunCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _write(self, relpath: str, content: str = "x\n") -> None:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_file_exists_passes(self) -> None:
        self._write("foo.txt")
        self.assertIsNone(run_check(self.repo, {"type": "file_exists", "path": "foo.txt"}))

    def test_file_exists_fails_when_missing(self) -> None:
        failure = run_check(self.repo, {"type": "file_exists", "path": "missing.txt"})
        self.assertIsNotNone(failure)
        self.assertIn("missing.txt", failure)

    def test_grep_passes(self) -> None:
        self._write("foo.txt", "hello world\n")
        self.assertIsNone(
            run_check(self.repo, {"type": "grep", "path": "foo.txt", "pattern": "hello"})
        )

    def test_grep_fails_no_match(self) -> None:
        self._write("foo.txt", "hello world\n")
        failure = run_check(self.repo, {"type": "grep", "path": "foo.txt", "pattern": "goodbye"})
        self.assertIsNotNone(failure)

    def test_grep_fails_when_file_missing(self) -> None:
        failure = run_check(self.repo, {"type": "grep", "path": "missing.txt", "pattern": "x"})
        self.assertIsNotNone(failure)

    def test_command_passes(self) -> None:
        self.assertIsNone(run_check(self.repo, {"type": "command", "cmd": "true"}))

    def test_command_fails(self) -> None:
        failure = run_check(self.repo, {"type": "command", "cmd": "exit 3"})
        self.assertIsNotNone(failure)
        self.assertIn("3", failure)

    def test_unknown_type_fails(self) -> None:
        failure = run_check(self.repo, {"type": "bogus"})
        self.assertIsNotNone(failure)
        self.assertIn("bogus", failure)

    def test_missing_required_key_fails(self) -> None:
        self.assertIsNotNone(run_check(self.repo, {"type": "file_exists"}))
        self.assertIsNotNone(run_check(self.repo, {"type": "grep", "path": "foo.txt"}))
        self.assertIsNotNone(run_check(self.repo, {"type": "command"}))
        self.assertIsNotNone(run_check(self.repo, {"type": "file_tracked"}))
        self.assertIsNotNone(run_check(self.repo, {"type": "ac_checkboxes_complete"}))
        self.assertIsNotNone(run_check(self.repo, {"type": "no_stub_markers"}))

    def test_no_stub_markers_passes_for_clean_file(self) -> None:
        self._write("clean.txt", "nothing to see here\n")
        self.assertIsNone(run_check(self.repo, {"type": "no_stub_markers", "path": "clean.txt"}))

    def test_no_stub_markers_fails_when_missing(self) -> None:
        failure = run_check(self.repo, {"type": "no_stub_markers", "path": "missing.txt"})
        self.assertIsNotNone(failure)
        self.assertIn("missing.txt", failure)

    def test_no_stub_markers_fails_for_todo(self) -> None:
        self._write("stub.txt", "before\nTODO: fix this\nafter\n")
        failure = run_check(self.repo, {"type": "no_stub_markers", "path": "stub.txt"})
        self.assertIsNotNone(failure)
        self.assertIn("TODO", failure)

    def test_no_stub_markers_fails_for_fixme(self) -> None:
        self._write("stub.txt", "FIXME later\n")
        failure = run_check(self.repo, {"type": "no_stub_markers", "path": "stub.txt"})
        self.assertIsNotNone(failure)
        self.assertIn("FIXME", failure)

    def test_no_stub_markers_fails_for_xxx(self) -> None:
        self._write("stub.txt", "XXX unsure about this\n")
        failure = run_check(self.repo, {"type": "no_stub_markers", "path": "stub.txt"})
        self.assertIsNotNone(failure)
        self.assertIn("XXX", failure)

    def test_no_stub_markers_fails_for_not_implemented_error(self) -> None:
        self._write("stub.py", "def f():\n    raise NotImplementedError\n")
        failure = run_check(self.repo, {"type": "no_stub_markers", "path": "stub.py"})
        self.assertIsNotNone(failure)
        self.assertIn("NotImplementedError", failure)

    def _write_task(self, relpath: str, body: str) -> None:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\nstatus: completed\n"
            f"---\n\n{body}",
            encoding="utf-8",
        )

    def test_ac_checkboxes_complete_passes_when_all_checked(self) -> None:
        self._write_task(
            "TASK-001.md",
            "## Acceptance Criteria\n\n- [x] one\n- [x] two\n",
        )
        self.assertIsNone(
            run_check(self.repo, {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"})
        )

    def test_ac_checkboxes_complete_fails_when_some_unchecked(self) -> None:
        self._write_task(
            "TASK-001.md",
            "## Acceptance Criteria\n\n- [x] one\n- [ ] two\n",
        )
        failure = run_check(
            self.repo, {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"}
        )
        self.assertIsNotNone(failure)
        self.assertIn("TASK-001.md", failure)

    def test_ac_checkboxes_complete_no_ac_section_falls_back_to_whole_body(self) -> None:
        # No "## Acceptance Criteria" heading: falls back to scanning the
        # whole body, which has an unchecked box -> fails.
        self._write_task("TASK-001.md", "- [x] one\n- [ ] two\n")
        failure = run_check(
            self.repo, {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"}
        )
        self.assertIsNotNone(failure)

    def test_ac_checkboxes_complete_no_checkboxes_anywhere_passes(self) -> None:
        # No AC section and no checkboxes at all in the body: vacuously
        # passes per _all_checkboxes_checked semantics.
        self._write_task("TASK-001.md", "just some prose, no checklist\n")
        self.assertIsNone(
            run_check(self.repo, {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"})
        )

    def test_ac_checkboxes_complete_fails_when_task_path_missing_on_disk(self) -> None:
        failure = run_check(
            self.repo, {"type": "ac_checkboxes_complete", "task_path": "missing.md"}
        )
        self.assertIsNotNone(failure)
        self.assertIn("missing.md", failure)


class FileTrackedTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def test_fails_when_path_missing(self) -> None:
        failure = run_check(self.repo, {"type": "file_tracked", "path": "missing.txt"})
        self.assertIsNotNone(failure)
        self.assertIn("missing.txt", failure)

    def test_fails_when_path_exists_but_untracked(self) -> None:
        (self.repo / "untracked.txt").write_text("hi\n", encoding="utf-8")
        failure = run_check(self.repo, {"type": "file_tracked", "path": "untracked.txt"})
        self.assertIsNotNone(failure)
        self.assertIn("untracked.txt", failure)

    def test_passes_when_path_is_tracked(self) -> None:
        (self.repo / "tracked.txt").write_text("hi\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.txt"], cwd=self.repo, check=True)
        self.assertIsNone(run_check(self.repo, {"type": "file_tracked", "path": "tracked.txt"}))


class DeriveDodChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _write_task(self, relpath: str, body: str) -> None:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\nstatus: completed\n"
            f"---\n\n{body}",
            encoding="utf-8",
        )

    def test_files_present_derives_file_tracked_and_no_stub_markers_per_path(self) -> None:
        checks = derive_dod_checks(
            {"files": ["src/foo.py", "src/bar.py"]}, "some body", "TASK-001.md"
        )
        self.assertEqual(
            checks,
            [
                {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"},
                {"type": "file_tracked", "path": "src/foo.py"},
                {"type": "no_stub_markers", "path": "src/foo.py"},
                {"type": "file_tracked", "path": "src/bar.py"},
                {"type": "no_stub_markers", "path": "src/bar.py"},
            ],
        )

    def test_files_absent_derives_ac_checkboxes_complete_only(self) -> None:
        checks = derive_dod_checks({}, "some body", "TASK-001.md")
        self.assertEqual(
            checks, [{"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"}]
        )

    def test_files_empty_list_derives_ac_checkboxes_complete_only(self) -> None:
        checks = derive_dod_checks({"files": []}, "some body", "TASK-001.md")
        self.assertEqual(
            checks, [{"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"}]
        )

    @unittest.expectedFailure
    def test_no_body_no_ac_section_and_no_files_derives_empty_list(self) -> None:
        # Task 4.2's third AC bullet: no body/no AC section and no files ->
        # empty derivation (matches today's no-op). The landed
        # derive_dod_checks (44b4eba) always emits ac_checkboxes_complete
        # regardless of body, so this currently fails; see 4.2-review.md
        # Major 1 for the discrepancy with tasks 2.1/2.2, which is a
        # planner decision outside this test-only task's scope.
        checks = derive_dod_checks({}, "", "TASK-001.md")
        self.assertEqual(checks, [])


class CheckTaskFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _write_task(self, relpath: str, frontmatter_extra: str) -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
            f"{frontmatter_extra}"
            "---\n\nbody\n",
            encoding="utf-8",
        )
        return path

    def test_noop_for_non_completed_status(self) -> None:
        task = self._write_task(
            "docs/specs/000-fixture/tasks/TASK-001.md",
            "status: pending\ndod-checks:\n  - type: file_exists\n    path: missing.txt\n",
        )
        self.assertEqual(check_task_file(self.repo, task), [])

    def test_noop_for_completed_with_no_dod_checks(self) -> None:
        task = self._write_task(
            "docs/specs/000-fixture/tasks/TASK-001.md", "status: completed\n"
        )
        self.assertEqual(check_task_file(self.repo, task), [])

    def test_aggregates_multiple_failures_mixed_pass_fail(self) -> None:
        (self.repo / "exists.txt").write_text("hello\n", encoding="utf-8")
        task = self._write_task(
            "docs/specs/000-fixture/tasks/TASK-001.md",
            "status: completed\n"
            "dod-checks:\n"
            "  - type: file_exists\n    path: exists.txt\n"
            "  - type: file_exists\n    path: missing.txt\n"
            "  - type: grep\n    path: exists.txt\n    pattern: nomatch\n",
        )
        failures = check_task_file(self.repo, task)
        self.assertEqual(len(failures), 2)


class CheckChangedSpecsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _write(self, relpath: str, content: str) -> None:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _completed_failing_task(self) -> str:
        return (
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
            "status: completed\n"
            "dod-checks:\n  - type: file_exists\n    path: missing.txt\n"
            "---\n\nbody\n"
        )

    def test_scopes_to_devkit_task_files_under_docs_specs(self) -> None:
        self._write(
            "docs/specs/000-fixture/tasks/TASK-001.md", self._completed_failing_task()
        )
        failures = check_changed_specs(
            self.repo, ["docs/specs/000-fixture/tasks/TASK-001.md"]
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("docs/specs/000-fixture/tasks/TASK-001.md", failures[0])

    def test_skips_paths_outside_docs_specs(self) -> None:
        self._write("src/TASK-001.md", self._completed_failing_task())
        self.assertEqual(check_changed_specs(self.repo, ["src/TASK-001.md"]), [])

    def test_skips_non_task_files_under_docs_specs(self) -> None:
        self._write("docs/specs/000-fixture/spec.md", "not a task file\n")
        self.assertEqual(
            check_changed_specs(self.repo, ["docs/specs/000-fixture/spec.md"]), []
        )

    def test_skips_path_that_does_not_exist_on_disk(self) -> None:
        self.assertEqual(
            check_changed_specs(
                self.repo, ["docs/specs/000-fixture/tasks/TASK-002.md"]
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
