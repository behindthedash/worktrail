"""Unit tests for loader.load_spec timeout projection (AC-3, AC-4, AC-10, FR-6)."""

import tempfile
import unittest
from pathlib import Path

from worktrail.taskformats.devkit import source as loader


def _write_task(d: Path, name: str, extra_fm: str = "") -> Path:
    """Write a minimal TASK-*.md with optional extra frontmatter lines."""
    p = d / f"{name}.md"
    p.write_text(
        f"---\nid: {name}\ntitle: Test task\nstatus: pending\ndependencies: []\n{extra_fm}---\n\n# body\n"
    )
    return p


class TestLoaderTimeoutProjection(unittest.TestCase):

    def _load(self, spec_dir: Path):
        _, tasks = loader.load_spec(str(spec_dir))
        return tasks

    def test_valid_timeout_coerced_to_int(self):
        """AC-3: timeout: 2700 in frontmatter → task['timeout'] == 2700 as int."""
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp)
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir()
            _write_task(tasks_dir, "TASK-001", "timeout: 2700\n")
            tasks = self._load(spec)
            self.assertEqual(len(tasks), 1)
            self.assertEqual(tasks[0]["timeout"], 2700)
            self.assertIsInstance(tasks[0]["timeout"], int)

    def test_missing_timeout_is_none(self):
        """AC-4: no timeout field in frontmatter → task['timeout'] is None."""
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp)
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir()
            _write_task(tasks_dir, "TASK-001")
            tasks = self._load(spec)
            self.assertEqual(len(tasks), 1)
            self.assertIsNone(tasks[0]["timeout"])

    def test_malformed_timeout_does_not_raise(self):
        """AC-10: timeout: abc → no exception, task['timeout'] is None."""
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp)
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir()
            _write_task(tasks_dir, "TASK-001", "timeout: abc\n")
            tasks = self._load(spec)
            self.assertEqual(len(tasks), 1)
            self.assertIsNone(tasks[0]["timeout"])

    def test_zero_timeout_normalized_to_none(self):
        """FR-6: timeout: 0 is non-positive → normalized to None so run_default fallback works."""
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp)
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir()
            _write_task(tasks_dir, "TASK-001", "timeout: 0\n")
            tasks = self._load(spec)
            self.assertEqual(len(tasks), 1)
            # Loader normalizes non-positive to None; `None or run_default` yields run_default.
            self.assertIsNone(tasks[0]["timeout"])
            run_default = 1800
            effective = tasks[0].get("timeout") or run_default
            self.assertEqual(effective, run_default)

    def test_negative_timeout_normalized_to_none(self):
        """FR-6: timeout: -300 is non-positive → normalized to None so run_default fallback works."""
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp)
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir()
            _write_task(tasks_dir, "TASK-001", "timeout: -300\n")
            tasks = self._load(spec)
            self.assertEqual(len(tasks), 1)
            # Loader normalizes non-positive to None; -300 is truthy so must be None for fallback.
            self.assertIsNone(tasks[0]["timeout"])
            run_default = 1800
            effective = tasks[0].get("timeout") or run_default
            self.assertEqual(effective, run_default)

    def test_mixed_tasks_malformed_does_not_abort(self):
        """AC-10 resilience: one malformed timeout doesn't block other tasks from loading."""
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp)
            tasks_dir = spec / "tasks"
            tasks_dir.mkdir()
            _write_task(tasks_dir, "TASK-001", "timeout: abc\n")
            _write_task(tasks_dir, "TASK-002", "timeout: 900\n")
            _write_task(tasks_dir, "TASK-003")
            tasks = self._load(spec)
            by_id = {t["id"]: t for t in tasks}
            self.assertIsNone(by_id["TASK-001"]["timeout"])
            self.assertEqual(by_id["TASK-002"]["timeout"], 900)
            self.assertIsInstance(by_id["TASK-002"]["timeout"], int)
            self.assertIsNone(by_id["TASK-003"]["timeout"])


if __name__ == "__main__":
    unittest.main()
