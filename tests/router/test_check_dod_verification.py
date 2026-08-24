#!/usr/bin/env python3
"""Unit tests for check_dod_verification.py."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router.check_dod_verification import (
    audit_all_specs_with_hints, check_changed_specs, check_changed_specs_with_hints,
    check_task_file, check_task_file_with_hints, classify_failure, derive_dod_checks,
    format_remediation_hint, run_check,
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

    def test_ac_checkboxes_complete_also_covers_dod_section(self) -> None:
        # The check spans taskformats.devkit.schema.COMPLETION_AUDIT_SECTIONS
        # ("Acceptance Criteria", "Definition of Done (DoD)"), not just AC --
        # an unchecked DoD box fails it even when AC is fully checked.
        self._write_task(
            "TASK-001.md",
            "## Acceptance Criteria\n\n- [x] one\n\n"
            "## Definition of Done (DoD)\n\n- [ ] not done\n",
        )
        failure = run_check(
            self.repo, {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"}
        )
        self.assertIsNotNone(failure)
        self.assertIn("TASK-001.md", failure)

    def test_ac_checkboxes_complete_passes_when_both_sections_fully_checked(self) -> None:
        self._write_task(
            "TASK-001.md",
            "## Acceptance Criteria\n\n- [x] one\n\n"
            "## Definition of Done (DoD)\n\n- [x] two\n",
        )
        self.assertIsNone(
            run_check(self.repo, {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"})
        )


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

    def test_files_sync_exempt_path_skips_file_tracked_and_no_stub_markers(self) -> None:
        checks = derive_dod_checks(
            {"files": ["src/foo.py", "src/bar.py"], "files-sync-exempt": ["src/foo.py"]},
            "some body",
            "TASK-001.md",
        )
        self.assertEqual(
            checks,
            [
                {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"},
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

    def test_no_body_no_ac_section_and_no_files_reports_no_failure(self) -> None:
        # derive_dod_checks always includes ac_checkboxes_complete (per 2.1);
        # it is never literally []. spec.md's requirement is "SHALL report no
        # failure" for this case, not "SHALL derive no checks" -- so assert
        # the derived list is exactly [ac_checkboxes_complete] AND that
        # running it against a task file with no body reports zero failures.
        checks = derive_dod_checks({}, "", "TASK-001.md")
        self.assertEqual(
            checks, [{"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            task_path = repo / "TASK-001.md"
            task_path.write_text(
                "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
                "status: completed\n---\n\n",
                encoding="utf-8",
            )
            failures = [f for c in checks if (f := run_check(repo, c)) is not None]
        self.assertEqual(failures, [])


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

    def test_explicit_dod_checks_skip_derivation_even_when_files_would_fail_derived_checks(
        self,
    ) -> None:
        # files: points at a file containing a stub marker (would fail a
        # derived no_stub_markers check), and the body has an unchecked AC
        # box (would fail a derived ac_checkboxes_complete check) -- but the
        # task declares explicit dod-checks that all pass, so derivation
        # must never run and neither failure should surface.
        (self.repo / "src").mkdir()
        (self.repo / "src" / "foo.py").write_text("# TODO: fix later\n", encoding="utf-8")
        task = self.repo / "docs" / "specs" / "000-fixture" / "tasks" / "TASK-001.md"
        task.parent.mkdir(parents=True, exist_ok=True)
        task.write_text(
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
            "status: completed\nfiles:\n  - src/foo.py\n"
            "dod-checks:\n  - type: file_exists\n    path: src/foo.py\n"
            "---\n\n## Acceptance Criteria\n\n- [ ] not actually checked\n",
            encoding="utf-8",
        )
        self.assertEqual(check_task_file(self.repo, task), [])


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


class ClassifyFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def _write(self, relpath: str, content: str = "x\n") -> None:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _track(self, relpath: str) -> None:
        subprocess.run(["git", "add", relpath], cwd=self.repo, check=True)

    def test_file_tracked_missing_with_no_candidate_is_stale_metadata(self) -> None:
        check = {"type": "file_tracked", "path": "docs/missing.md"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "stale-files-metadata")
        self.assertIn("verify by hand", hint["suggestion"])

    def test_file_tracked_missing_with_one_candidate_suggests_it(self) -> None:
        self._write("scripts/ci/lint.sh")
        self._track("scripts/ci/lint.sh")
        check = {"type": "file_tracked", "path": "ci/scripts/lint.sh"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "stale-files-metadata")
        self.assertIn("scripts/ci/lint.sh", hint["suggestion"])

    def test_file_tracked_missing_with_multiple_candidates_lists_them(self) -> None:
        self._write("a/lint.sh")
        self._track("a/lint.sh")
        self._write("b/lint.sh")
        self._track("b/lint.sh")
        check = {"type": "file_tracked", "path": "ci/lint.sh"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "stale-files-metadata")
        self.assertIn("a/lint.sh", hint["suggestion"])
        self.assertIn("b/lint.sh", hint["suggestion"])

    def test_file_tracked_exists_but_untracked_suggests_git_add(self) -> None:
        self._write("untracked.txt")
        check = {"type": "file_tracked", "path": "untracked.txt"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "stale-files-metadata")
        self.assertIn("git add untracked.txt", hint["suggestion"])

    def test_file_exists_missing_is_stale_metadata(self) -> None:
        check = {"type": "file_exists", "path": "missing.txt"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "stale-files-metadata")

    def test_no_stub_markers_missing_path_is_stale_metadata(self) -> None:
        check = {"type": "no_stub_markers", "path": "missing.txt"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "stale-files-metadata")

    def test_no_stub_markers_stub_marker_present_is_unclassified(self) -> None:
        self._write("stub.txt", "TODO: fix this\n")
        check = {"type": "no_stub_markers", "path": "stub.txt"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "unclassified")
        self.assertIsNone(hint["suggestion"])

    def test_ac_checkboxes_complete_is_genuine_unmet_ac(self) -> None:
        check = {"type": "ac_checkboxes_complete", "task_path": "TASK-001.md"}
        failure = "ac_checkboxes_complete check failed: TASK-001.md has unchecked boxes"
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "genuine-unmet-ac")
        self.assertIn("status: completed -> implemented", hint["suggestion"])
        self.assertIn("TASK-001.md", hint["suggestion"])

    def test_grep_failure_is_unclassified(self) -> None:
        self._write("foo.txt", "hello\n")
        check = {"type": "grep", "path": "foo.txt", "pattern": "goodbye"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "unclassified")
        self.assertIsNone(hint["suggestion"])

    def test_command_failure_is_unclassified(self) -> None:
        check = {"type": "command", "cmd": "exit 1"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "unclassified")

    def test_unknown_type_is_unclassified(self) -> None:
        check = {"type": "bogus"}
        failure = run_check(self.repo, check)
        hint = classify_failure(self.repo, check, failure)
        self.assertEqual(hint["classification"], "unclassified")


class FormatRemediationHintTests(unittest.TestCase):
    def test_includes_label_and_suggestion(self) -> None:
        line = format_remediation_hint(
            {"classification": "stale-files-metadata", "suggestion": "do X"}
        )
        self.assertIn("stale files: metadata", line)
        self.assertIn("do X", line)

    def test_no_suggestion_is_label_only(self) -> None:
        line = format_remediation_hint({"classification": "unclassified", "suggestion": None})
        self.assertEqual(line, "unclassified")


class CheckTaskFileWithHintsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def _write_task(self, relpath: str, frontmatter_extra: str, body: str = "body\n") -> Path:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
            f"{frontmatter_extra}"
            f"---\n\n{body}",
            encoding="utf-8",
        )
        return path

    def test_hints_mirror_check_task_file_failure_count(self) -> None:
        task = self._write_task(
            "docs/specs/000-fixture/tasks/TASK-001.md",
            "status: completed\n"
            "dod-checks:\n"
            "  - type: file_exists\n    path: missing.txt\n"
            "  - type: ac_checkboxes_complete\n    task_path: docs/specs/000-fixture/tasks/TASK-001.md\n",
            body="## Acceptance Criteria\n\n- [ ] not done\n",
        )
        plain = check_task_file(self.repo, task)
        hints = check_task_file_with_hints(self.repo, task)
        self.assertEqual(len(plain), len(hints))
        self.assertEqual({h["classification"] for h in hints},
                          {"stale-files-metadata", "genuine-unmet-ac"})

    def test_no_failures_yields_empty_hints(self) -> None:
        task = self._write_task(
            "docs/specs/000-fixture/tasks/TASK-001.md", "status: pending\n"
        )
        self.assertEqual(check_task_file_with_hints(self.repo, task), [])


class CheckChangedSpecsWithHintsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def _write(self, relpath: str, content: str) -> None:
        path = self.repo / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_scopes_to_devkit_task_files_and_tags_task_path(self) -> None:
        self._write(
            "docs/specs/000-fixture/tasks/TASK-001.md",
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
            "status: completed\n"
            "dod-checks:\n  - type: file_exists\n    path: missing.txt\n"
            "---\n\nbody\n",
        )
        hints = check_changed_specs_with_hints(
            self.repo, ["docs/specs/000-fixture/tasks/TASK-001.md"]
        )
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]["task"], "docs/specs/000-fixture/tasks/TASK-001.md")
        self.assertEqual(hints[0]["classification"], "stale-files-metadata")

    def test_skips_paths_outside_docs_specs(self) -> None:
        self._write(
            "src/TASK-001.md",
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
            "status: completed\n"
            "dod-checks:\n  - type: file_exists\n    path: missing.txt\n"
            "---\n\nbody\n",
        )
        self.assertEqual(check_changed_specs_with_hints(self.repo, ["src/TASK-001.md"]), [])


class AuditAllSpecsWithHintsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)

    def test_audits_every_task_file_with_hints(self) -> None:
        path = self.repo / "docs" / "specs" / "000-fixture" / "tasks" / "TASK-001.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nid: TASK-001\ntitle: Fixture\nspec: 000-fixture\n"
            "status: completed\n"
            "dod-checks:\n  - type: ac_checkboxes_complete\n    task_path: docs/specs/000-fixture/tasks/TASK-001.md\n"
            "---\n\n## Acceptance Criteria\n\n- [ ] not done\n",
            encoding="utf-8",
        )
        hints = audit_all_specs_with_hints(self.repo)
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0]["classification"], "genuine-unmet-ac")


if __name__ == "__main__":
    unittest.main(verbosity=2)
