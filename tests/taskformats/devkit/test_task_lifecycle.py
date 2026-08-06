#!/usr/bin/env python3
"""Tests for task_lifecycle.py — covers status detection, validation, and lifecycle transitions."""

import os
import sys
import subprocess
import tempfile
import pytest

from worktrail.taskformats.devkit.schema import (
    TaskStatus,
    FIELD_SCHEMA,
    COMPLETION_AUDIT_SECTIONS,
    detect_status_from_body,
    _all_dod_complete,
    _all_checkboxes_checked,
    _extract_sections,
    is_task_file,
    read_task_file,
    write_task_file,
    update_status,
    validate_task,
)

# `worktrail-task-lifecycle` console script / `python -m
# worktrail.taskformats.devkit.cli` -- not direct file execution, since
# cli.py uses package-absolute imports.
CLI_ARGV = [sys.executable, "-m", "worktrail.taskformats.devkit.cli"]


# --- Helpers ---

def _make_task(frontmatter: dict, body: str) -> str:
    """Write a temporary task file and return its path."""
    import yaml
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8")
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


# --- P2: superseded is a valid status ---

class TestSupersededStatus:
    """superseded must be accepted by the schema and preserved by update_status."""

    def test_superseded_in_task_status_class(self):
        assert TaskStatus.SUPERSEDED == "superseded"

    def test_superseded_in_field_schema_values(self):
        assert "superseded" in FIELD_SCHEMA["status"]["values"]

    def test_validate_accepts_superseded(self):
        fm = {
            "id": "TASK-001",
            "title": "Test task",
            "spec": "spec-001",
            "status": "superseded",
        }
        body = "## Acceptance Criteria\n- [ ] AC1\n\n## Definition of Done\n- [ ] DoD1\n"
        path = _make_task(fm, body)
        try:
            assert validate_task(path) is True
        finally:
            _cleanup(path)

    def test_auto_status_preserves_superseded(self):
        fm = {
            "id": "TASK-001",
            "title": "Test task",
            "spec": "spec-001",
            "status": "superseded",
        }
        body = "## Acceptance Criteria\n- [x] AC1\n\n## Definition of Done\n- [x] DoD1\n"
        path = _make_task(fm, body)
        try:
            update_status(path)
            updated_fm, _, _ = read_task_file(__import__("pathlib").Path(path))
            assert updated_fm["status"] == "superseded"
        finally:
            _cleanup(path)


# --- P3: implemented -> reviewed -> completed transitions ---

class TestStatusPromotion:
    """detect_status_from_body must promote implemented -> reviewed when DoD is complete."""

    def test_implemented_promoted_to_reviewed_when_dod_complete(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done\n"
            "- [x] DoD1\n"
        )
        result = detect_status_from_body(body, old_status="implemented")
        assert result == TaskStatus.REVIEWED

    def test_implemented_stays_when_dod_incomplete(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done\n"
            "- [ ] DoD1\n"
        )
        result = detect_status_from_body(body, old_status="implemented")
        assert result == TaskStatus.IMPLEMENTED

    def test_reviewed_preserved(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done\n"
            "- [x] DoD1\n"
        )
        result = detect_status_from_body(body, old_status="reviewed")
        assert result == TaskStatus.REVIEWED

    def test_all_checked_new_task_gets_implemented(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done\n"
            "- [x] DoD1\n"
        )
        result = detect_status_from_body(body, old_status="pending")
        assert result == TaskStatus.IMPLEMENTED

    def test_partial_gets_in_progress(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "- [ ] AC2\n"
        )
        result = detect_status_from_body(body, old_status="pending")
        assert result == TaskStatus.IN_PROGRESS

    def test_no_checkboxes_gets_pending(self):
        result = detect_status_from_body("No checkboxes here", old_status="in_progress")
        assert result == TaskStatus.PENDING


