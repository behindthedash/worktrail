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
from worktrail.router.land_pr import LandOutcome

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
            return subprocess.CompletedProcess(
                cmd, returncode, stdout=stdout, stderr=stderr
            )
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


class TestMainInvokesLandPrOnSuccess(unittest.TestCase):
    def test_main_calls_land_pr_after_successful_flip_and_archive(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "test-change")

            mock_outcome = LandOutcome(
                outcome="landed",
                pr_url="https://github.com/org/repo/pull/123",
                pr_number=123,
                final_status="completed_pr_open",
                merge_result="eligible for auto-merge",
            )

            with (
                _patch_openspec_archive(0),
                unittest.mock.patch(
                    "worktrail.router.close_stale_openspec.land_pr",
                    return_value=mock_outcome,
                ) as mock_land_pr,
            ):
                result = cso.main(
                    [
                        "--worktree",
                        str(wt),
                        "--change-id",
                        "test-change",
                        "--base",
                        "main",
                        "--run",
                        "/path/to/run",
                        "--json",
                    ]
                )

            # Verify land_pr was called once with correct parameters
            self.assertEqual(mock_land_pr.call_count, 1)
            call_args = mock_land_pr.call_args[0][0]  # First positional arg
            self.assertEqual(call_args.repo, str(wt))
            self.assertEqual(call_args.base_branch, "main")
            self.assertEqual(
                call_args.title, "chore(test-change): close stale bookkeeping"
            )
            self.assertEqual(call_args.route, "E")
            self.assertEqual(call_args.risk, "low")
            self.assertEqual(call_args.run, "/path/to/run")
            self.assertEqual(result, 0)  # landed exit code

    def test_main_does_not_call_land_pr_when_flip_and_archive_fails(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            # Don't create the change directory; this will fail flip_and_archive
            with unittest.mock.patch(
                "worktrail.router.close_stale_openspec.land_pr"
            ) as mock_land_pr:
                result = cso.main(
                    [
                        "--worktree",
                        str(wt),
                        "--change-id",
                        "nonexistent-change",
                        "--base",
                        "main",
                        "--json",
                    ]
                )

            # land_pr should not have been called
            self.assertEqual(mock_land_pr.call_count, 0)
            self.assertEqual(result, 1)  # error exit code

    def test_main_includes_landing_outcome_in_json_output(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "test-change")

            mock_outcome = LandOutcome(
                outcome="landed",
                pr_url="https://github.com/org/repo/pull/42",
                pr_number=42,
                final_status="completed_and_merged",
                merge_result="merged by automerge",
                run="/some/run/path",
            )

            with (
                _patch_openspec_archive(0),
                unittest.mock.patch(
                    "worktrail.router.close_stale_openspec.land_pr",
                    return_value=mock_outcome,
                ),
                unittest.mock.patch("builtins.print") as mock_print,
            ):
                cso.main(
                    [
                        "--worktree",
                        str(wt),
                        "--change-id",
                        "test-change",
                        "--base",
                        "main",
                        "--run",
                        "/path/to/run",
                        "--json",
                    ]
                )

            # Verify print was called with JSON containing the landing outcome
            self.assertEqual(mock_print.call_count, 1)
            output_json = mock_print.call_args[0][0]
            import json

            output_dict = json.loads(output_json)
            self.assertIn("landing", output_dict)
            self.assertEqual(output_dict["landing"]["outcome"], "landed")
            self.assertEqual(
                output_dict["landing"]["pr_url"], "https://github.com/org/repo/pull/42"
            )
            self.assertEqual(output_dict["landing"]["pr_number"], 42)

    def test_main_maps_landing_outcomes_to_exit_codes(self):
        test_cases = [
            ("landed", 0),
            ("refused", 2),
            ("code_defect", 3),
            ("review_threads_blocking", 3),
            ("ceiling", 4),
        ]

        for outcome_str, expected_exit_code in test_cases:
            with self.subTest(outcome=outcome_str), tempfile.TemporaryDirectory() as t:
                wt = Path(t)
                _write_change(wt, "test-change")

                mock_outcome = LandOutcome(outcome=outcome_str)

                with (
                    _patch_openspec_archive(0),
                    unittest.mock.patch(
                        "worktrail.router.close_stale_openspec.land_pr",
                        return_value=mock_outcome,
                    ),
                ):
                    result = cso.main(
                        [
                            "--worktree",
                            str(wt),
                            "--change-id",
                            "test-change",
                            "--base",
                            "main",
                            "--json",
                        ]
                    )

                self.assertEqual(result, expected_exit_code)


if __name__ == "__main__":
    unittest.main()
