#!/usr/bin/env python3
"""Integration coverage: both `live.py` tail-dispatch call sites
(`_pipeline_scheduler` and `_full_real_inner`) must run
`integrate.reconcile_unreconciled_tail_evidence` over
`integrate.detect_unreconciled_tail_evidence`'s findings before calling
`integrate._record_unreconciled_tail_evidence`, and must skip reconciliation
entirely (leaving only the existing clear-when-empty journal write) when
there are no findings.

Companion to test_pipeline.py (`_pipeline_scheduler`, injectable
_spawn/_integrate_one/_make_verifier seams) and test_full_real_tail_dispatch.py
(`_full_real_inner`, real-git + LiveSpawn/verify/gh seams) -- reuses their
fixtures rather than re-deriving repo/fan-out setup.

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
from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import verify  # noqa: E402

from test_full_real_tail_dispatch import (  # noqa: E402
    FakeGh,
    FakeSpawn as FullRealFakeSpawn,
    _fake_verify_and_cleanup,
    _init_repo_with_tail,
)
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
                integrate, "detect_unreconciled_tail_evidence", return_value=[_finding()]
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
                integrate, "detect_unreconciled_tail_evidence", return_value=[]
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


class FullRealInnerReconciliationTest(unittest.TestCase):
    """`_full_real_inner`'s tail block -- mirrors PipelineSchedulerReconciliationTest
    for the sequential/default (non-pipeline) scheduler."""

    def _run(self, repo):
        spawn = FullRealFakeSpawn()
        with unittest.mock.patch.object(live, "LiveSpawn", return_value=spawn):
            with unittest.mock.patch.object(
                verify, "verify_and_cleanup", side_effect=_fake_verify_and_cleanup
            ):
                with unittest.mock.patch(
                    "worktrail.orchestrator.integrate.subprocess.run",
                    side_effect=FakeGh(),
                ):
                    return live._full_real_inner(
                        str(repo),
                        "docs/specs/001-x",
                        remote="origin",
                        base="main",
                        max_workers=3,
                        timeout=30,
                        pipeline=False,
                    )

    def test_reconciliation_invoked_before_recording_when_findings_present(self):
        call_order = []

        def fake_reconcile(findings, *_args, **_kwargs):
            call_order.append("reconcile")
            return findings

        def fake_record(*_args, **_kwargs):
            call_order.append("record")

        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_tail(Path(tmp), with_origin=True)

            with unittest.mock.patch.object(
                integrate, "detect_unreconciled_tail_evidence", return_value=[_finding()]
            ), unittest.mock.patch.object(
                integrate, "reconcile_unreconciled_tail_evidence", side_effect=fake_reconcile
            ) as reconcile_mock, unittest.mock.patch.object(
                integrate, "_record_unreconciled_tail_evidence", side_effect=fake_record
            ) as record_mock:
                self._run(repo)

            reconcile_mock.assert_called_once()
            self.assertEqual(reconcile_mock.call_args.args[0], [_finding()])
            record_mock.assert_called_once()
            self.assertEqual(
                call_order, ["reconcile", "record"],
                "reconciliation must run before the finding is recorded to the journal",
            )

    def test_empty_findings_is_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_tail(Path(tmp), with_origin=True)

            with unittest.mock.patch.object(
                integrate, "detect_unreconciled_tail_evidence", return_value=[]
            ), unittest.mock.patch.object(
                integrate, "reconcile_unreconciled_tail_evidence"
            ) as reconcile_mock, unittest.mock.patch.object(
                integrate, "_record_unreconciled_tail_evidence"
            ) as record_mock:
                self._run(repo)

            reconcile_mock.assert_not_called()
            record_mock.assert_called_once()
            self.assertFalse(
                record_mock.call_args.args[1],
                "no findings must still clear any stale journal entry, "
                f"got {record_mock.call_args.args[1]!r}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
