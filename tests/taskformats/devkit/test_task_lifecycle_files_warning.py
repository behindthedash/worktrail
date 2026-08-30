#!/usr/bin/env python3
"""Tests for impl-files warning in task_lifecycle.py"""

import os
import tempfile

from worktrail.taskformats.devkit.schema import validate_task

# --- Helpers ---


def _make_task(frontmatter: dict, body: str) -> str:
    """Write a temporary task file and return its path."""
    import yaml

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", delete=False, encoding="utf-8"
    )
    tmp.write("---\n")
    tmp.write(yaml.dump(frontmatter, sort_keys=False))
    tmp.write("---\n")
    tmp.write(body)
    tmp.close()
    return tmp.name


def _cleanup(path: str):
    try:
        os.unlink(path)
    except OSError:
        pass


_REQUIRED_FM = {
    "id": "TASK-001",
    "title": "Test task",
    "spec": "spec-001",
    "status": "pending",
}
_BODY = "## Acceptance Criteria\n- [ ] AC1\n\n## Definition of Done\n- [ ] DoD1\n"


class TestImplFilesWarning:
    """Test the impl-files warning behavior in validate_task."""

    def test_impl_with_empty_files_emits_warning(self, capsys):
        """kind=impl and files=[] should emit warning but return True."""
        fm = {**_REQUIRED_FM, "kind": "impl", "files": []}
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True, "validate_task should return True (non-blocking)"
            captured = capsys.readouterr()
            assert "TASK-001" in captured.err
            assert "impl task has no files" in captured.err
        finally:
            _cleanup(path)

    def test_no_kind_with_empty_files_emits_warning(self, capsys):
        """No kind field (defaults to impl) and files=[] should emit warning."""
        fm = {**_REQUIRED_FM, "files": []}
        # kind is absent; should default to "impl"
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True
            captured = capsys.readouterr()
            assert "TASK-001" in captured.err
            assert "impl task has no files" in captured.err
        finally:
            _cleanup(path)

    def test_no_files_field_emits_warning(self, capsys):
        """kind=impl and no files field (defaults to empty) should emit warning."""
        fm = {**_REQUIRED_FM, "kind": "impl"}
        # files is absent; should default to empty list
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True
            captured = capsys.readouterr()
            assert "TASK-001" in captured.err
            assert "impl task has no files" in captured.err
        finally:
            _cleanup(path)

    def test_e2e_with_empty_files_no_warning(self, capsys):
        """kind=e2e and files=[] should NOT emit warning."""
        fm = {**_REQUIRED_FM, "kind": "e2e", "files": []}
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True
            captured = capsys.readouterr()
            assert "impl task has no files" not in captured.err
        finally:
            _cleanup(path)

    def test_cleanup_with_empty_files_no_warning(self, capsys):
        """kind=cleanup and files=[] should NOT emit warning."""
        fm = {**_REQUIRED_FM, "kind": "cleanup", "files": []}
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True
            captured = capsys.readouterr()
            assert "impl task has no files" not in captured.err
        finally:
            _cleanup(path)

    def test_docs_with_empty_files_no_warning(self, capsys):
        """kind=docs and files=[] should NOT emit warning."""
        fm = {**_REQUIRED_FM, "kind": "docs", "files": []}
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True
            captured = capsys.readouterr()
            assert "impl task has no files" not in captured.err
        finally:
            _cleanup(path)

    def test_impl_with_non_empty_files_no_warning(self, capsys):
        """kind=impl and files=['some/path.py'] should NOT emit warning."""
        fm = {**_REQUIRED_FM, "kind": "impl", "files": ["some/path.py"]}
        path = _make_task(fm, _BODY)
        try:
            result = validate_task(path)
            assert result is True
            captured = capsys.readouterr()
            assert "impl task has no files" not in captured.err
        finally:
            _cleanup(path)
