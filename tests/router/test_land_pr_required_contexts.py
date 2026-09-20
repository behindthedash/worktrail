#!/usr/bin/env python3
"""The CI watch settles only once every required status-check context is
reported (Requirement: CI watch settles only on required-context coverage).

`gh pr checks` -- both the one-shot registration probe and `--watch` -- is
happy to report (and exit 0 over) whatever subset of checks happens to exist
at the time. A required context that GitHub has not created yet therefore
reads exactly like a passing one, which is how a PR can be landed with its
gating CI never observed. These tests script that subset through `FakeRun`
(reused from `test_land_pr.py`, the canonical `Runner` fake) and pin both the
grace loop and the blocking-watch loop against it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import land_pr

from .test_land_pr import FakeRun

_REPO = Path("/repo")
_PR = 42
_REQUIRED = ["Lint, Test & Build"]


def _checks_json(*names: str) -> str:
    return json.dumps([{"name": n} for n in names])


def _run(fake: FakeRun, **kwargs):
    """`_watch_ci` with sleeps neutralized -- the grace loop's real interval
    would add seconds of pure wall-clock per exhaustion test."""
    with mock.patch.object(land_pr.time, "sleep"):
        return land_pr._watch_ci(_REPO, _PR, 60, fake, **kwargs)


class ChecksRegisteredRequiredContexts(unittest.TestCase):
    def test_reported_names_missing_a_required_context_are_false(self) -> None:
        fake = FakeRun().script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review"),
        )
        self.assertIs(
            land_pr._checks_registered(_REPO, _PR, fake, None, _REQUIRED), False
        )

    def test_every_required_context_present_is_true(self) -> None:
        fake = FakeRun().script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review", "Lint, Test & Build"),
        )
        self.assertIs(
            land_pr._checks_registered(_REPO, _PR, fake, None, _REQUIRED), True
        )

    def test_unparseable_response_is_none(self) -> None:
        fake = FakeRun().script(
            "gh", "pr", "checks", str(_PR), "--json", "name", stdout="not json"
        )
        self.assertIsNone(land_pr._checks_registered(_REPO, _PR, fake, None, _REQUIRED))

    def test_none_contexts_keep_todays_behaviour(self) -> None:
        fake = FakeRun().script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review"),
        )
        self.assertIs(land_pr._checks_registered(_REPO, _PR, fake, None, None), True)

    def test_empty_contexts_keep_todays_behaviour(self) -> None:
        fake = FakeRun().script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review"),
        )
        self.assertIs(land_pr._checks_registered(_REPO, _PR, fake, None, []), True)

    def test_explicit_no_checks_is_still_false(self) -> None:
        fake = FakeRun().script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            returncode=1,
            stderr="no checks reported on the 'x' branch",
        )
        self.assertIs(
            land_pr._checks_registered(_REPO, _PR, fake, None, _REQUIRED), False
        )


class WatchCiRequiredContexts(unittest.TestCase):
    def test_non_required_only_checks_keep_polling_then_exhaust(self) -> None:
        fake = FakeRun().script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review"),
        )
        result = _run(fake, required_contexts=_REQUIRED)
        self.assertTrue(result["budget_exhausted"])
        self.assertFalse(result["settled"])
        probes = [
            c
            for c in fake.calls
            if c[:5] == ["gh", "pr", "checks", str(_PR), "--json"] and c[5] == "name"
        ]
        self.assertEqual(len(probes), land_pr._NO_CHECKS_GRACE_ATTEMPTS)
        self.assertFalse(
            fake.called_with_prefix("gh", "pr", "checks", str(_PR), "--watch")
        )

    def test_coverage_on_a_later_poll_enters_the_watch(self) -> None:
        fake = FakeRun()
        fake.script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review"),
        )
        fake.script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review", "Lint, Test & Build"),
        )
        result = _run(fake, required_contexts=_REQUIRED)
        self.assertTrue(result["settled"])
        self.assertFalse(result["budget_exhausted"])
        self.assertTrue(
            fake.called_with_prefix("gh", "pr", "checks", str(_PR), "--watch")
        )

    def test_clean_watch_exit_without_coverage_does_not_settle(self) -> None:
        fake = FakeRun()
        # Coverage at the grace probe, then gone by the post-watch re-check:
        # every re-issue of the watch exits clean over the non-required
        # check alone, so the loop runs out its budget instead of settling.
        fake.script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review", "Lint, Test & Build"),
        )
        fake.script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review"),
        )
        result = _run(fake, required_contexts=_REQUIRED)
        self.assertFalse(result["settled"])
        self.assertTrue(result["budget_exhausted"])
        watches = [
            c
            for c in fake.calls
            if c[:5] == ["gh", "pr", "checks", str(_PR), "--watch"]
        ]
        self.assertEqual(len(watches), land_pr.WATCH_REISSUE_MAX + 1)

    def test_clean_watch_exit_with_coverage_settles(self) -> None:
        fake = FakeRun().script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("Lint, Test & Build"),
        )
        result = _run(fake, required_contexts=_REQUIRED)
        self.assertTrue(result["settled"])
        self.assertFalse(result["budget_exhausted"])

    def test_no_required_contexts_reproduces_todays_behaviour(self) -> None:
        for contexts in (None, []):
            with self.subTest(contexts=contexts):
                fake = FakeRun().script(
                    "gh",
                    "pr",
                    "checks",
                    str(_PR),
                    "--json",
                    "name",
                    stdout=_checks_json("claude-review"),
                )
                result = _run(fake, required_contexts=contexts)
                self.assertTrue(result["settled"])
                self.assertFalse(result["budget_exhausted"])

    def test_merged_pr_settles_without_coverage(self) -> None:
        fake = FakeRun()
        fake.script(
            "gh",
            "pr",
            "view",
            str(_PR),
            "--json",
            "state",
            stdout=json.dumps({"state": "MERGED"}),
        )
        fake.script(
            "gh",
            "pr",
            "checks",
            str(_PR),
            "--json",
            "name",
            stdout=_checks_json("claude-review"),
        )
        result = _run(fake, required_contexts=_REQUIRED)
        self.assertTrue(result["settled"])
        self.assertFalse(result["budget_exhausted"])


if __name__ == "__main__":
    unittest.main()