class TestAllDodComplete:
    """Unit tests for _all_dod_complete helper."""

    def test_all_dod_checked(self):
        body = "## Definition of Done\n- [x] DoD1\n- [x] DoD2\n"
        assert _all_dod_complete(body) is True

    def test_some_dod_unchecked(self):
        body = "## Definition of Done\n- [x] DoD1\n- [ ] DoD2\n"
        assert _all_dod_complete(body) is False

    def test_no_dod_section(self):
        body = "## Acceptance Criteria\n- [x] AC1\n"
        assert _all_dod_complete(body) is False

    def test_dod_empty(self):
        body = "## Definition of Done\n"
        assert _all_dod_complete(body) is True


class TestAllCheckboxesChecked:
    """Unit tests for _all_checkboxes_checked helper."""

    def test_all_checked(self):
        body = "## Acceptance Criteria\n- [x] AC1\n\n## Definition of Done\n- [x] DoD1\n"
        assert _all_checkboxes_checked(body) is True

    def test_some_unchecked(self):
        body = "## Acceptance Criteria\n- [x] AC1\n\n## Definition of Done\n- [ ] DoD1\n"
        assert _all_checkboxes_checked(body) is False

    def test_none_checked(self):
        body = "## Acceptance Criteria\n- [ ] AC1\n\n## Definition of Done\n- [ ] DoD1\n"
        assert _all_checkboxes_checked(body) is False

    def test_no_checkboxes(self):
        assert _all_checkboxes_checked("No checkboxes here") is False

    def test_completion_audit_sections_constant_shape(self):
        assert COMPLETION_AUDIT_SECTIONS == ("Acceptance Criteria", "Definition of Done (DoD)")

    def test_scoped_ac_only_ignores_unchecked_dor(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Ready (DoR)\n"
            "- [ ] DoR1\n"
        )
        assert _all_checkboxes_checked(body, sections=("Acceptance Criteria",)) is True

    def test_scoped_completion_audit_false_when_dod_unchecked(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done (DoD)\n"
            "- [x] DoD1\n"
            "- [ ] DoD2\n"
        )
        assert _all_checkboxes_checked(body, sections=COMPLETION_AUDIT_SECTIONS) is False

    def test_scoped_completion_audit_true_ignoring_other_unchecked_sections(self):
        body = (
            "## Definition of Ready (DoR)\n"
            "- [ ] DoR1\n"
            "\n"
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done (DoD)\n"
            "- [x] DoD1\n"
            "\n"
            "## Test Instructions\n"
            "- [ ] TI1\n"
        )
        assert _all_checkboxes_checked(body, sections=COMPLETION_AUDIT_SECTIONS) is True


class TestExtractSections:
    """Unit tests for _extract_sections helper."""

    def test_extracts_only_named_section(self):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done (DoD)\n"
            "- [ ] DoD1\n"
        )
        extracted = _extract_sections(body, ("Acceptance Criteria",))
        assert "AC1" in extracted
        assert "DoD1" not in extracted

    def test_missing_heading_yields_no_text(self):
        body = "## Acceptance Criteria\n- [x] AC1\n"
        extracted = _extract_sections(body, ("Definition of Done (DoD)",))
        assert "AC1" not in extracted
        assert extracted == ""


