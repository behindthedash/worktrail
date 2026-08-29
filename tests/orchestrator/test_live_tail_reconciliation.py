#!/usr/bin/env python3
"""Integration coverage: `_pipeline_scheduler`'s tail-dispatch block must run
`integrate.reconcile_unreconciled_tail_evidence` over
`integrate.detect_unreconciled_evidence`'s findings before calling
`integrate._record_unreconciled_tail_evidence`, and must skip reconciliation
entirely (leaving only the existing clear-when-empty journal write) when
there are no findings.

Companion to test_pipeline.py (`_pipeline_scheduler`, injectable
_spawn/_integrate_one/_make_verifier seams) -- reuses its fixtures rather
than re-deriving repo/fan-out setup.

Run: python3 test_live_tail_reconciliation.py
"""
from __future__ import annotations

import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from worktrail.orchestrator import integrate  # noqa: E402

from test_pipeline import (  # noqa: E402
    FakeSpawn as PipelineFakeSpawn,
    FakeVerifier,
    _init_repo,
    _make_integrate_one,
    _run as _run_pipeline_scheduler,
)


def _finding(task_id: str = "TASK-999") -> dict:
    return {
        "task": task_id,
        "head_sha": "deadbeef",
        "worktree": "/tmp/fake-wt",
        "reason": "commit never merged onto base",
    }


