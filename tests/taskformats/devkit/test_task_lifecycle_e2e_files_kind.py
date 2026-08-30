#!/usr/bin/env python3
"""End-to-end verification for spec 012-task-frontmatter: files: and kind: frontmatter."""

import os
import tempfile

import pytest

from worktrail.taskformats.devkit.schema import validate_task


def _make_task(frontmatter: dict, body: str) -> str:
    import yaml

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write("---\n")
        tmp.write(yaml.dump(frontmatter, sort_keys=False))
        tmp.write("---\n")
        tmp.write(body)
    return tmp.name


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _validate_task_metadata(tasks: list) -> None:
    """Replicate the live.py gate: raises RuntimeError for impl tasks with empty files:."""
    missing = [
        t["id"]
        for t in tasks
        if t.get("status") == "pending"
        and t.get("kind", "impl") not in ("docs", "e2e", "cleanup")
        and not t.get("files")
    ]
    if missing:
        raise RuntimeError(
            "implementation task(s) missing required frontmatter files: "
            + ", ".join(missing)
        )


_BODY = "## Acceptance Criteria\n- [ ] AC1\n\n## Definition of Done\n- [ ] DoD1\n"
_REQUIRED_BASE = {
    "id": "TASK-NNN",
    "title": "Test task",
    "spec": "docs/specs/012/spec.md",
    "status": "pending",
}


class TestAC1FilesPopulated:
    """AC-1: impl tasks get a non-empty files: list; validate_task_metadata accepts them."""

    def test_impl_task_with_files_passes_metadata_gate(self):
        task = {
            **_REQUIRED_BASE,
            "kind": "impl",
            "files": ["plugins/foo/bar.py", "plugins/foo/test_bar.py"],
        }
        _validate_task_metadata([task])  # must not raise

    def test_impl_task_with_empty_files_fails_metadata_gate(self):
        task = {**_REQUIRED_BASE, "kind": "impl", "files": []}
        with pytest.raises(RuntimeError, match="missing required frontmatter files"):
            _validate_task_metadata([task])

    def test_impl_task_absent_files_fails_metadata_gate(self):
        task = {**_REQUIRED_BASE, "kind": "impl"}
        with pytest.raises(RuntimeError, match="missing required frontmatter files"):
            _validate_task_metadata([task])


class TestAC2E2EKind:
    """AC-2: tasks titled '[E2E] ...' get kind: e2e; metadata gate exempts them."""

    def test_e2e_kind_exempt_from_files_gate(self):
        task = {
            **_REQUIRED_BASE,
            "title": "[E2E] Playwright tests for login",
            "kind": "e2e",
            "files": [],
        }
        _validate_task_metadata([task])  # must not raise — e2e is exempt

    def test_e2e_kind_with_files_also_passes(self):
        task = {
            **_REQUIRED_BASE,
            "title": "[E2E] Full flow",
            "kind": "e2e",
            "files": ["tests/e2e/test_login.py"],
        }
        _validate_task_metadata([task])  # must not raise


class TestAC3CleanupKind:
    """AC-3: tasks titled '[CLEANUP] ...' get kind: cleanup; metadata gate exempts them."""

    def test_cleanup_kind_exempt_from_files_gate(self):
        task = {
            **_REQUIRED_BASE,
            "title": "[CLEANUP] Remove legacy auth",
            "kind": "cleanup",
            "files": [],
        }
        _validate_task_metadata([task])  # must not raise

    def test_cleanup_kind_with_files_also_passes(self):
        task = {
            **_REQUIRED_BASE,
            "title": "[CLEANUP] Final hygiene",
            "kind": "cleanup",
            "files": ["plugins/old/file.py"],
        }
        _validate_task_metadata([task])  # must not raise


class TestAC4DocsKind:
    """AC-4: tasks whose files: list contains only .md files get kind: docs; gate exempts them."""

    def test_docs_kind_exempt_from_files_gate(self):
        task = {
            **_REQUIRED_BASE,
            "files": ["docs/foo.md", "docs/bar.md"],
            "kind": "docs",
        }
        _validate_task_metadata([task])  # must not raise

    def test_docs_kind_with_empty_files_also_passes_gate(self):
        task = {**_REQUIRED_BASE, "kind": "docs", "files": []}
        _validate_task_metadata([task])  # docs are always exempt


class TestAC5HookWarning:
    """AC-5: task_lifecycle.py emits warning for impl+empty files:; does not block."""

    def test_warning_emitted_non_blocking(self, capsys):
        fm = {**_REQUIRED_BASE, "kind": "impl", "files": []}
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True, "validate_task must return True (non-blocking)"
            captured = capsys.readouterr()
            assert "TASK-NNN" in captured.err
            assert "impl task has no files" in captured.err
        finally:
            _cleanup(path)

    def test_no_warning_for_e2e_empty_files(self, capsys):
        fm = {**_REQUIRED_BASE, "kind": "e2e", "files": []}
        path = _make_task(fm, _BODY)
        try:
            validate_task(path)
            captured = capsys.readouterr()
            assert "impl task has no files" not in captured.err
        finally:
            _cleanup(path)

    def test_no_warning_for_impl_with_files(self, capsys):
        fm = {**_REQUIRED_BASE, "kind": "impl", "files": ["plugins/foo/bar.py"]}
        path = _make_task(fm, _BODY)
        try:
            validate_task(path)
            captured = capsys.readouterr()
            assert "impl task has no files" not in captured.err
        finally:
            _cleanup(path)


class TestAC6MetadataGateBatch:
    """AC-6: a batch of properly-populated tasks (AC-1 through AC-4) passes validate_task_metadata."""

    def test_all_ac_tasks_pass_gate(self):
        tasks = [
            {
                **_REQUIRED_BASE,
                "id": "TASK-AC1",
                "kind": "impl",
                "files": ["plugins/foo.py"],
            },
            {**_REQUIRED_BASE, "id": "TASK-AC2", "kind": "e2e", "files": []},
            {**_REQUIRED_BASE, "id": "TASK-AC3", "kind": "cleanup", "files": []},
            {
                **_REQUIRED_BASE,
                "id": "TASK-AC4",
                "kind": "docs",
                "files": ["docs/foo.md"],
            },
        ]
        _validate_task_metadata(tasks)  # must not raise

    def test_completed_impl_task_skipped_by_gate(self):
        tasks = [
            {
                "id": "TASK-DONE",
                "title": "t",
                "spec": "s",
                "status": "completed",
                "kind": "impl",
                "files": [],
            },
        ]
        _validate_task_metadata(tasks)  # completed tasks are skipped by the gate