class TestCompletedWithUncheckedBoxesWarns:
    """A `status: completed` task whose checkboxes are unticked must warn, not silently pass."""

    def _fm(self):
        return {
            "id": "TASK-007",
            "title": "Test task",
            "spec": "spec-001",
            "status": "completed",
        }

    def test_completed_with_all_boxes_unchecked_emits_warning(self, capsys):
        body = "## Acceptance Criteria\n- [ ] AC1\n\n## Definition of Done\n- [ ] DoD1\n"
        path = _make_task(self._fm(), body)
        try:
            update_status(path)
            captured = capsys.readouterr()
            assert "WARNING" in captured.err
            assert "completed" in captured.err
        finally:
            _cleanup(path)

    def test_completed_with_all_boxes_unchecked_status_not_downgraded(self):
        body = "## Acceptance Criteria\n- [ ] AC1\n\n## Definition of Done\n- [ ] DoD1\n"
        path = _make_task(self._fm(), body)
        try:
            update_status(path)
            updated_fm, _, _ = read_task_file(__import__("pathlib").Path(path))
            assert updated_fm["status"] == "completed"
        finally:
            _cleanup(path)

    def test_completed_with_all_boxes_checked_emits_no_warning(self, capsys):
        body = "## Acceptance Criteria\n- [x] AC1\n\n## Definition of Done\n- [x] DoD1\n"
        path = _make_task(self._fm(), body)
        try:
            update_status(path)
            captured = capsys.readouterr()
            assert "WARNING" not in captured.err
        finally:
            _cleanup(path)

    def test_completed_ac_and_dod_ticked_but_dor_unticked_emits_no_warning(self, capsys):
        body = (
            "## Definition of Ready (DoR)\n"
            "- [ ] DoR1\n"
            "\n"
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done (DoD)\n"
            "- [x] DoD1\n"
        )
        path = _make_task(self._fm(), body)
        try:
            update_status(path)
            captured = capsys.readouterr()
            assert "WARNING" not in captured.err
        finally:
            _cleanup(path)

    def test_completed_with_dod_unticked_still_emits_warning(self, capsys):
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done (DoD)\n"
            "- [ ] DoD1\n"
        )
        path = _make_task(self._fm(), body)
        try:
            update_status(path)
            captured = capsys.readouterr()
            assert "WARNING" in captured.err
        finally:
            _cleanup(path)


class TestUpdateStatusDates:
    """Auto-set date fields on promotion."""

    def test_reviewed_date_set_on_promotion(self):
        fm = {
            "id": "TASK-001",
            "title": "Test task",
            "spec": "spec-001",
            "status": "implemented",
        }
        body = (
            "## Acceptance Criteria\n"
            "- [x] AC1\n"
            "\n"
            "## Definition of Done\n"
            "- [x] DoD1\n"
        )
        path = _make_task(fm, body)
        try:
            update_status(path)
            updated_fm, _, _ = read_task_file(__import__("pathlib").Path(path))
            assert updated_fm["status"] == "reviewed"
            assert updated_fm.get("reviewed_date") is not None
        finally:
            _cleanup(path)


# --- P1-related: ensure test_session_tracker is gone ---

class TestOrphanedTestRemoved:
    """Verify the orphaned test file for session-tracker.py has been removed."""

    def test_session_tracker_test_not_present(self):
        scripts_test_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "scripts", "tests",
        )
        test_file = os.path.join(scripts_test_dir, "test_session_tracker.py")
        assert not os.path.exists(test_file), (
            "test_session_tracker.py still exists but session-tracker.py was deleted"
        )


# --- AC1: out-of-scope files exit clean ---

class TestOutOfScopeFileExitsClean:
    """AC1: task_lifecycle.py must exit 0 with no output for non-task files."""

    def _run(self, action: str, path: str):
        return subprocess.run(
            CLI_ARGV + [ action, path],
            capture_output=True, text=True
        )

    def test_validate_non_task_py_file_exits_zero(self, tmp_path):
        f = tmp_path / "config.py"
        f.write_text("x = 1\n")
        result = self._run("validate", str(f))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_validate_non_task_md_file_exits_zero(self, tmp_path):
        f = tmp_path / "README.md"
        f.write_text("# Hello\n")
        result = self._run("validate", str(f))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_auto_status_non_task_file_exits_zero(self, tmp_path):
        f = tmp_path / "some_script.sh"
        f.write_text("#!/bin/bash\necho hi\n")
        result = self._run("auto-status", str(f))
        assert result.returncode == 0
        assert result.stderr == ""

    def test_no_file_arg_exits_zero(self):
        """Missing CLAUDE_CHANGED_FILE (collapsed to absent arg) exits 0."""
        result = subprocess.run(
            CLI_ARGV + [ "validate"],
            capture_output=True, text=True
        )
        assert result.returncode == 0

    def test_empty_filepath_exits_zero(self):
        """Empty string filepath exits 0."""
        result = subprocess.run(
            CLI_ARGV + [ "validate", ""],
            capture_output=True, text=True
        )
        assert result.returncode == 0


# --- AC3: invalid in-scope task files fail with stderr ---

