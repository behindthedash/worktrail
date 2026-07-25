#!/usr/bin/env python3
"""Unit tests for stale spec detection (spec_creation_date and is_stale_spec).

Tests the functions added in TASK-004 for identifying specs with no completed
tasks and older than the stale threshold (7 days). Uses tempfile and unittest.mock
to isolate from filesystem and git.

Run with: python3 test_stale_spec.py
"""

from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router import dashboard


def _write_spec(tmp: Path, date_str: str, bold: bool = True) -> Path:
    """Write a spec.md with a date header to tmp directory.

    Args:
        tmp: Temporary directory path
        date_str: Date string in YYYY-MM-DD format
        bold: If True, use **Date**: format; else use Date: format
    """
    tmp.mkdir(parents=True, exist_ok=True)
    if bold:
        spec_text = f"# Feature Specification\n\n**Date**: {date_str}\n\n## Summary\nTest spec.\n"
    else:
        spec_text = f"# Feature Specification\n\nDate: {date_str}\n\n## Summary\nTest spec.\n"
    (tmp / "spec.md").write_text(spec_text)
    return tmp


def _make_spec_dir(tmp: Path, total: int, completed: int) -> Path:
    """Create a spec directory with task files simulating _count_tasks results.

    Args:
        tmp: Temporary directory path
        total: Total number of tasks to create
        completed: Number of completed tasks
    """
    tmp.mkdir(parents=True, exist_ok=True)
    tasks_dir = tmp / "tasks"
    tasks_dir.mkdir(exist_ok=True)

    for i in range(1, total + 1):
        status = "completed" if i <= completed else "pending"
        task_id = f"TASK-{i:03d}"
        (tasks_dir / f"{task_id}.md").write_text(
            f"---\nid: {task_id}\nstatus: {status}\nkind: impl\n---\n# {task_id}\n"
        )

    return tmp


