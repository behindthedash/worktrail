#!/usr/bin/env python3
"""Unit tests for the close-stale OpenSpec mechanical helper.

`openspec archive` itself is mocked via `subprocess.run` (no real OpenSpec
CLI dependency in tests), mirroring `tests/drain/test_drain.py`'s
`archive_openspec_change` mocking style.
"""
from __future__ import annotations

import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worktrail.router import close_stale_openspec as cso

TASKS_MD = """## 1. Setup

- [x] 1.1 Already done

## 2. Tests

- [ ] 2.1 Add coverage
- [ ] 3.1 Verify end to end
"""


def _write_change(wt: Path, change_id: str, tasks_md: str = TASKS_MD) -> Path:
    change_dir = wt / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    (change_dir / "tasks.md").write_text(tasks_md, encoding="utf-8")
    return change_dir


def _patch_openspec_archive(returncode: int, stdout: str = "ok", stderr: str = ""):
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["openspec", "archive"]:
            return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr=stderr)
        return real_run(cmd, **kwargs)

    return unittest.mock.patch.object(cso.subprocess, "run", side_effect=fake_run)


class TestMissingTasksFile(unittest.TestCase):
    def test_missing_change_dir_is_unchecked(self):
        with tempfile.TemporaryDirectory() as t:
            res = cso.flip_and_archive(Path(t), "does-not-exist")
            self.assertFalse(res["checked"])
            self.assertIsNotNone(res["error"])
            self.assertFalse(res["archived"])


class TestDefaultFlipsAllPending(unittest.TestCase):
    def test_flips_all_pending_and_archives(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            change_dir = _write_change(wt, "add-export")

            with _patch_openspec_archive(0):
                res = cso.flip_and_archive(wt, "add-export")

            self.assertTrue(res["checked"])
            self.assertEqual(sorted(res["flipped"]), ["2.1", "3.1"])
            self.assertEqual(res["already_checked"], [])
            self.assertTrue(res["archived"])
            self.assertIsNone(res["error"])

            text = (change_dir / "tasks.md").read_text()
            self.assertNotIn("[ ]", text)


class TestExplicitTaskIds(unittest.TestCase):
    def test_flips_only_requested_ids(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            change_dir = _write_change(wt, "add-export")

            with _patch_openspec_archive(0):
                res = cso.flip_and_archive(wt, "add-export", task_ids=["2.1"])

            self.assertEqual(res["flipped"], ["2.1"])
            self.assertTrue(res["archived"])

            text = (change_dir / "tasks.md").read_text()
            # 2.1 flipped, 3.1 left untouched
            self.assertIn("[x] 2.1", text)
            self.assertIn("[ ] 3.1", text)


class TestAlreadyCheckedIsNoOpButStillArchives(unittest.TestCase):
    def test_already_checked_task_id_archives_without_flip(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")

            with _patch_openspec_archive(0):
                res = cso.flip_and_archive(wt, "add-export", task_ids=["1.1"])

            self.assertEqual(res["flipped"], [])
            self.assertEqual(res["already_checked"], ["1.1"])
            self.assertTrue(res["archived"])


class TestUnknownTaskIdWithNoFallback(unittest.TestCase):
    def test_unknown_task_id_alone_refuses_to_archive(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            res = cso.flip_and_archive(wt, "add-export", task_ids=["9.9"])
            self.assertEqual(res["unknown_task_ids"], ["9.9"])
            self.assertFalse(res["archived"])
            self.assertIsNotNone(res["error"])


class TestArchiveFailureIsSurfaced(unittest.TestCase):
    def test_openspec_archive_nonzero_exit_is_reported(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")

            with _patch_openspec_archive(1, stdout="", stderr="boom"):
                res = cso.flip_and_archive(wt, "add-export")

            self.assertFalse(res["archived"])
            self.assertIn("boom", res["error"])


if __name__ == "__main__":
    unittest.main()
