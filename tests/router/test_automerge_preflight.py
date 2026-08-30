#!/usr/bin/env python3
"""Unit tests for automerge_preflight.py (stdlib unittest, mirrors test_policy.py style)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

from worktrail.router.automerge_preflight import (
    is_preflight_query_error,
    owner_repo_from_git,
    repo_settings,
    required_checks_gate,
    required_status_check_contexts,
)

_NO_SLEEP = lambda seconds: None


class _FakeResult:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout


class _FakeRunner:
    """Scripted `subprocess.run`-alike keyed by the command's first two argv
    tokens (enough to distinguish `git remote`, `gh api .../rules/...`, and
    `gh api repos/{owner_repo}`)."""

    def __init__(self, responses: dict[str, _FakeResult]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> _FakeResult:
        self.calls.append(cmd)
        for key, result in self.responses.items():
            if cmd[: len(key.split())] == key.split():
                return result
        raise AssertionError(f"unscripted command: {cmd}")


def _rules_response(contexts: list[str]) -> _FakeResult:
    rules = [
        {
            "type": "required_status_checks",
            "parameters": {
                "required_status_checks": [{"context": c} for c in contexts]
            },
        }
    ]
    return _FakeResult(0, json.dumps(rules))


def _repo_settings_response(allow_auto_merge: bool) -> _FakeResult:
    return _FakeResult(0, json.dumps({"allow_auto_merge": allow_auto_merge}))


class TestOwnerRepoFromGit(unittest.TestCase):
    def test_parses_https_remote(self) -> None:
        runner = _FakeRunner(
            {
                "git remote": _FakeResult(0, "https://github.com/acme/widgets.git\n"),
            }
        )
        self.assertEqual(owner_repo_from_git(Path("."), runner), "acme/widgets")

    def test_parses_ssh_remote(self) -> None:
        runner = _FakeRunner(
            {
                "git remote": _FakeResult(0, "git@github.com:acme/widgets.git\n"),
            }
        )
        self.assertEqual(owner_repo_from_git(Path("."), runner), "acme/widgets")

    def test_no_remote_returns_none(self) -> None:
        runner = _FakeRunner({"git remote": _FakeResult(1, "")})
        self.assertIsNone(owner_repo_from_git(Path("."), runner))

    def test_non_github_remote_returns_none(self) -> None:
        runner = _FakeRunner(
            {
                "git remote": _FakeResult(0, "https://gitlab.com/acme/widgets.git\n"),
            }
        )
        self.assertIsNone(owner_repo_from_git(Path("."), runner))


class TestRequiredStatusCheckContexts(unittest.TestCase):
    def test_extracts_contexts_from_ruleset_rules(self) -> None:
        runner = _FakeRunner({"gh api": _rules_response(["ci", "lint"])})
        self.assertEqual(
            required_status_check_contexts("acme/widgets", "main", runner),
            ["ci", "lint"],
        )

    def test_zero_required_checks_is_empty_list_not_none(self) -> None:
        runner = _FakeRunner({"gh api": _rules_response([])})
        self.assertEqual(
            required_status_check_contexts("acme/widgets", "main", runner), []
        )

    def test_ignores_non_status_check_rules(self) -> None:
        rules = [{"type": "deletion"}, {"type": "non_fast_forward"}]
        runner = _FakeRunner({"gh api": _FakeResult(0, json.dumps(rules))})
        self.assertEqual(
            required_status_check_contexts("acme/widgets", "main", runner), []
        )

    def test_api_failure_returns_none(self) -> None:
        runner = _FakeRunner({"gh api": _FakeResult(1, "")})
        self.assertIsNone(
            required_status_check_contexts("acme/widgets", "main", runner)
        )

    def test_malformed_json_returns_none(self) -> None:
        runner = _FakeRunner({"gh api": _FakeResult(0, "not json")})
        self.assertIsNone(
            required_status_check_contexts("acme/widgets", "main", runner)
        )


class TestRepoSettings(unittest.TestCase):
    def test_returns_parsed_settings(self) -> None:
        runner = _FakeRunner({"gh api": _repo_settings_response(True)})
        self.assertEqual(
            repo_settings("acme/widgets", runner), {"allow_auto_merge": True}
        )

    def test_api_failure_returns_none(self) -> None:
        runner = _FakeRunner({"gh api": _FakeResult(1, "")})
        self.assertIsNone(repo_settings("acme/widgets", runner))


class TestRequiredChecksGate(unittest.TestCase):
    # A rules/settings query that never recovers within any reasonable retry
    # budget -- used to script "persistently fails" scenarios.
    _NEVER_RECOVERS = 10**9

    def _runner(
        self,
        remote_ok: bool = True,
        contexts: list[str] | None = None,
        allow_auto_merge: bool = True,
        rules_fail_count: int = 0,
        settings_fail_count: int = 0,
    ) -> _RoutedRunner:
        """Route by URL content (rules query vs repo-settings query), not raw
        call order -- retries mean either query can be called multiple times,
        so a call-order counter (the pre-retry design) misattributes retried
        calls to the wrong query. `rules_fail_count`/`settings_fail_count`
        script the first N calls to that query as failures before it starts
        succeeding (0 = always succeeds; `_NEVER_RECOVERS` = always fails)."""
        remote_result = (
            _FakeResult(0, "https://github.com/acme/widgets.git\n")
            if remote_ok
            else _FakeResult(1, "")
        )
        effective_contexts = contexts if contexts is not None else ["ci"]
        return _RoutedRunner(
            remote_result=remote_result,
            rules_result=_rules_response(effective_contexts),
            rules_failure=_FakeResult(1, ""),
            rules_fail_count=rules_fail_count,
            settings_result=_repo_settings_response(allow_auto_merge),
            settings_failure=_FakeResult(1, ""),
            settings_fail_count=settings_fail_count,
        )

    def test_eligible_when_checks_present_and_auto_merge_allowed(self) -> None:
        runner = self._runner(contexts=["ci"], allow_auto_merge=True)
        ok, reason = required_checks_gate(Path("."), "main", runner, sleep=_NO_SLEEP)
        self.assertTrue(ok)
        self.assertIn("ci", reason)

    def test_refuses_on_zero_required_checks(self) -> None:
        runner = self._runner(contexts=[], allow_auto_merge=True)
        ok, reason = required_checks_gate(Path("."), "main", runner, sleep=_NO_SLEEP)
        self.assertFalse(ok)
        self.assertIn("zero required status checks", reason)
        self.assertFalse(is_preflight_query_error(reason))

    def test_refuses_when_allow_auto_merge_false(self) -> None:
        runner = self._runner(contexts=["ci"], allow_auto_merge=False)
        ok, reason = required_checks_gate(Path("."), "main", runner, sleep=_NO_SLEEP)
        self.assertFalse(ok)
        self.assertIn("allow_auto_merge=false", reason)
        self.assertFalse(is_preflight_query_error(reason))

    def test_refuses_when_no_github_remote(self) -> None:
        runner = self._runner(remote_ok=False)
        ok, reason = required_checks_gate(Path("."), "main", runner, sleep=_NO_SLEEP)
        self.assertFalse(ok)
        self.assertIn("owner/repo", reason)
        self.assertFalse(is_preflight_query_error(reason))

    def test_refuses_when_rules_query_persistently_fails(self) -> None:
        runner = self._runner(rules_fail_count=self._NEVER_RECOVERS)
        ok, reason = required_checks_gate(
            Path("."), "main", runner, retries=3, sleep=_NO_SLEEP
        )
        self.assertFalse(ok)
        self.assertIn("gh api failed", reason)
        self.assertTrue(is_preflight_query_error(reason))
        # Retried the full budget, not just attempted once.
        rules_calls = [c for c in runner.calls if "rules/branches" in " ".join(c)]
        self.assertEqual(len(rules_calls), 3)

    def test_refuses_when_settings_query_persistently_fails(self) -> None:
        runner = self._runner(contexts=["ci"], settings_fail_count=self._NEVER_RECOVERS)
        ok, reason = required_checks_gate(
            Path("."), "main", runner, retries=3, sleep=_NO_SLEEP
        )
        self.assertFalse(ok)
        self.assertIn("could not query repo settings", reason)
        self.assertTrue(is_preflight_query_error(reason))

    def test_rules_query_recovers_within_retry_budget(self) -> None:
        """The exact incident this brief fixes: one transient `gh api` blip on
        an otherwise-healthy repo must not read as "zero required checks" --
        it must retry and succeed."""
        runner = self._runner(
            contexts=["ci"], allow_auto_merge=True, rules_fail_count=2
        )
        ok, reason = required_checks_gate(
            Path("."), "main", runner, retries=3, sleep=_NO_SLEEP
        )
        self.assertTrue(ok)
        self.assertIn("ci", reason)
        rules_calls = [c for c in runner.calls if "rules/branches" in " ".join(c)]
        self.assertEqual(len(rules_calls), 3)  # 2 failures + 1 success

    def test_rules_query_exhausts_retry_budget_before_recovering(self) -> None:
        # Recovers on the 4th call, but retries is only 3 -- must still fail.
        runner = self._runner(contexts=["ci"], rules_fail_count=3)
        ok, reason = required_checks_gate(
            Path("."), "main", runner, retries=3, sleep=_NO_SLEEP
        )
        self.assertFalse(ok)
        self.assertTrue(is_preflight_query_error(reason))


class _RoutedRunner(_FakeRunner):
    """Dispatches by URL content (rules vs repo-settings), independent of call
    order, so retries never misattribute a retried call to the wrong query.
    `*_fail_count` scripts the first N calls to that query as failures before
    switching to `*_result` -- 0 always succeeds, a huge count never recovers."""

    def __init__(
        self,
        *,
        remote_result: _FakeResult,
        rules_result: _FakeResult,
        rules_failure: _FakeResult,
        rules_fail_count: int,
        settings_result: _FakeResult,
        settings_failure: _FakeResult,
        settings_fail_count: int,
    ) -> None:
        super().__init__({})
        self._remote_result = remote_result
        self._rules_result = rules_result
        self._rules_failure = rules_failure
        self._rules_fail_count = rules_fail_count
        self._settings_result = settings_result
        self._settings_failure = settings_failure
        self._settings_fail_count = settings_fail_count
        self._rules_calls = 0
        self._settings_calls = 0

    def __call__(self, cmd: list[str], **kwargs: Any) -> _FakeResult:
        self.calls.append(cmd)
        joined = " ".join(cmd)
        if cmd[:2] == ["git", "remote"]:
            return self._remote_result
        if "rules/branches" in joined:
            self._rules_calls += 1
            if self._rules_calls <= self._rules_fail_count:
                return self._rules_failure
            return self._rules_result
        self._settings_calls += 1
        if self._settings_calls <= self._settings_fail_count:
            return self._settings_failure
        return self._settings_result


if __name__ == "__main__":
    unittest.main()
