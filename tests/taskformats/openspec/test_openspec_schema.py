"""Tests for `worktrail.taskformats.openspec.schema`'s `files:` declaration parsing."""

from __future__ import annotations

import textwrap
from pathlib import Path

from worktrail.taskformats.openspec.schema import parse_tasks_md, set_task_checked


def test_files_declaration_parsed_into_task_files():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files: src/widget.py, tests/test_widget.py
        """
    )
    parsed = parse_tasks_md(text)
    task = parsed.by_id("1.1")
    assert task.files == ["src/widget.py", "tests/test_widget.py"]
    assert parsed.warnings == []


def test_task_without_continuation_line_has_empty_files():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Create export module structure
        - [ ] 1.2 Add CSV encoder dependency
        """
    )
    parsed = parse_tasks_md(text)
    assert parsed.by_id("1.1").files == []
    assert parsed.by_id("1.2").files == []
    assert parsed.warnings == []


def test_files_declaration_comma_separated():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files: src/widget.py,tests/test_widget.py,src/other.py
        """
    )
    parsed = parse_tasks_md(text)
    assert parsed.by_id("1.1").files == [
        "src/widget.py",
        "tests/test_widget.py",
        "src/other.py",
    ]


def test_files_declaration_space_separated():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files: src/widget.py tests/test_widget.py src/other.py
        """
    )
    parsed = parse_tasks_md(text)
    assert parsed.by_id("1.1").files == [
        "src/widget.py",
        "tests/test_widget.py",
        "src/other.py",
    ]


def test_files_declaration_mixed_separators():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files: src/widget.py, tests/test_widget.py src/other.py,  src/third.py
        """
    )
    parsed = parse_tasks_md(text)
    assert parsed.by_id("1.1").files == [
        "src/widget.py",
        "tests/test_widget.py",
        "src/other.py",
        "src/third.py",
    ]


def test_files_declaration_backticked_tokens():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files: `src/widget.py`, `tests/test_widget.py`
        """
    )
    parsed = parse_tasks_md(text)
    assert parsed.by_id("1.1").files == ["src/widget.py", "tests/test_widget.py"]


def test_files_declaration_with_no_paths_warns_and_leaves_files_empty():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files:
        """
    )
    parsed = parse_tasks_md(text)
    task = parsed.by_id("1.1")
    assert task.files == []
    assert len(parsed.warnings) == 1
    assert "1.1" in parsed.warnings[0]
    assert "names no paths" in parsed.warnings[0]


def test_duplicate_files_declaration_warns_and_uses_first():
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files: src/widget.py
          files: src/other.py
        """
    )
    parsed = parse_tasks_md(text)
    task = parsed.by_id("1.1")
    assert task.files == ["src/widget.py"]
    assert len(parsed.warnings) == 1
    assert "1.1" in parsed.warnings[0]
    assert "more than one" in parsed.warnings[0]


def test_set_task_checked_on_declared_task_changes_only_checkbox_byte(tmp_path: Path):
    text = textwrap.dedent(
        """\
        ## 1. Setup

        - [ ] 1.1 Add the widget
          files: src/widget.py, tests/test_widget.py
        - [ ] 1.2 Add another widget
        """
    )
    tasks_md = tmp_path / "tasks.md"
    tasks_md.write_text(text)

    changed = set_task_checked(tasks_md, "1.1", checked=True)
    assert changed is True

    new_text = tasks_md.read_text()
    expected = text.replace(
        "- [ ] 1.1 Add the widget", "- [x] 1.1 Add the widget", 1
    )
    assert new_text == expected

    parsed = parse_tasks_md(new_text)
    task = parsed.by_id("1.1")
    assert task.files == ["src/widget.py", "tests/test_widget.py"]
    assert task.status == "completed"