class TestInvalidTaskFileFailsWithStderr:
    """AC3: invalid in-scope TASK-*.md must exit 1 and write errors to stderr."""

    def test_invalid_task_file_exits_nonzero(self, tmp_path):
        # File name matches TASK-\d+\.md$ but contents are missing required fields
        f = tmp_path / "TASK-999.md"
        f.write_text("---\ntitle: Broken task\n---\n# No required fields\n")
        result = subprocess.run(
            CLI_ARGV + [ "validate", str(f)],
            capture_output=True, text=True
        )
        assert result.returncode != 0

    def test_invalid_task_file_emits_errors_to_stderr(self, tmp_path):
        f = tmp_path / "TASK-999.md"
        f.write_text("---\ntitle: Broken task\n---\n# No required fields\n")
        result = subprocess.run(
            CLI_ARGV + [ "validate", str(f)],
            capture_output=True, text=True
        )
        assert "Validation failed" in result.stderr
        # At least one specific error message must be present
        assert result.stderr.strip() != ""

    def test_invalid_task_file_has_no_errors_on_stdout(self, tmp_path):
        f = tmp_path / "TASK-999.md"
        f.write_text("---\ntitle: Broken task\n---\n# No required fields\n")
        result = subprocess.run(
            CLI_ARGV + [ "validate", str(f)],
            capture_output=True, text=True
        )
        # Validation failure output must NOT appear on stdout
        assert "Validation failed" not in result.stdout


# --- FR-8: timeout field in FIELD_SCHEMA ---

_REQUIRED_FM = {
    "id": "TASK-001",
    "title": "Test task",
    "spec": "spec-001",
    "status": "pending",
}
_BODY = "## Acceptance Criteria\n- [ ] AC1\n\n## Definition of Done\n- [ ] DoD1\n"


class TestComplexityDomainFieldSchema:
    """AC-010/REQ-014: complexity/domain are optional string fields with no values enum."""

    def test_complexity_in_field_schema(self):
        assert "complexity" in FIELD_SCHEMA
        assert FIELD_SCHEMA["complexity"]["type"] is str
        assert FIELD_SCHEMA["complexity"]["required"] is False
        assert "values" not in FIELD_SCHEMA["complexity"]

    def test_domain_in_field_schema(self):
        assert "domain" in FIELD_SCHEMA
        assert FIELD_SCHEMA["domain"]["type"] is str
        assert FIELD_SCHEMA["domain"]["required"] is False
        assert "values" not in FIELD_SCHEMA["domain"]

    def test_complexity_and_domain_pass_validation(self):
        fm = {**_REQUIRED_FM, "complexity": "standard", "domain": "backend"}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is True
        finally:
            _cleanup(path)

    def test_unlisted_domain_string_passes(self):
        fm = {**_REQUIRED_FM, "domain": "data-eng"}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is True
        finally:
            _cleanup(path)

    def test_non_string_complexity_fails_like_other_optional_string_fields(self):
        fm = {**_REQUIRED_FM, "complexity": 3}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is False
        finally:
            _cleanup(path)

    def test_kind_still_enum_enforced(self):
        fm = {**_REQUIRED_FM, "kind": "not-a-real-kind"}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is False
        finally:
            _cleanup(path)


class TestPurposeFieldSchema:
    """task-purpose-classification 1.1: purpose is an optional string field with
    no values enum, matching complexity/domain's existing coverage pattern."""

    def test_purpose_in_field_schema(self):
        assert "purpose" in FIELD_SCHEMA
        assert FIELD_SCHEMA["purpose"]["type"] is str
        assert FIELD_SCHEMA["purpose"]["required"] is False
        assert "values" not in FIELD_SCHEMA["purpose"]

    def test_purpose_set_passes_validation(self):
        fm = {**_REQUIRED_FM, "purpose": "scaffolding"}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is True
        finally:
            _cleanup(path)

    def test_purpose_round_trips_through_read_task_file(self):
        fm = {**_REQUIRED_FM, "purpose": "scaffolding"}
        path = _make_task(fm, _BODY)
        try:
            read_fm, error, _ = read_task_file(__import__("pathlib").Path(path))
            assert error is None
            assert read_fm["purpose"] == "scaffolding"
        finally:
            _cleanup(path)

    def test_absent_purpose_passes_validation(self):
        fm = {**_REQUIRED_FM}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is True
        finally:
            _cleanup(path)

    def test_absent_purpose_not_in_read_frontmatter(self):
        fm = {**_REQUIRED_FM}
        path = _make_task(fm, _BODY)
        try:
            read_fm, _, _ = read_task_file(__import__("pathlib").Path(path))
            assert "purpose" not in read_fm
        finally:
            _cleanup(path)

    def test_non_string_purpose_fails_like_other_optional_string_fields(self):
        fm = {**_REQUIRED_FM, "purpose": 3}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is False
        finally:
            _cleanup(path)


