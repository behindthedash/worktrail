"""Tests for the capability-spec Purpose drift guard."""

from __future__ import annotations

from pathlib import Path

from worktrail.router.check_spec_purpose import check_changed_specs

PLACEHOLDER = """# thing Specification

## Purpose
TBD - created by archiving change some-change. Update Purpose after archive.
## Requirements
### Requirement: Something
It SHALL do something.
"""

REAL = """# thing Specification

## Purpose
Keeps the widget aligned with the sprocket, so a drifted sprocket is caught at
build time instead of in production.
## Requirements
### Requirement: Something
It SHALL do something.
"""


def _write(repo: Path, relpath: str, text: str) -> None:
    path = repo / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_archive_placeholder_fails(tmp_path: Path) -> None:
    _write(tmp_path, "openspec/specs/thing/spec.md", PLACEHOLDER)
    failures = check_changed_specs(tmp_path, ["openspec/specs/thing/spec.md"])
    assert len(failures) == 1
    assert "thing" in failures[0]


def test_missing_purpose_heading_fails(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "openspec/specs/thing/spec.md",
        "# thing Specification\n\n## Requirements\n### Requirement: X\nIt SHALL x.\n",
    )
    failures = check_changed_specs(tmp_path, ["openspec/specs/thing/spec.md"])
    assert len(failures) == 1
    assert "no `## Purpose` section" in failures[0]


def test_blank_purpose_body_fails(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "openspec/specs/thing/spec.md",
        "# thing Specification\n\n## Purpose\n\n## Requirements\n### Requirement: X\nIt SHALL x.\n",
    )
    failures = check_changed_specs(tmp_path, ["openspec/specs/thing/spec.md"])
    assert len(failures) == 1
    assert "empty" in failures[0]


def test_real_prose_passes(tmp_path: Path) -> None:
    _write(tmp_path, "openspec/specs/thing/spec.md", REAL)
    assert check_changed_specs(tmp_path, ["openspec/specs/thing/spec.md"]) == []


def test_tbd_not_at_start_passes(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "openspec/specs/thing/spec.md",
        "# thing Specification\n\n## Purpose\nResolves the TBD entries left by an archive.\n"
        "## Requirements\n### Requirement: X\nIt SHALL x.\n",
    )
    assert check_changed_specs(tmp_path, ["openspec/specs/thing/spec.md"]) == []


def test_unchanged_placeholder_spec_is_not_checked(tmp_path: Path) -> None:
    _write(tmp_path, "openspec/specs/thing/spec.md", PLACEHOLDER)
    _write(tmp_path, "openspec/specs/other/spec.md", REAL)
    assert check_changed_specs(tmp_path, ["openspec/specs/other/spec.md"]) == []


def test_change_delta_spec_path_is_ignored(tmp_path: Path) -> None:
    _write(tmp_path, "openspec/changes/c/specs/thing/spec.md", PLACEHOLDER)
    assert (
        check_changed_specs(tmp_path, ["openspec/changes/c/specs/thing/spec.md"]) == []
    )


def test_deleted_path_is_skipped(tmp_path: Path) -> None:
    assert check_changed_specs(tmp_path, ["openspec/specs/gone/spec.md"]) == []
