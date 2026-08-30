#!/usr/bin/env python3
"""Tests for PR pacing (--pr-pacing-wait / go-policy pr_pacing_wait_s).

Covers:
  - integrate._wait_for_pr_checks outcome classification (merged / closed /
    checks-done / no-checks / timeout / error) for both statusCheckRollup
    entry shapes (CheckRun and StatusContext).
  - _pipeline_scheduler pacing: the injected integrate_one seam is wrapped so
    concurrent per-group IV threads open PRs paced by the previous PR's URL.

Run: python3 test_pr_pacing.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_pipeline import (
    FakeSpawn,
    FakeVerifier,
    _init_repo,
    _make_integrate_one,
)

from worktrail.orchestrator import (
    integrate,
    live,
)

Proc = namedtuple("Proc", "returncode stdout stderr")


def _pr_view(state="OPEN", rollup=None):
    return Proc(0, json.dumps({"state": state, "statusCheckRollup": rollup}), "")


class WaitForPrChecksTest(unittest.TestCase):
    """Outcome classification of integrate._wait_for_pr_checks."""

    def _wait(self, responses, timeout_s=5):
        """Run _wait_for_pr_checks against a scripted sequence of gh outputs
        (last response repeats)."""
        seq = list(responses)

        def fake_run(cmd, **kw):
            return seq.pop(0) if len(seq) > 1 else seq[0]

        with patch.object(integrate.subprocess, "run", side_effect=fake_run):
            return integrate._wait_for_pr_checks(
                Path("."), "http://pr/1", timeout_s, poll_s=0
            )

    def test_merged_pr_returns_merged(self):
        self.assertEqual(self._wait([_pr_view(state="MERGED")]), "merged")

    def test_closed_pr_returns_closed(self):
        self.assertEqual(self._wait([_pr_view(state="CLOSED")]), "closed")

    def test_completed_checkruns_return_checks_done(self):
        rollup = [
            {"status": "COMPLETED", "conclusion": "SUCCESS"},
            {"state": "SUCCESS"},  # StatusContext shape
        ]
        self.assertEqual(self._wait([_pr_view(rollup=rollup)]), "checks-done")

    def test_pending_then_completed(self):
        pending = _pr_view(rollup=[{"status": "IN_PROGRESS"}])
        done = _pr_view(rollup=[{"status": "COMPLETED", "conclusion": "FAILURE"}])
        self.assertEqual(self._wait([pending, done]), "checks-done")

    def test_pending_status_context_blocks(self):
        pending = _pr_view(rollup=[{"state": "PENDING"}])
        self.assertEqual(self._wait([pending], timeout_s=0.2), "timeout")

    def test_empty_rollup_twice_returns_no_checks(self):
        self.assertEqual(self._wait([_pr_view(rollup=[])]), "no-checks")

    def test_gh_failure_returns_error(self):
        self.assertEqual(self._wait([Proc(1, "", "boom")]), "error")

    def test_bad_json_returns_error(self):
        self.assertEqual(self._wait([Proc(0, "not json", "")]), "error")

    def test_zero_timeout_returns_timeout_without_polling(self):
        def explode(cmd, **kw):  # pragma: no cover - must not be called
            raise AssertionError("gh should not be invoked with timeout_s=0")

        with patch.object(integrate.subprocess, "run", side_effect=explode):
            self.assertEqual(
                integrate._wait_for_pr_checks(Path("."), "http://pr/1", 0), "timeout"
            )


class PipelinePacingTest(unittest.TestCase):
    """_pipeline_scheduler paces PR-opening across concurrent IV threads."""

    def _run_pipeline(self, pacing):
        waits = []

        def fake_wait(repo, pr_url, timeout_s, poll_s=30):
            waits.append((pr_url, timeout_s))
            return "checks-done"

        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            integrate_one, events = _make_integrate_one()
            with patch.object(integrate, "_wait_for_pr_checks", side_effect=fake_wait):
                summary = live._pipeline_scheduler(
                    repo=repo,
                    spec_rel="docs/specs/001-x",
                    remote="origin",
                    base="main",
                    model="haiku",
                    max_workers=3,
                    timeout=30,
                    resume=False,
                    only=None,
                    role_models=None,
                    run_budget=None,
                    journal_path=str(Path(tmp) / "pipeline-journal.json"),
                    run_id="full-test",
                    pr_pacing_wait=pacing,
                    _spawn=FakeSpawn(),
                    _integrate_one=integrate_one,
                    _make_verifier=lambda: FakeVerifier(),
                )
        return summary, waits

    def test_paced_pipeline_waits_between_group_prs(self):
        summary, waits = self._run_pipeline(600)
        self.assertEqual(len(summary["group_prs"]), 3)
        # 3 PRs opened -> paced twice (never before the first PR), each wait
        # keyed on some previously-opened group's PR URL with the full bound.
        self.assertEqual(len(waits), 2)
        opened = {url for _, _, url in summary["group_prs"]}
        for url, timeout_s in waits:
            self.assertIn(url, opened)
            self.assertEqual(timeout_s, 600)
        # base integrates first, so the first wait is on the base group's PR.
        self.assertEqual(waits[0][0], "http://fake-pr/base")

    def test_pipeline_unpaced_by_default(self):
        summary, waits = self._run_pipeline(0)
        self.assertEqual(len(summary["group_prs"]), 3)
        self.assertEqual(waits, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
