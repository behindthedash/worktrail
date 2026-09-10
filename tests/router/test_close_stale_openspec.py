#!/usr/bin/env python3
"""Unit tests for the close-stale OpenSpec mechanical helper.

`openspec archive` and `openspec validate` are mocked via `subprocess.run`
(no real OpenSpec CLI dependency in tests), mirroring
`tests/drain/test_drain.py`'s `archive_openspec_change` mocking style.
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


ALL_CHECKED_TASKS_MD = """## 1. Setup

- [x] 1.1 Already done

## 2. Tests

- [x] 2.1 Also already done
"""


def _write_change(wt: Path, change_id: str, tasks_md: str = TASKS_MD) -> Path:
    change_dir = wt / "openspec" / "changes" / change_id
    change_dir.mkdir(parents=True)
    (change_dir / "tasks.md").write_text(tasks_md, encoding="utf-8")
    return change_dir


def _patch_openspec(
    archive_rc: int = 0,
    archive_stdout: str = "ok",
    archive_stderr: str = "",
    validate_rc: int = 0,
    validate_stdout: str = "valid",
    validate_stderr: str = "",
    calls: list | None = None,
):
    """Mock `openspec validate` and `openspec archive`; record their argv in
    `calls` when given. Every other subprocess (e.g. `git log` from the
    dashboard drift helper) runs for real."""
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if cmd[:2] == ["openspec", "validate"]:
            if calls is not None:
                calls.append(cmd)
            return subprocess.CompletedProcess(
                cmd, validate_rc, stdout=validate_stdout, stderr=validate_stderr
            )
        if cmd[:2] == ["openspec", "archive"]:
            if calls is not None:
                calls.append(cmd)
            return subprocess.CompletedProcess(
                cmd, archive_rc, stdout=archive_stdout, stderr=archive_stderr
            )
        return real_run(cmd, **kwargs)

    return unittest.mock.patch.object(cso.subprocess, "run", side_effect=fake_run)


def _patch_openspec_archive(returncode: int, stdout: str = "ok", stderr: str = ""):
    return _patch_openspec(
        archive_rc=returncode, archive_stdout=stdout, archive_stderr=stderr
    )


def _write_delta(wt: Path, change_id: str, capability: str, text: str) -> None:
    path = wt / "openspec" / "changes" / change_id / "specs" / capability / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_canonical(wt: Path, capability: str, text: str) -> None:
    path = wt / "openspec" / "specs" / capability / "spec.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


CANONICAL_SPEC = """# auth

## Requirements

### Requirement: Login
Users SHALL log in.

#### Scenario: ok
- **WHEN** x
- **THEN** y
"""

MODIFIED_LOGIN_DELTA = """## MODIFIED Requirements

### Requirement: Login
Users SHALL log in with MFA.

