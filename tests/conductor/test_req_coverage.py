"""Tests for the requirement-coverage gate (`conductor/req_coverage.py`).

Design `openspec-req-ac-coverage-gate/design.md` D1/D3/D4: coverage is a
case-insensitive name-presence match of a delta spec's declared requirement
names against `tasks.md` text, ratcheted non-retroactively against the
already-merged main spec (D3), and a no-op for devkit-format spec
directories (D4).
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from worktrail.conductor import req_coverage


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")


def _change_dir(tmp_path: Path, change_id: str = "add-widget") -> Path:
    return tmp_path / "repo" / "openspec" / "changes" / change_id


def test_added_requirement_with_no_tasks_md_reference_is_uncovered(tmp_path: Path):
    change = _change_dir(tmp_path)
    _write(
        change / "tasks.md",
        """\
        ## 1. Setup

        - [ ] 1.1 Do something unrelated
        """,
    )
    _write(
        change / "specs" / "widget" / "spec.md",
        """\
        ## ADDED Requirements

        ### Requirement: Widget Rendering
        The widget SHALL render.
        """,
    )

    uncovered = req_coverage.find_uncovered_requirements(change, change.parents[2])

    assert uncovered == ["Widget Rendering"]


def test_added_requirement_referenced_by_name_in_tasks_md_is_covered(tmp_path: Path):
    change = _change_dir(tmp_path)
    _write(
        change / "tasks.md",
        """\
        ## 1. Setup

        - [ ] 1.1 Implement Widget Rendering per the spec
        """,
    )
    _write(
        change / "specs" / "widget" / "spec.md",
        """\
        ## ADDED Requirements

        ### Requirement: Widget Rendering
        The widget SHALL render.
        """,
    )

    uncovered = req_coverage.find_uncovered_requirements(change, change.parents[2])

    assert uncovered == []


def test_requirement_title_wrapped_across_line_break_is_still_covered(
    tmp_path: Path,
):
    """Regression: a requirement title word-wrapped across a newline in
    tasks.md must still count as covered. Before the fix the containment check
    ran against raw text, so a natural editor reflow (or a long title) split
    the title mid-phrase and tripped a false 'no task coverage' CI failure;
    observed live on PR #513 -- had to reword the sentence onto one line.
    Whitespace (newlines/indentation) is incidental, the wording is not.
    """
    change = _change_dir(tmp_path)
    _write(
        change / "tasks.md",
        """\
        ## 1. Setup

        - [ ] 1.1 Implement An unresolvable or mismatched pin fails the
            run instead of recompiling per the spec
        """,
    )
    _write(
        change / "specs" / "widget" / "spec.md",
        """\
        ## ADDED Requirements

        ### Requirement: An unresolvable or mismatched pin fails the run instead of recompiling
        The run SHALL fail on an unresolvable pin.
        """,
    )

    uncovered = req_coverage.find_uncovered_requirements(change, change.parents[2])

    assert uncovered == []


def test_requirement_declared_only_under_removed_requirements_is_never_enforced(
    tmp_path: Path,
):
    change = _change_dir(tmp_path)
    _write(
        change / "tasks.md",
        """\
        ## 1. Setup

        - [ ] 1.1 Do something unrelated
        """,
    )
    _write(
        change / "specs" / "widget" / "spec.md",
        """\
        ## REMOVED Requirements

        ### Requirement: Widget Rendering
        The widget SHALL render.
        """,
    )

    uncovered = req_coverage.find_uncovered_requirements(change, change.parents[2])

    assert uncovered == []


def test_brand_new_capability_path_every_requirement_newly_declared_and_enforced(
    tmp_path: Path,
):
    change = _change_dir(tmp_path)
    _write(
        change / "tasks.md",
        """\
        ## 1. Setup

        - [ ] 1.1 Do something unrelated
        """,
    )
    _write(
        change / "specs" / "brand-new-capability" / "spec.md",
        """\
        ## ADDED Requirements

        ### Requirement: Brand New Behavior
        It SHALL behave.
        """,
    )
    repo = change.parents[2]
    assert not (repo / "openspec" / "specs" / "brand-new-capability").exists()

    uncovered = req_coverage.find_uncovered_requirements(change, repo)

    assert uncovered == ["Brand New Behavior"]


def test_modified_requirement_already_in_main_spec_is_not_newly_declared(
    tmp_path: Path,
):
    change = _change_dir(tmp_path)
    repo = change.parents[2]
    _write(
        change / "tasks.md",
        """\
        ## 1. Setup

        - [ ] 1.1 Do something unrelated
        """,
    )
    _write(
        change / "specs" / "widget" / "spec.md",
        """\
        ## MODIFIED Requirements

        ### Requirement: Widget Rendering
        The widget SHALL render faster.
        """,
    )
    _write(
        repo / "openspec" / "specs" / "widget" / "spec.md",
        """\
        ## Requirements

        ### Requirement: Widget Rendering
        The widget SHALL render.
        """,
    )

    uncovered = req_coverage.find_uncovered_requirements(change, repo)

    assert uncovered == []


def test_change_with_no_tasks_md_every_declared_requirement_uncovered(tmp_path: Path):
    change = _change_dir(tmp_path)
    _write(
        change / "specs" / "widget" / "spec.md",
        """\
        ## ADDED Requirements

        ### Requirement: Widget Rendering
        The widget SHALL render.
        """,
    )
    assert not (change / "tasks.md").exists()

    uncovered = req_coverage.find_uncovered_requirements(change, change.parents[2])

    assert uncovered == ["Widget Rendering"]


def test_devkit_format_spec_directory_is_a_no_op(tmp_path: Path):
    spec_dir = tmp_path / "repo" / "docs" / "specs" / "001-widget"
    _write(
        spec_dir / "spec.md", "## Requirements\n\n### Requirement: Widget Rendering\n"
    )
    (spec_dir / "tasks").mkdir(parents=True)

    uncovered = req_coverage.find_uncovered_requirements(spec_dir, tmp_path / "repo")

    assert uncovered == []