class TestTimeoutFieldSchema:
    """FR-8: timeout is an optional int in FIELD_SCHEMA; valid int passes, non-int string fails."""

    def test_timeout_in_field_schema(self):
        assert "timeout" in FIELD_SCHEMA
        assert FIELD_SCHEMA["timeout"]["type"] is int
        assert FIELD_SCHEMA["timeout"]["required"] is False

    def test_valid_int_timeout_passes(self):
        fm = {**_REQUIRED_FM, "timeout": 1800}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is True
        finally:
            _cleanup(path)

    def test_string_timeout_fails(self):
        fm = {**_REQUIRED_FM, "timeout": "abc"}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is False
        finally:
            _cleanup(path)

    def test_absent_timeout_passes(self):
        fm = {**_REQUIRED_FM}
        path = _make_task(fm, _BODY)
        try:
            assert validate_task(path) is True
        finally:
            _cleanup(path)


# --- TASK-CHG-001: widen is_task_file()/hooks.json pattern to TASK-CHG-NNN.md ---

class TestIsTaskFileRegex:
    """Direct unit coverage for is_task_file() — TASK-NNN.md and TASK-CHG-NNN.md shapes."""

    def test_task_chg_basename_is_true(self):
        assert is_task_file("TASK-CHG-001.md") is True

    def test_task_plain_basename_is_true(self):
        assert is_task_file("TASK-001.md") is True

    def test_readme_is_false(self):
        assert is_task_file("README.md") is False

    def test_non_md_python_file_is_false(self):
        assert is_task_file("some-file.py") is False

    def test_task_chg_full_path_is_true(self):
        assert is_task_file("/repo/docs/specs/changes/tasks/TASK-CHG-001.md") is True

    def test_task_plain_full_path_is_true(self):
        assert is_task_file("/repo/docs/specs/tasks/TASK-001.md") is True


class TestChangeSpecTaskFileValidation:
    """AC-005: a malformed TASK-CHG-*.md fixture must fail validate non-zero with stderr."""

    def test_malformed_task_chg_file_exits_nonzero(self, tmp_path):
        f = tmp_path / "TASK-CHG-999.md"
        f.write_text("---\ntitle: Broken change task\n---\n# No required fields\n")
        result = subprocess.run(
            CLI_ARGV + [ "validate", str(f)],
            capture_output=True, text=True
        )
        assert result.returncode != 0

    def test_malformed_task_chg_file_emits_validation_failed_to_stderr(self, tmp_path):
        f = tmp_path / "TASK-CHG-999.md"
        f.write_text("---\ntitle: Broken change task\n---\n# No required fields\n")
        result = subprocess.run(
            CLI_ARGV + [ "validate", str(f)],
            capture_output=True, text=True
        )
        assert "Validation failed" in result.stderr
        assert "Validation failed" not in result.stdout


# `TestHooksJsonPattern` (AC-004) is intentionally not ported: it asserted the
# `pattern` field of devkit's `hooks/hooks.json` (Claude Code PostToolUse
# wiring) matches `is_task_file()`'s regex. `hooks.json` is devkit-specific
# plugin config that stays behind as part of the thin-shim wrapper, not part
# of this package -- devkit's own copy of this test (once shimmed) still
# guards that file.