class PipelineSchedulerReconciliationTest(unittest.TestCase):
    """`_pipeline_scheduler`'s tail block (live.py, after fan-out completes)."""

    def test_reconciliation_invoked_before_recording_when_findings_present(self):
        call_order = []

        def fake_reconcile(findings, *_args, **_kwargs):
            call_order.append("reconcile")
            return findings

        def fake_record(*_args, **_kwargs):
            call_order.append("record")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            with unittest.mock.patch.object(
                integrate, "detect_unreconciled_evidence", return_value=[_finding()]
            ), unittest.mock.patch.object(
                integrate, "reconcile_unreconciled_tail_evidence", side_effect=fake_reconcile
            ) as reconcile_mock, unittest.mock.patch.object(
                integrate, "_record_unreconciled_tail_evidence", side_effect=fake_record
            ) as record_mock:
                _run_pipeline_scheduler(
                    repo, tmp, PipelineFakeSpawn(), integrate_one, FakeVerifier()
                )

            reconcile_mock.assert_called_once()
            self.assertEqual(reconcile_mock.call_args.args[0], [_finding()])
            record_mock.assert_called_once()
            self.assertEqual(
                call_order, ["reconcile", "record"],
                "reconciliation must run before the finding is recorded to the journal",
            )

    def test_empty_findings_is_noop(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, _ = _make_integrate_one()

            with unittest.mock.patch.object(
                integrate, "detect_unreconciled_evidence", return_value=[]
            ), unittest.mock.patch.object(
                integrate, "reconcile_unreconciled_tail_evidence"
            ) as reconcile_mock, unittest.mock.patch.object(
                integrate, "_record_unreconciled_tail_evidence"
            ) as record_mock:
                _run_pipeline_scheduler(
                    repo, tmp, PipelineFakeSpawn(), integrate_one, FakeVerifier()
                )

            reconcile_mock.assert_not_called()
            record_mock.assert_called_once()
            self.assertFalse(
                record_mock.call_args.args[1],
                "no findings must still clear any stale journal entry, "
                f"got {record_mock.call_args.args[1]!r}",
            )


class ReconcileTailEvidenceVerifyOneTest(unittest.TestCase):
    """`reconcile_unreconciled_tail_evidence` (integrate.py) itself -- the
    `verify_one` call it makes when `integrate_one` leaves a synthetic tail
    group OPEN, per its `make_verifier` seam (task 1.x)."""

    def test_verify_one_called_for_group_left_open_by_integrate_one(self):
        finding = _finding("TASK-1.1")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = str(Path(tmp) / "journal.json")

            def fake_integrate_one(g, *_args, **_kwargs):
                integrate._write_group_journal(
                    journal_path, g["name"], "https://github.com/acme/repo/pull/1",
                    "tail-task-1.1", "OPEN",
                )
                return None

            fake_verifier = unittest.mock.Mock()

            def fake_make_verifier():
                return fake_verifier

            with unittest.mock.patch.object(
                integrate, "integrate_one", side_effect=fake_integrate_one
            ):
                result = integrate.reconcile_unreconciled_tail_evidence(
                    [finding], Path("/fake/repo"), "spec-1", [{"id": "TASK-1.1", "deps": []}],
                    "origin", "run-1", "main", journal_path,
                    make_verifier=fake_make_verifier,
                )

            fake_verifier.verify_one.assert_called_once()
            called_group = fake_verifier.verify_one.call_args.args[0]
            self.assertEqual(called_group["tasks"], ["TASK-1.1"])
            self.assertEqual(called_group["name"], "tail-task-1.1")
            self.assertEqual(result[0]["task"], "TASK-1.1")

    def test_verify_one_not_called_when_integrate_one_leaves_group_merged(self):
        finding = _finding("TASK-1.2")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = str(Path(tmp) / "journal.json")

            def fake_integrate_one(g, *_args, **_kwargs):
                integrate._write_group_journal(
                    journal_path, g["name"], "https://github.com/acme/repo/pull/2",
                    "tail-task-1.2", "MERGED",
                )
                return None

            fake_verifier = unittest.mock.Mock()

            def fake_make_verifier():
                return fake_verifier

            with unittest.mock.patch.object(
                integrate, "integrate_one", side_effect=fake_integrate_one
            ):
                result = integrate.reconcile_unreconciled_tail_evidence(
                    [finding], Path("/fake/repo"), "spec-1", [{"id": "TASK-1.2", "deps": []}],
                    "origin", "run-1", "main", journal_path,
                    make_verifier=fake_make_verifier,
                )

            fake_verifier.verify_one.assert_not_called()
            self.assertEqual(result[0]["task"], "TASK-1.2")
            self.assertEqual(result[0]["reconcile_state"], "merged")

    def test_verify_one_not_called_when_integrate_one_leaves_group_quarantined(self):
        finding = _finding("TASK-1.3")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = str(Path(tmp) / "journal.json")

            def fake_integrate_one(g, *_args, **_kwargs):
                integrate._write_group_journal(
                    journal_path, g["name"], "",
                    "tail-task-1.3", "QUARANTINED", "integration-error",
                )
                return None

            fake_verifier = unittest.mock.Mock()

            def fake_make_verifier():
                return fake_verifier

            with unittest.mock.patch.object(
                integrate, "integrate_one", side_effect=fake_integrate_one
            ):
                result = integrate.reconcile_unreconciled_tail_evidence(
                    [finding], Path("/fake/repo"), "spec-1", [{"id": "TASK-1.3", "deps": []}],
                    "origin", "run-1", "main", journal_path,
                    make_verifier=fake_make_verifier,
                )

            fake_verifier.verify_one.assert_not_called()
            self.assertEqual(result[0]["task"], "TASK-1.3")
            self.assertEqual(result[0]["reconcile_state"], "quarantined")

    def test_verify_one_exception_quarantines_only_that_finding(self):
        finding_a = _finding("TASK-1.4")
        finding_b = _finding("TASK-1.5")

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            journal_path = str(Path(tmp) / "journal.json")

            def fake_integrate_one(g, *_args, **_kwargs):
                integrate._write_group_journal(
                    journal_path, g["name"], "https://github.com/acme/repo/pull/3",
                    g["name"], "OPEN",
                )
                return None

            fake_verifier = unittest.mock.Mock()

            def fake_verify_one(g, *_args, **_kwargs):
                if g["name"] == "tail-task-1.4":
                    raise RuntimeError("boom")

            fake_verifier.verify_one.side_effect = fake_verify_one

            def fake_make_verifier():
                return fake_verifier

            with unittest.mock.patch.object(
                integrate, "integrate_one", side_effect=fake_integrate_one
            ):
                result = integrate.reconcile_unreconciled_tail_evidence(
                    [finding_a, finding_b], Path("/fake/repo"), "spec-1",
                    [
                        {"id": "TASK-1.4", "deps": []},
                        {"id": "TASK-1.5", "deps": []},
                    ],
                    "origin", "run-1", "main", journal_path,
                    make_verifier=fake_make_verifier,
                )

            self.assertEqual(fake_verifier.verify_one.call_count, 2)
            self.assertEqual(result[0]["task"], "TASK-1.4")
            self.assertEqual(result[0]["reconcile_state"], "quarantined")
            self.assertEqual(result[1]["task"], "TASK-1.5")
            self.assertEqual(result[1]["reconcile_state"], "opened")


if __name__ == "__main__":
    unittest.main(verbosity=2)