class TestSpecCreationDate(unittest.TestCase):
    """Tests for spec_creation_date function."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_bold_date_format_parsed(self):
        """Test **Date**: YYYY-MM-DD format is parsed correctly."""
        spec_dir = _write_spec(self.root / "001-x", "2026-06-08", bold=True)
        result = dashboard.spec_creation_date(spec_dir, self.root)
        self.assertEqual(result, datetime.date(2026, 6, 8))

    def test_bare_date_format_parsed(self):
        """Test Date: YYYY-MM-DD format is parsed correctly."""
        spec_dir = _write_spec(self.root / "002-x", "2026-05-15", bold=False)
        result = dashboard.spec_creation_date(spec_dir, self.root)
        self.assertEqual(result, datetime.date(2026, 5, 15))

    def test_no_date_no_spec_file_returns_none(self):
        """Test None is returned when no date is found and no spec file exists."""
        spec_dir = self.root / "003-x"
        spec_dir.mkdir(parents=True, exist_ok=True)
        result = dashboard.spec_creation_date(spec_dir, self.root)
        self.assertIsNone(result)

    def test_git_fallback_invoked_and_parsed(self):
        """Test git fallback is called and date is parsed from ISO format."""
        spec_dir = _write_spec(self.root / "004-x", "", bold=True)
        # Remove the date to trigger git fallback
        spec_text = (spec_dir / "spec.md").read_text()
        spec_text = spec_text.replace("**Date**: ", "")
        (spec_dir / "spec.md").write_text(spec_text)

        git_output = "2026-06-10 15:30:45 +0000\n"
        with patch("worktrail.router.dashboard.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = git_output
            result = dashboard.spec_creation_date(spec_dir, self.root)

        self.assertEqual(result, datetime.date(2026, 6, 10))
        mock_run.assert_called_once()

    def test_git_fallback_empty_returns_none(self):
        """Test None is returned when git fallback returns empty output."""
        spec_dir = _write_spec(self.root / "005-x", "", bold=True)
        # Remove the date to trigger git fallback
        spec_text = (spec_dir / "spec.md").read_text()
        spec_text = spec_text.replace("**Date**: ", "")
        (spec_dir / "spec.md").write_text(spec_text)

        with patch("worktrail.router.dashboard.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            result = dashboard.spec_creation_date(spec_dir, self.root)

        self.assertIsNone(result)


class TestIsStaleSpec(unittest.TestCase):
    """Tests for is_stale_spec function."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_ac6_zero_done_ten_days_old_is_stale(self):
        """AC-6: 0 done, 10-day-old spec → is_stale_spec returns True."""
        spec_dir = _make_spec_dir(self.root / "001-ac6", total=3, completed=0)
        _write_spec(spec_dir, "2026-06-05", bold=True)

        with patch("worktrail.router.dashboard.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2026, 6, 15)
            mock_datetime.datetime = datetime.datetime
            result = dashboard.is_stale_spec(spec_dir, self.root)

        self.assertTrue(result)

    def test_ac7_zero_done_two_days_old_not_stale(self):
        """AC-7: 0 done, 2-day-old spec → is_stale_spec returns False."""
        spec_dir = _make_spec_dir(self.root / "002-ac7", total=3, completed=0)
        _write_spec(spec_dir, "2026-06-13", bold=True)

        with patch("worktrail.router.dashboard.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2026, 6, 15)
            mock_datetime.datetime = datetime.datetime
            result = dashboard.is_stale_spec(spec_dir, self.root)

        self.assertFalse(result)

    def test_completed_tasks_not_stale_regardless_of_age(self):
        """Test: completed > 0 → is_stale_spec returns False regardless of age."""
        spec_dir = _make_spec_dir(self.root / "003-completed", total=5, completed=1)
        _write_spec(spec_dir, "2026-06-05", bold=True)

        with patch("worktrail.router.dashboard.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2026, 6, 15)
            mock_datetime.datetime = datetime.datetime
            result = dashboard.is_stale_spec(spec_dir, self.root)

        self.assertFalse(result)

    def test_no_date_returns_false(self):
        """Test: spec_creation_date returns None → is_stale_spec returns False."""
        spec_dir = _make_spec_dir(self.root / "004-no-date", total=2, completed=0)
        # Create spec dir but don't write a date
        _write_spec(spec_dir, "", bold=True)
        spec_text = (spec_dir / "spec.md").read_text()
        spec_text = spec_text.replace("**Date**: ", "")
        (spec_dir / "spec.md").write_text(spec_text)

        with patch("worktrail.router.dashboard.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            mock_run.return_value.stdout = ""
            result = dashboard.is_stale_spec(spec_dir, self.root)

        self.assertFalse(result)

    def test_no_tasks_returns_false(self):
        """Test: total=0 (no tasks) → is_stale_spec returns False."""
        spec_dir = self.root / "005-no-tasks"
        spec_dir.mkdir(parents=True, exist_ok=True)
        _write_spec(spec_dir, "2026-06-05", bold=True)

        with patch("worktrail.router.dashboard.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2026, 6, 15)
            mock_datetime.datetime = datetime.datetime
            result = dashboard.is_stale_spec(spec_dir, self.root)

        self.assertFalse(result)

    def test_boundary_exactly_seven_days_old_not_stale(self):
        """Test: Spec exactly 7 days old (threshold) is not stale."""
        spec_dir = _make_spec_dir(self.root / "006-boundary", total=2, completed=0)
        _write_spec(spec_dir, "2026-06-08", bold=True)

        with patch("worktrail.router.dashboard.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2026, 6, 15)
            mock_datetime.datetime = datetime.datetime
            result = dashboard.is_stale_spec(spec_dir, self.root)

        # 7 days should NOT be stale (> 7 is the threshold)
        self.assertFalse(result)

    def test_boundary_eight_days_old_is_stale(self):
        """Test: Spec 8 days old crosses the stale threshold."""
        spec_dir = _make_spec_dir(self.root / "007-boundary", total=2, completed=0)
        _write_spec(spec_dir, "2026-06-07", bold=True)

        with patch("worktrail.router.dashboard.datetime") as mock_datetime:
            mock_datetime.date.today.return_value = datetime.date(2026, 6, 15)
            mock_datetime.datetime = datetime.datetime
            result = dashboard.is_stale_spec(spec_dir, self.root)

        # 8 days should be stale (> 7)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
