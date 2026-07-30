#!/usr/bin/env python3
"""Unit tests for check_dod_verification.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_dod_verification import (
    check_changed_specs, check_task_file, run_check,
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
