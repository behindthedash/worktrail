#!/usr/bin/env python3
"""The check-registration grace period scales with the run's watch budget,
and its exhaustion is reported distinctly from the main watch loop's.

A fixed three probes (~9s) is far shorter than the post-`gh pr create`
registration race it exists to absorb on a run given a long
`--watch-timeout`, so `_no_checks_grace_attempts` derives the probe count
from that budget. When the grace is genuinely spent, the returned dict
carries `checks_unregistered` so `land_pr()` can name *unregistered* checks
instead of the main loop's "still pending" wording.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import land_pr

from .test_land_pr import FakeRun, LandPrOrchestrationTests, _land_request

_REPO = Path("/repo")
_PR = 42


def _no_checks_fake() -> FakeRun:
    return FakeRun().script(
        "gh",
        "pr",
        "checks",
        str(_PR),
        "--json",
        "name",
        returncode=1,
        stderr="no checks reported on the 'feature' branch",
    )


def _probes(fake: FakeRun) -> list[list[str]]:
    return [
        c
        for c in fake.calls
        if c[:5] == ["gh", "pr", "checks", str(_PR), "--json"] and c[5] == "name"
    ]


class _Harness:
    """Borrows `LandPrOrchestrationTests`' seam-patching helpers without
    subclassing it (which would re-run its whole suite here)."""

    _patched = LandPrOrchestrationTests._patched
    _run = LandPrOrchestrationTests._run


def _watch(fake: FakeRun, watch_timeout_s: int, **kwargs):
    with mock.patch.object(land_pr.time, "sleep"):
        return land_pr._watch_ci(_REPO, _PR, watch_timeout_s, fake, **kwargs)


class GraceAttemptDerivationTests(unittest.TestCase):
    def test_long_timeout_probes_more_than_the_fixed_attempts(self) -> None:
        fake = _no_checks_fake()
        result = _watch(fake, 600)
        self.assertTrue(result["budget_exhausted"])
        self.assertGreater(len(_probes(fake)), land_pr._NO_CHECKS_GRACE_ATTEMPTS)
        self.assertEqual(len(_probes(fake)), land_pr._no_checks_grace_attempts(600))

    def test_short_timeout_keeps_the_fixed_attempt_floor(self) -> None:
        fake = _no_checks_fake()
        result = _watch(fake, 3)
        self.assertTrue(result["budget_exhausted"])
        self.assertEqual(len(_probes(fake)), land_pr._NO_CHECKS_GRACE_ATTEMPTS)
        self.assertEqual(
            land_pr._no_checks_grace_attempts(3), land_pr._NO_CHECKS_GRACE_ATTEMPTS
        )

    def test_total_grace_never_exceeds_one_watch_window(self) -> None:
        for timeout in (60, 300, 601, 3600):
            with self.subTest(timeout=timeout):
                attempts = land_pr._no_checks_grace_attempts(timeout)
                self.assertLessEqual(
                    attempts * land_pr._NO_CHECKS_POLL_INTERVAL_S, timeout
                )

    def test_registration_past_the_fixed_count_enters_the_watch(self) -> None:
        fake = FakeRun()
        for _ in range(land_pr._NO_CHECKS_GRACE_ATTEMPTS + 2):
            fake.script(
                "gh",
                "pr",
                "checks",
                str(_PR),
                "--json",
                "name",
                returncode=1,
                stderr="no checks reported on the 'feature' branch",
            )
        fake.script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=json.dumps([{"name": "Lint, Test & Build"}]),
        )
        result = _watch(fake, 600)
        self.assertTrue(result["settled"])
        self.assertFalse(result["budget_exhausted"])
        self.assertGreater(len(_probes(fake)), land_pr._NO_CHECKS_GRACE_ATTEMPTS)
        self.assertTrue(
            fake.called_with_prefix("gh", "pr", "checks", str(_PR), "--watch")
        )

    def test_merge_during_the_grace_period_settles(self) -> None:
        fake = _no_checks_fake()
        with mock.patch.object(land_pr, "_pr_is_merged", return_value=True):
            result = _watch(fake, 600)
        self.assertTrue(result["settled"])
        self.assertFalse(result["budget_exhausted"])
        self.assertNotIn("checks_unregistered", result)

    def test_exhausted_grace_carries_the_unregistered_marker(self) -> None:
        result = _watch(_no_checks_fake(), 600)
        self.assertTrue(result["budget_exhausted"])
        self.assertTrue(result["checks_unregistered"])


class ExhaustedGraceReportingTests(unittest.TestCase):
    """End-to-end through `land_pr()`: the two budget-exhausted shapes get
    distinct merge results, same `ceiling` / `failed_recoverable` outcome."""

    def test_spent_registration_grace_names_unregistered_checks(self) -> None:
        outcome, spy = _Harness()._run(
            _land_request(),
            _watch_ci={
                "settled": False,
                "failing_checks": [],
                "log_excerpt": "",
                "budget_exhausted": True,
                "checks_unregistered": True,
            },
            _pr_is_merged=False,
        )
        self.assertEqual(outcome.outcome, "ceiling")
        self.assertEqual(outcome.final_status, "failed_recoverable")
        self.assertEqual(
            outcome.merge_result, "required checks never registered at watch budget"
        )
        finish_calls = spy.finish_calls()
        self.assertEqual(len(finish_calls), 1)
        self.assertIn(
            "required checks never registered at watch budget", finish_calls[0]
        )

    def test_main_watch_exhaustion_keeps_the_pending_wording(self) -> None:
        outcome, spy = _Harness()._run(
            _land_request(),
            _watch_ci={
                "settled": False,
                "failing_checks": ["unit-tests"],
                "log_excerpt": "boom",
                "budget_exhausted": True,
            },
            _pr_is_merged=False,
        )
        self.assertEqual(outcome.outcome, "ceiling")
        self.assertEqual(outcome.final_status, "failed_recoverable")
        self.assertEqual(outcome.merge_result, "checks still pending at watch budget")
        self.assertIn("checks still pending at watch budget", spy.finish_calls()[0])


if __name__ == "__main__":
    unittest.main()
