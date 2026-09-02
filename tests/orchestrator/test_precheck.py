#!/usr/bin/env python3
"""Tests for precheck function from live.py.

Covers all acceptance criteria for the precheck subcommand:
- AC-1: all files present → WARN line, exit code 1
- AC-2: no files present → silent, exit code 0
- AC-3: partial files present → INFO line, exit code 0
- AC-8: e2e/cleanup/docs kinds skipped regardless of file presence
- AC-9: all tasks completed → silent, exit code 0
- FR-4: tasks with files: [] are silently skipped

Run: python3 scripts/test_precheck.py
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live


def _make_task(
    task_id, status="pending", kind="impl", files=None, body=None, extra_fm=None
):
    """Build a minimal task dict. `body` overrides the default bodiless stub
    with real markdown (e.g. "Files to Create"/"Files to Modify" sections).
    `extra_fm` is a dict of additional raw frontmatter key: value lines."""
    task = {
        "id": task_id,
        "status": status,
        "kind": kind,
    }
    if files is not None:
        task["files"] = files
    if body is not None:
        task["body"] = body
    if extra_fm is not None:
        task["extra_fm"] = extra_fm
    return task


def _make_spec_dir(tmp_root, tasks, create_files=None):
    """Create a temporary spec folder with TASK-*.md stubs and optionally create referenced files.

    Args:
        tmp_root: Path to temp directory where spec will be created
        tasks: list of task dicts from _make_task
        create_files: set of file paths to actually create under tmp_root

    Returns:
        Path to the spec directory
    """
    if create_files is None:
        create_files = set()

    spec_dir = tmp_root / "specs" / "001-test"
    tasks_dir = spec_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    # Write task TASK-*.md stubs with frontmatter
    for task in tasks:
        task_id = task["id"]
        frontmatter_lines = [f"id: {task_id}"]
        if "status" in task:
            frontmatter_lines.append(f"status: {task['status']}")
        if "kind" in task:
            frontmatter_lines.append(f"kind: {task['kind']}")
        if "files" in task:
            files = task["files"]
            if files:
                frontmatter_lines.append(f"files: {files}")
        for extra_key, extra_val in (task.get("extra_fm") or {}).items():
            frontmatter_lines.append(f"{extra_key}: {extra_val}")

        body = task.get("body") or f"# {task_id}\n"
        task_content = f"---\n{chr(10).join(frontmatter_lines)}\n---\n{body}"
        (tasks_dir / f"{task_id}.md").write_text(task_content)

    # Create the actual files in the repo root
    for file_path in create_files:
        full_path = tmp_root / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(f"# {file_path}\n")

    return spec_dir


def _write_status_file(repo_root: Path, spec_id: str, payload: dict):
    status_dir = repo_root.parent / f"{repo_root.name}-worktrees"
    status_dir.mkdir(parents=True, exist_ok=True)
    (status_dir / f"run-{spec_id}.status.json").write_text(json.dumps(payload))


class TestPrecheckWarn(unittest.TestCase):
    """AC-1: all files present → WARN line, exit code 1"""

    def test_all_files_present_single_file(self):
        """Single pending impl task with all files present."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001", status="pending", kind="impl", files=["src/lib.py"]
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"src/lib.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1, "Should return exit code 1")
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("all listed files already exist", output)

    def test_all_files_present_multiple_files(self):
        """Pending impl task with multiple files, all present."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/a.py", "src/b.py", "src/c.py"]
            tasks = [_make_task("TASK-001", status="pending", kind="impl", files=files)]
            _make_spec_dir(tmp_root, tasks, create_files=set(files))

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)

    def test_fanout_failed_status_blocks_relaunch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001", status="pending", kind="impl", files=["src/lib.py"]
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())
            _write_status_file(
                tmp_root,
                "001-test",
                {
                    "phase": "fanout_failed",
                    "failed_tasks": [{"id": "TASK-001"}],
                    "blocked_tasks": [{"id": "TASK-002"}],
                },
            )

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("fanout_failed", output)
            self.assertIn("TASK-001", output)
            self.assertIn("TASK-002", output)


class TestPrecheckSilent(unittest.TestCase):
    """AC-2: no files present → silent, exit code 0"""

    def test_no_files_present_single_file(self):
        """Pending impl task with no files present."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001", status="pending", kind="impl", files=["src/lib.py"]
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0, "Should return exit code 0")
            self.assertEqual(output.strip(), "", "Should be silent")

    def test_no_files_present_multiple_files(self):
        """Pending impl task with multiple files, none present."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/a.py", "src/b.py", "src/c.py"]
            tasks = [_make_task("TASK-001", status="pending", kind="impl", files=files)]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertEqual(output.strip(), "")


class TestPrecheckInfo(unittest.TestCase):
    """AC-3: partial files present → INFO line, exit code 0"""

    def test_partial_files_single_missing(self):
        """Multiple files, one missing."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/a.py", "src/b.py", "src/c.py"]
            tasks = [_make_task("TASK-001", status="pending", kind="impl", files=files)]
            _make_spec_dir(tmp_root, tasks, create_files={"src/a.py", "src/b.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0, "Should return exit code 0")
            self.assertIn("INFO: TASK-001", output)
            self.assertIn("2 of 3", output)
            self.assertIn("partial", output)

    def test_partial_files_single_present(self):
        """Multiple files, only one present."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/a.py", "src/b.py", "src/c.py"]
            tasks = [_make_task("TASK-001", status="pending", kind="impl", files=files)]
            _make_spec_dir(tmp_root, tasks, create_files={"src/a.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("INFO: TASK-001", output)
            self.assertIn("1 of 3", output)


class TestPrecheckKindFilter(unittest.TestCase):
    """AC-8: e2e/cleanup/docs kinds skipped regardless of file presence"""

    def test_e2e_task_skipped_all_files_present(self):
        """e2e task with all files present should be skipped silently."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="pending", kind="e2e", files=["test.py"])
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"test.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertNotIn("TASK-001", output, "e2e task should be skipped")

    def test_cleanup_task_skipped_all_files_present(self):
        """cleanup task with all files present should be skipped silently."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001", status="pending", kind="cleanup", files=["cleanup.py"]
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"cleanup.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertNotIn("TASK-001", output, "cleanup task should be skipped")

    def test_docs_task_skipped_all_files_present(self):
        """docs task with all files present should be skipped silently."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="pending", kind="docs", files=["docs.md"])
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"docs.md"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertNotIn("TASK-001", output, "docs task should be skipped")


class TestPrecheckAllCompleted(unittest.TestCase):
    """AC-9: all tasks completed → silent, exit code 0"""

    def test_all_completed_status(self):
        """All tasks have completed status, even with files present."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="completed", kind="impl", files=["a.py"]),
                _make_task("TASK-002", status="completed", kind="impl", files=["b.py"]),
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"a.py", "b.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertEqual(output.strip(), "", "Should be silent when all completed")

    def test_all_done_status(self):
        """All tasks have done status."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="done", kind="impl", files=["a.py"]),
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"a.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertEqual(output.strip(), "")


class TestPrecheckEmptyFiles(unittest.TestCase):
    """FR-4: tasks with files: [] are silently skipped"""

    def test_empty_files_list_skipped(self):
        """Task with empty files list should be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [_make_task("TASK-001", status="pending", kind="impl", files=[])]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertEqual(
                output.strip(), "", "Empty files task should be skipped silently"
            )

    def test_no_files_field_skipped(self):
        """Task without files field should be skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            task = {"id": "TASK-001", "status": "pending", "kind": "impl"}
            tasks = [task]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertEqual(
                output.strip(), "", "Task without files field should be skipped"
            )


class TestPrecheckMixed(unittest.TestCase):
    """Combined scenarios: WARN + INFO in same run, and exit code behavior"""

    def test_warn_and_info_in_same_run(self):
        """Multiple tasks with different file presence states."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="pending", kind="impl", files=["a.py"]),
                _make_task(
                    "TASK-002", status="pending", kind="impl", files=["b.py", "c.py"]
                ),
                _make_task(
                    "TASK-003", status="pending", kind="impl", files=["d.py", "e.py"]
                ),
            ]
            # All files present for TASK-001 (WARN), partial for TASK-002 (INFO), none for TASK-003 (silent)
            _make_spec_dir(tmp_root, tasks, create_files={"a.py", "b.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1, "Should return 1 due to WARN")
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("INFO: TASK-002", output)
            self.assertNotIn("TASK-003", output)

    def test_multiple_warns_return_exit_1(self):
        """Multiple tasks with all files present."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="pending", kind="impl", files=["a.py"]),
                _make_task("TASK-002", status="pending", kind="impl", files=["b.py"]),
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"a.py", "b.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("WARN: TASK-002", output)

    def test_info_only_returns_exit_0(self):
        """Only INFO messages, no WARN → exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001", status="pending", kind="impl", files=["a.py", "b.py"]
                ),
                _make_task(
                    "TASK-002", status="pending", kind="impl", files=["c.py", "d.py"]
                ),
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"a.py", "c.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("INFO: TASK-001", output)
            self.assertIn("INFO: TASK-002", output)
            self.assertNotIn("WARN", output)


class TestPrecheckModifyPipeline(unittest.TestCase):
    """Modify pipeline (Route F/G, task id TASK-CHG-*): the "files already
    exist" check is inverted -- a bugfix/spec-change task's whole point is
    to modify pre-existing files, so every legitimate Route F/G task was
    tripping the new-pipeline WARN forever until this fix."""

    def test_all_files_present_is_silent(self):
        """All listed files existing is the CORRECT state for a modify
        task -- must not WARN (the false positive this fix removes)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/a.py", "src/b.py"]
            tasks = [
                _make_task("TASK-CHG-001", status="pending", kind="impl", files=files)
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set(files))

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(
                result, 0, "All pre-existing files should be silent, not WARN"
            )
            self.assertEqual(output.strip(), "")

    def test_missing_file_warns(self):
        """A modify task with a MISSING listed file is the actually
        suspicious case -- WARN, exit code 1."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/a.py", "src/b.py", "src/c.py"]
            tasks = [
                _make_task("TASK-CHG-001", status="pending", kind="impl", files=files)
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"src/a.py", "src/b.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-CHG-001", output)
            self.assertIn("1 of 3", output)
            self.assertIn("do not exist yet", output)

    def test_no_files_present_warns(self):
        """None of a modify task's listed files exist -- also suspicious,
        WARN."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-CHG-001", status="pending", kind="impl", files=["src/a.py"]
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-CHG-001", output)
            self.assertIn("1 of 1", output)

    def test_mixed_new_and_modify_tasks_each_use_own_rule(self):
        """A plain TASK-NNN and a TASK-CHG-NNN in the same run each get
        their own pipeline's check, independent of each other."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="pending", kind="impl", files=["a.py"]),
                _make_task(
                    "TASK-CHG-001", status="pending", kind="impl", files=["b.py"]
                ),
            ]
            # a.py exists -> new-pipeline WARN (all present, unexpected).
            # b.py exists -> modify-pipeline silent (all present, expected).
            _make_spec_dir(tmp_root, tasks, create_files={"a.py", "b.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertNotIn("TASK-CHG-001", output)


class TestPrecheckModifyOnlyBodyDetection(unittest.TestCase):
    """brief 20260723-162700: a plain TASK-NNN (new/Route C-D) task that is
    legitimately modify-only -- adds steps/wiring to an existing file,
    creates nothing new -- must not false-WARN "already implemented" just
    because the id doesn't start with TASK-CHG-. The task BODY's own
    "Files to Create"/"Files to Modify" split (already written by
    spec-to-tasks) is the real signal, checked ahead of the id-prefix
    heuristic."""

    _MODIFY_ONLY_BODY = (
        "# TASK-005: Wire checks into the ci-guardrails job\n\n"
        "## Implementation Details (File names only, no code)\n\n"
        "**Files to Modify**:\n"
        "- `.github/workflows/qa-pipeline.yml` - add a new validation step\n"
        "  (unconditional on pull_request), matching an existing step's shape.\n\n"
        "## Test Instructions\n"
    )

    def test_modify_only_body_silences_false_positive_on_plain_task_id(self):
        """Reproduces the real TASK-005 bug: files: [existing.yml], no
        TASK-CHG- prefix, body has ONLY a Files to Modify section -- must be
        silent (correct/expected state), not WARN."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = [".github/workflows/qa-pipeline.yml"]
            tasks = [
                _make_task(
                    "TASK-005",
                    status="pending",
                    kind="impl",
                    files=files,
                    body=self._MODIFY_ONLY_BODY,
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set(files))

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0, f"should be silent, got: {output!r}")
            self.assertEqual(output.strip(), "")

    def test_modify_only_body_still_warns_on_missing_file(self):
        """Same modify-only body, but the listed file is missing -- the
        actually suspicious case for a modify-only task -- still WARNs."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = [".github/workflows/qa-pipeline.yml"]
            tasks = [
                _make_task(
                    "TASK-005",
                    status="pending",
                    kind="impl",
                    files=files,
                    body=self._MODIFY_ONLY_BODY,
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-005", output)
            self.assertIn("do not exist yet", output)

    def test_body_with_files_to_create_still_uses_create_check(self):
        """A plain TASK-NNN body with a non-empty Files to Create section
        keeps the original create-pipeline check (WARN if all exist),
        confirming the body signal is not applied blindly to every task."""
        body = (
            "# TASK-002\n\n"
            "## Implementation Details (File names only, no code)\n\n"
            "**Files to Create**:\n"
            "- `src/new_module.py` - new module\n\n"
            "## Test Instructions\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/new_module.py"]
            tasks = [
                _make_task(
                    "TASK-002", status="pending", kind="impl", files=files, body=body
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set(files))

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-002", output)
            self.assertIn("all listed files already exist", output)

    def test_bodiless_task_falls_back_to_id_prefix_heuristic(self):
        """No body sections at all (older/legacy task file) -- behavior is
        unchanged from before this fix: plain id -> create-check WARN."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            files = ["src/a.py"]
            tasks = [_make_task("TASK-003", status="pending", kind="impl", files=files)]
            _make_spec_dir(tmp_root, tasks, create_files=set(files))

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-003", output)


class TestPrecheckFrontmatterTypoWarning(unittest.TestCase):
    """route:J loader-frontmatter-schema-validation: precheck() surfaces
    loader.py's frontmatter_warnings as WARN lines, for every task regardless
    of kind/status."""

    def test_typo_key_warns_and_fails_precheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    files=["a.py"],
                    extra_fm={"revie": "skip"},
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("'revie'", output)
            self.assertIn("review", output)

    def test_typo_warning_applies_even_to_e2e_kind(self):
        """Frontmatter warnings fire regardless of kind (unlike the file-
        existence checks, which skip e2e/cleanup/docs)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001", status="pending", kind="e2e", extra_fm={"kinf": "e2e"}
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", captured.getvalue())

    def test_clean_frontmatter_no_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="pending", kind="impl", files=["a.py"])
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            self.assertEqual(result, 0)
            self.assertEqual(captured.getvalue().strip(), "")

    def test_depends_on_alias_does_not_warn(self):
        """The real incident's exact key (depends_on) is a recognized alias,
        not a typo -- must stay silent. TASK-000 is included so the aliased
        same-spec dependency also resolves, keeping this test isolated to the
        typo-vs-alias question it's about rather than same-spec dependency
        resolution (covered by devkit's own validate_dependencies tests)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-000", status="completed", kind="impl"),
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    files=["a.py"],
                    extra_fm={"depends_on": "[TASK-000]"},
                ),
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            self.assertEqual(result, 0)
            self.assertEqual(captured.getvalue().strip(), "")


class TestPrecheckDefaultKind(unittest.TestCase):
    """Tasks without explicit kind default to impl"""

    def test_task_without_kind_defaults_to_impl(self):
        """Task without kind field defaults to impl and is not skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            task = {"id": "TASK-001", "status": "pending", "files": ["a.py"]}
            tasks = [task]
            _make_spec_dir(tmp_root, tasks, create_files={"a.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)


class TestPrecheckExternalDeps(unittest.TestCase):
    """AC-009, AC-010, AC-011: precheck() resolves/reports each pending
    task's `external-dependencies:` entries per
    contracts/precheck-external-deps-report.md."""

    @staticmethod
    def _write_sibling_task(tmp_root, spec_id, task_id, status):
        """Write a real docs/specs/<spec_id>/tasks/<task_id>.md -- the fixed
        location loader.resolve_external_dependency always resolves against,
        independent of _make_spec_dir's own (non docs/specs) test layout."""
        tasks_dir = tmp_root / "docs" / "specs" / spec_id / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        (tasks_dir / f"{task_id}.md").write_text(
            f"---\nid: {task_id}\nstatus: {status}\n---\n# {task_id}\n"
        )

    def test_unresolved_entry_warns_and_fails(self):
        """AC-009: unresolved external-dependencies entry -> WARN naming the
        task id and the unresolved reference, non-zero exit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={"external-dependencies": '["099-missing/TASK-999"]'},
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("099-missing/TASK-999", output)

    def test_resolved_entry_pending_is_info_and_non_blocking(self):
        """AC-010: resolved entry to a sibling task with status: pending ->
        INFO reporting status=pending; does not by itself return non-zero."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            self._write_sibling_task(tmp_root, "098-x", "TASK-036", "pending")
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={"external-dependencies": '["098-x/TASK-036"]'},
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("INFO: TASK-001", output)
            self.assertIn("098-x/TASK-036", output)
            self.assertIn("status=pending", output)

    def test_resolved_entry_done_reports_done_status(self):
        """AC-010: resolved entry to a sibling task with status: done ->
        INFO reporting status=done."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            self._write_sibling_task(tmp_root, "098-x", "TASK-036", "done")
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={"external-dependencies": '["098-x/TASK-036"]'},
                )
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 0)
            self.assertIn("INFO: TASK-001", output)
            self.assertIn("status=done", output)

    def test_unresolved_on_one_task_does_not_stop_evaluating_next(self):
        """AC-011: an unresolved entry on one task does not short-circuit
        evaluation of a later task's resolved entry in the same run."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            self._write_sibling_task(tmp_root, "098-x", "TASK-036", "pending")
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={"external-dependencies": '["099-missing/TASK-999"]'},
                ),
                _make_task(
                    "TASK-002",
                    status="pending",
                    kind="impl",
                    extra_fm={"external-dependencies": '["098-x/TASK-036"]'},
                ),
            ]
            _make_spec_dir(tmp_root, tasks, create_files=set())

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("INFO: TASK-002", output)
            self.assertIn("status=pending", output)

    def test_no_external_deps_is_byte_identical_regression(self):
        """AC-019 regression: a spec with zero external-dependencies:
        entries anywhere produces output byte-identical to precheck()'s
        pre-existing behavior (same WARN/INFO lines, same exit code)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-001", status="pending", kind="impl", files=["a.py"]),
                _make_task(
                    "TASK-002", status="pending", kind="impl", files=["b.py", "c.py"]
                ),
            ]
            _make_spec_dir(tmp_root, tasks, create_files={"a.py", "b.py"})

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("all listed files already exist", output)
            self.assertIn("INFO: TASK-002", output)
            self.assertNotIn("external dependency", output)


class TestPrecheckDependencyValidation(unittest.TestCase):
    """5.4: precheck() surfaces the loaded `TaskSource`'s
    `validate_dependencies()` diagnostics (unresolved same-spec `deps` and
    unsettled `decision-refs:`) as WARN lines and folds them into
    `warn_count`, per the wiring added in live.py (4.1)."""

    @staticmethod
    def _make_docs_spec_dir(tmp_root, spec_id, tasks, decision_log=None):
        """Write tasks under docs/specs/<spec_id>/tasks/ -- the layout
        `taskformats.task_source_for()` needs in order to resolve
        `DevkitSpecTaskSource.spec_root()` (and therefore decision-log.md)
        correctly. The plain `specs/<id>` layout `_make_spec_dir` uses
        elsewhere in this file resolves to the wrong repo_root for that
        lookup, since task_source_for's path-split assumes a `docs/specs/`
        prefix."""
        spec_dir = tmp_root / "docs" / "specs" / spec_id
        tasks_dir = spec_dir / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        for task in tasks:
            task_id = task["id"]
            frontmatter_lines = [f"id: {task_id}"]
            if "status" in task:
                frontmatter_lines.append(f"status: {task['status']}")
            if "kind" in task:
                frontmatter_lines.append(f"kind: {task['kind']}")
            for extra_key, extra_val in (task.get("extra_fm") or {}).items():
                frontmatter_lines.append(f"{extra_key}: {extra_val}")
            body = task.get("body") or f"# {task_id}\n"
            content = f"---\n{chr(10).join(frontmatter_lines)}\n---\n{body}"
            (tasks_dir / f"{task_id}.md").write_text(content)
        if decision_log is not None:
            (spec_dir / "decision-log.md").write_text(decision_log)
        return spec_dir

    def test_unresolved_same_spec_dependency_warns_and_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={"dependencies": '["TASK-999"]'},
                )
            ]
            self._make_docs_spec_dir(tmp_root, "001-test", tasks)

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "docs/specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("unresolved same-spec dependency", output)
            self.assertIn("TASK-999", output)

    def test_missing_decision_log_diagnostic_warns_and_fails(self):
        """A task references `decision-refs:` but no decision-log.md exists
        for the spec at all -- treated as "missing", not silently skipped."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={"decision-refs": '["D9"]'},
                )
            ]
            self._make_docs_spec_dir(tmp_root, "001-test", tasks)

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "docs/specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("missing decision", output)
            self.assertIn("D9", output)

    def test_open_decision_diagnostic_warns_and_fails(self):
        """A referenced decision exists in the log but is not decided/
        resolved -- "open decision" WARN, non-zero exit."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={"decision-refs": '["D9"]'},
                )
            ]
            self._make_docs_spec_dir(
                tmp_root,
                "001-test",
                tasks,
                decision_log="## D9: Pick a storage backend\nStatus: proposed\n",
            )

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "docs/specs/001-test")

            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn("WARN: TASK-001", output)
            self.assertIn("open decision", output)
            self.assertIn("D9", output)
            self.assertIn("proposed", output)

    def test_resolved_dependency_and_decided_decision_stay_clean(self):
        """A `deps` entry that resolves within the spec and a `decision-refs:`
        entry that is `decided` in the log both produce no diagnostic --
        exit code 0, same as the pre-existing no-findings contract."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [
                _make_task("TASK-000", status="completed", kind="impl"),
                _make_task(
                    "TASK-001",
                    status="pending",
                    kind="impl",
                    extra_fm={
                        "dependencies": '["TASK-000"]',
                        "decision-refs": '["D9"]',
                    },
                ),
            ]
            self._make_docs_spec_dir(
                tmp_root,
                "001-test",
                tasks,
                decision_log="## D9: Pick a storage backend\nStatus: decided\n",
            )

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "docs/specs/001-test")

            self.assertEqual(result, 0)
            self.assertEqual(captured.getvalue().strip(), "")

    def test_no_deps_or_decision_refs_stays_clean(self):
        """No `deps` and no `decision-refs:` declared anywhere -- unchanged
        from precheck's pre-existing silent/exit-0 behavior."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            tasks = [_make_task("TASK-001", status="pending", kind="impl")]
            self._make_docs_spec_dir(tmp_root, "001-test", tasks)

            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "docs/specs/001-test")

            self.assertEqual(result, 0)
            self.assertEqual(captured.getvalue().strip(), "")


class TestPrecheckSerialisedDag(unittest.TestCase):
    """A pending task DAG that collapses to one serial chain WARNs before launch
    (brief 20260901-175140: 19 same-file tasks ran one at a time for hours)."""

    def _chain(self, n):
        tasks = []
        for i in range(1, n + 1):
            extra = {"dependencies": f"[TASK-{i - 1:03d}]"} if i > 1 else None
            tasks.append(
                _make_task(f"TASK-{i:03d}", files=[f"src/new_{i}.py"], extra_fm=extra)
            )
        return tasks

    def test_a_long_serial_chain_warns_with_a_wall_clock_projection(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _make_spec_dir(tmp_root, self._chain(6))
            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")
            output = captured.getvalue()
            self.assertEqual(result, 1)
            self.assertIn(
                "WARN: task DAG is effectively serial (6 tasks, critical path 6, width 1)",
                output,
            )
            self.assertIn("projected wall-clock", output)

    def test_a_short_chain_stays_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            _make_spec_dir(tmp_root, self._chain(3))
            captured = io.StringIO()
            with mock.patch("sys.stdout", captured):
                result = live.precheck(tmp_root, "specs/001-test")
            self.assertEqual(result, 0)
            self.assertEqual(captured.getvalue().strip(), "")


if __name__ == "__main__":
    unittest.main()