#### Scenario: ok
- **WHEN** x
- **THEN** y
"""


class TestDeltaPrecheck(unittest.TestCase):
    """design.md D1-D3: the pre-check runs before any flip, always populates
    `precheck`, refuses with a class-naming error, and only the drift class
    is overridable."""

    def _tasks_text(self, wt: Path, change_id: str = "add-export") -> str:
        return (wt / "openspec" / "changes" / change_id / "tasks.md").read_text()

    def test_validate_failure_leaves_tasks_unchanged_and_skips_archive(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            calls: list = []
            with _patch_openspec(
                validate_rc=1,
                validate_stdout="",
                validate_stderr="bad delta",
                calls=calls,
            ):
                res = cso.flip_and_archive(wt, "add-export")

            self.assertTrue(res["checked"])
            self.assertFalse(res["precheck"]["validate_ok"])
            self.assertEqual(res["precheck"]["validate_output"], "bad delta")
            self.assertIn("openspec validate", res["error"])
            self.assertIn("bad delta", res["error"])
            self.assertEqual(res["flipped"], [])
            self.assertFalse(res["archived"])
            self.assertEqual(self._tasks_text(wt), TASKS_MD)
            self.assertEqual([c[1] for c in calls], ["validate"])
            self.assertEqual(
                calls[0], ["openspec", "validate", "add-export", "--strict"]
            )

    def test_modified_requirement_missing_from_canonical_refuses(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_canonical(wt, "auth", CANONICAL_SPEC)
            _write_delta(
                wt,
                "add-export",
                "auth",
                "## MODIFIED Requirements\n\n### Requirement: Logout\nx\n",
            )
            calls: list = []
            with _patch_openspec(calls=calls):
                res = cso.flip_and_archive(wt, "add-export")

            self.assertTrue(res["precheck"]["validate_ok"])
            self.assertEqual(
                res["precheck"]["missing_canonical"],
                [{"capability": "auth", "requirement": "Logout", "kind": "MODIFIED"}],
            )
            self.assertIn("missing canonical target", res["error"])
            self.assertIn("auth/Logout", res["error"])
            self.assertEqual(res["flipped"], [])
            self.assertFalse(res["archived"])
            self.assertEqual(self._tasks_text(wt), TASKS_MD)
            self.assertNotIn("archive", [c[1] for c in calls])

    def test_removed_and_renamed_from_missing_targets_refuse(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_canonical(wt, "auth", CANONICAL_SPEC)
            _write_delta(
                wt,
                "add-export",
                "auth",
                "## REMOVED Requirements\n\n### Requirement: Gone\n"
                "**Reason**: x\n\n"
                "## RENAMED Requirements\n\n- FROM: `### Requirement: Old`\n"
                "- TO: `### Requirement: New`\n",
            )
            with _patch_openspec():
                res = cso.flip_and_archive(wt, "add-export")

            self.assertEqual(
                res["precheck"]["missing_canonical"],
                [
                    {"capability": "auth", "requirement": "Gone", "kind": "REMOVED"},
                    {"capability": "auth", "requirement": "Old", "kind": "RENAMED"},
                ],
            )
            self.assertIn("REMOVED", res["error"])
            self.assertFalse(res["archived"])
            self.assertEqual(self._tasks_text(wt), TASKS_MD)

    def test_missing_canonical_file_with_modified_refuses(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_delta(wt, "add-export", "auth", MODIFIED_LOGIN_DELTA)
            with _patch_openspec():
                res = cso.flip_and_archive(wt, "add-export")
            self.assertEqual(len(res["precheck"]["missing_canonical"]), 1)
            self.assertFalse(res["archived"])

    def test_added_only_under_new_capability_passes(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_delta(
                wt,
                "add-export",
                "brand-new",
                "## ADDED Requirements\n\n### Requirement: Fresh\nx\n",
            )
            with _patch_openspec():
                res = cso.flip_and_archive(wt, "add-export")

            self.assertIsNone(res["error"])
            self.assertEqual(res["precheck"]["missing_canonical"], [])
            self.assertTrue(res["archived"])

    def test_drift_refuses_without_flag(self):
        drift = [
            {"capability": "auth", "requirement": "Login", "archived_change_id": "sib"}
        ]
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_canonical(wt, "auth", CANONICAL_SPEC)
            _write_delta(wt, "add-export", "auth", MODIFIED_LOGIN_DELTA)
            with (
                _patch_openspec(),
                unittest.mock.patch.object(
                    cso.dashboard, "_openspec_delta_drift", return_value=drift
                ),
            ):
                res = cso.flip_and_archive(wt, "add-export")

            self.assertEqual(res["precheck"]["delta_drift"], drift)
            self.assertFalse(res["precheck"]["drift_allowed"])
            self.assertIn("delta drift", res["error"])
            self.assertIn("auth/Login", res["error"])
            self.assertIn("sib", res["error"])
            self.assertEqual(res["flipped"], [])
            self.assertFalse(res["archived"])
            self.assertEqual(self._tasks_text(wt), TASKS_MD)

    def test_drift_proceeds_when_allowed(self):
        drift = [
            {"capability": "auth", "requirement": "Login", "archived_change_id": "sib"}
        ]
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_canonical(wt, "auth", CANONICAL_SPEC)
            _write_delta(wt, "add-export", "auth", MODIFIED_LOGIN_DELTA)
            with (
                _patch_openspec(),
                unittest.mock.patch.object(
                    cso.dashboard, "_openspec_delta_drift", return_value=drift
                ),
            ):
                res = cso.flip_and_archive(wt, "add-export", allow_delta_drift=True)

            self.assertIsNone(res["error"])
            self.assertEqual(res["precheck"]["delta_drift"], drift)
            self.assertTrue(res["precheck"]["drift_allowed"])
            self.assertEqual(sorted(res["flipped"]), ["2.1", "3.1"])
            self.assertTrue(res["archived"])

    def test_flag_does_not_bypass_validate_failure(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            with _patch_openspec(validate_rc=1, validate_stderr="nope"):
                res = cso.flip_and_archive(wt, "add-export", allow_delta_drift=True)
            self.assertIn("openspec validate", res["error"])
            self.assertTrue(res["precheck"]["drift_allowed"])
            self.assertFalse(res["archived"])
            self.assertEqual(self._tasks_text(wt), TASKS_MD)

    def test_flag_does_not_bypass_missing_canonical(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_delta(wt, "add-export", "auth", MODIFIED_LOGIN_DELTA)
            with _patch_openspec():
                res = cso.flip_and_archive(wt, "add-export", allow_delta_drift=True)
            self.assertIn("missing canonical target", res["error"])
            self.assertFalse(res["archived"])
            self.assertEqual(self._tasks_text(wt), TASKS_MD)

    def test_pass_through_flips_and_archives_with_validate_ok(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export")
            _write_canonical(wt, "auth", CANONICAL_SPEC)
            _write_delta(wt, "add-export", "auth", MODIFIED_LOGIN_DELTA)
            calls: list = []
            with _patch_openspec(calls=calls):
                res = cso.flip_and_archive(wt, "add-export")

            self.assertIsNone(res["error"])
            self.assertEqual(
                res["precheck"],
                {
                    "validate_ok": True,
                    "validate_output": "valid",
                    "missing_canonical": [],
                    "delta_drift": [],
                    "drift_allowed": False,
                },
            )
            self.assertEqual(sorted(res["flipped"]), ["2.1", "3.1"])
            self.assertTrue(res["archived"])
            self.assertEqual([c[1] for c in calls], ["validate", "archive"])
            self.assertNotIn("[ ]", self._tasks_text(wt))

    def test_missing_tasks_md_leaves_precheck_none(self):
        with tempfile.TemporaryDirectory() as t:
            res = cso.flip_and_archive(Path(t), "does-not-exist")
            self.assertIsNone(res["precheck"])

    def test_cli_flag_is_forwarded(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "test-change")
            with (
                _patch_openspec(),
                unittest.mock.patch.object(
                    cso, "flip_and_archive", wraps=cso.flip_and_archive
                ) as spy,
                unittest.mock.patch(
                    "worktrail.router.close_stale_openspec.land_pr",
                    return_value=LandOutcome(outcome="landed"),
                ),
            ):
                cso.main(
                    [
                        "--worktree",
                        str(wt),
                        "--change-id",
                        "test-change",
                        "--allow-delta-drift",
                        "--base",
                        "main",
                        "--run",
                        "/r",
                        "--json",
                    ]
                )
            self.assertTrue(spy.call_args.kwargs["allow_delta_drift"])


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
            # The default targets every id, so a task that was already `[x]`
            # is reported in `already_checked` rather than being skipped.
            self.assertEqual(res["already_checked"], ["1.1"])
            self.assertTrue(res["archived"])
            self.assertIsNone(res["error"])

            text = (change_dir / "tasks.md").read_text()
            self.assertNotIn("[ ]", text)


class TestDefaultOnFullyCheckedChange(unittest.TestCase):
    """A change whose tasks.md is already 100% `[x]` -- pure bookkeeping drift,
    the exact case this module exists for -- must still archive on the default
    (no `task_ids`) path, landing every id in `already_checked`.
    """

    def test_zero_pending_tasks_still_archives(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            _write_change(wt, "add-export", tasks_md=ALL_CHECKED_TASKS_MD)

            with _patch_openspec_archive(0):
                res = cso.flip_and_archive(wt, "add-export")

            self.assertTrue(res["checked"])
            self.assertEqual(res["flipped"], [])
            self.assertEqual(sorted(res["already_checked"]), ["1.1", "2.1"])
            self.assertTrue(res["archived"])
            self.assertIsNone(res["error"])


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
            with _patch_openspec():
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
                        "--run",
                        "/path/to/run",
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
                            "--run",
                            "/path/to/run",
                            "--json",
                        ]
                    )

                self.assertEqual(result, expected_exit_code)


if __name__ == "__main__":
    unittest.main()
