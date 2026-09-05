#!/usr/bin/env python3
"""Regression tests for the push-refusal `detail=null` gap
(openspec/changes/shared-pr-landing-pipeline/tasks.md task 14.1).

`_push()` used to discard a failed `git push`'s stdout/stderr entirely,
so `land_pr()` reported `LandOutcome(outcome="refused", refused_step="push",
detail=None)` with no information about *why* the push was rejected. These
tests pin `_push()`'s stderr (falling back to stdout) capture via its
`detail_out` out-param, and `land_pr()`'s own surfacing of that detail on
the `refused_step == "push"` path.

Kept in its own module rather than extending `tests/router/test_land_pr.py`:
that file's tasks 1.1->1.2 already saturate the compile same-file chain
gate.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from unittest import mock

from worktrail.router import land_pr


class PushDetailCaptureTests(unittest.TestCase):
    """Unit tests directly against `_push()`."""

    def _runner(self, push_result: subprocess.CompletedProcess):
        def runner(cmd, **kwargs):
            if cmd[-3:] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            if "push" in cmd:
                return push_result
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        return runner

    def test_success_returns_none_and_leaves_detail_out_empty(self) -> None:
        runner = self._runner(subprocess.CompletedProcess([], 0, stdout="", stderr=""))
        detail_out: list[str] = []
        refused = land_pr._push("/tmp", "feature", "origin", runner, detail_out)
        self.assertIsNone(refused)
        self.assertEqual(detail_out, [])

    def test_failure_captures_stderr_into_detail_out(self) -> None:
        runner = self._runner(
            subprocess.CompletedProcess(
                [],
                1,
                stdout="",
                stderr="! [remote rejected] feature -> feature (permission denied)",
            )
        )
        detail_out: list[str] = []
        refused = land_pr._push("/tmp", "feature", "origin", runner, detail_out)
        self.assertEqual(refused, "push")
        self.assertEqual(
            detail_out, ["! [remote rejected] feature -> feature (permission denied)"]
        )

    def test_failure_falls_back_to_stdout_when_stderr_empty(self) -> None:
        runner = self._runner(
            subprocess.CompletedProcess(
                [], 1, stdout="everything up-to-date", stderr=""
            )
        )
        detail_out: list[str] = []
        refused = land_pr._push("/tmp", "feature", "origin", runner, detail_out)
        self.assertEqual(refused, "push")
        self.assertEqual(detail_out, ["everything up-to-date"])

    def test_failure_with_no_output_leaves_detail_out_empty(self) -> None:
        runner = self._runner(subprocess.CompletedProcess([], 1, stdout="", stderr=""))
        detail_out: list[str] = []
        refused = land_pr._push("/tmp", "feature", "origin", runner, detail_out)
        self.assertEqual(refused, "push")
        self.assertEqual(detail_out, [])

    def test_ambiguous_timeout_does_not_populate_detail_out(self) -> None:
        def runner(cmd, **kwargs):
            if cmd[-3:] == ["rev-parse", "--abbrev-ref", "--symbolic-full-name"]:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=60)

        detail_out: list[str] = []
        refused = land_pr._push("/tmp", "feature", "origin", runner, detail_out)
        self.assertEqual(refused, "push_ambiguous")
        self.assertEqual(detail_out, [])

    def test_omitting_detail_out_still_returns_refused_step(self) -> None:
        runner = self._runner(
            subprocess.CompletedProcess([], 1, stdout="", stderr="denied")
        )
        refused = land_pr._push("/tmp", "feature", "origin", runner)
        self.assertEqual(refused, "push")


class RunRecordSpy:
    def __init__(self, run_path: str = "runs/fake-run.yaml") -> None:
        self.run_path = run_path
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        if argv[0] == "start":
            print(json.dumps({"path": self.run_path}))
        return 0


def _land_request(**overrides) -> land_pr.LandRequest:
    kwargs = {
        "repo": "/tmp/does-not-matter",
        "base_branch": "main",
        "title": "Fix the widget",
        "summary": "Fixes the widget.",
        "route": "B",
        "run": "runs/existing.yaml",
    }
    kwargs.update(overrides)
    return land_pr.LandRequest(**kwargs)


class LandPrPushRefusalOrchestrationTests(unittest.TestCase):
    """Full `land_pr()` runs with every step up to and including `_push()`
    patched at the seam `land_pr()` composes through, isolating exactly the
    push-refusal detail-surfacing behavior under test."""

    def _patched(self, **overrides):
        defaults = {
            "_commit_pending": None,
            "_ensure_compile_markers": (None, None),
            "_run_preflight_and_labels": (None, ["go:risk-low"]),
            "_current_branch": "feature",
            "_push_target": ("origin", None),
        }
        defaults.update(overrides)
        return [
            mock.patch.object(land_pr, name, return_value=value)
            for name, value in defaults.items()
        ]

    def _run(self, request, push_side_effect, run_record_spy=None):
        run_record_spy = run_record_spy or RunRecordSpy()
        patchers = self._patched()
        with (
            mock.patch.object(land_pr.run_record_module, "main", run_record_spy),
            mock.patch.object(land_pr, "_push", side_effect=push_side_effect),
        ):
            for p in patchers:
                p.start()
            try:
                outcome = land_pr.land_pr(request)
            finally:
                for p in patchers:
                    p.stop()
        return outcome, run_record_spy

    def test_push_refusal_surfaces_captured_stderr_as_detail(self) -> None:
        def fake_push(repo, branch, remote, runner, detail_out=None):
            if detail_out is not None:
                detail_out.append("! [remote rejected] feature -> feature (denied)")
            return "push"

        outcome, _ = self._run(_land_request(), fake_push)
        self.assertEqual(outcome.outcome, "refused")
        self.assertEqual(outcome.refused_step, "push")
        self.assertEqual(
            outcome.detail, "! [remote rejected] feature -> feature (denied)"
        )

    def test_push_refusal_with_no_captured_output_reports_none(self) -> None:
        def fake_push(repo, branch, remote, runner, detail_out=None):
            return "push"

        outcome, _ = self._run(_land_request(), fake_push)
        self.assertEqual(outcome.outcome, "refused")
        self.assertEqual(outcome.refused_step, "push")
        self.assertIsNone(outcome.detail)


if __name__ == "__main__":
    unittest.main()
