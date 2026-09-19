"""Regression tests for land_pr's post-PR-open `gh` calls on a fork checkout.

Incident (2026-09-18, behindthedash/aspens PR #15): the checkout has
`remote.pushDefault=fork` and `origin` is the upstream `aspenkit/aspens`. The
CI-watch / merge-state / review-thread steps addressed the PR by bare number,
so `gh` resolved `15` against `origin` -- an unrelated upstream PR #15 merged
months earlier -- and `land_pr()` reported `completed_and_merged` /
"merged externally" while the fork PR was still OPEN.

`ForkAwareRunner` reproduces that resolution rule: a `gh` call without
`-R <slug>` addresses the upstream; PR numbers collide across the two repos.
Every assertion here reduces to "no PR- or run-scoped `gh` call resolved to
the upstream".
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import land_pr

from .test_land_pr import FakeRun, _land_request
from .test_land_pr_resume import _HEAD, LandPrResumeTests

_UPSTREAM = "up/stream"
_FORK = "me/fork"
_PR_NUMBER = 15
_FORK_PR_URL = f"https://github.com/{_FORK}/pull/{_PR_NUMBER}"

# PR #15 exists in both repos: merged upstream long ago, open on the fork.
_PR_STATE = {_UPSTREAM: "MERGED", _FORK: "OPEN"}


class ForkAwareRunner(FakeRun):
    """`FakeRun` whose `gh` replies depend on which repo the call resolves to."""

    def __init__(self) -> None:
        super().__init__()
        self.resolved: list[tuple[tuple[str, ...], str]] = []
        self.failing_run_check = False

    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        if cmd[0] != "gh":
            return super().__call__(cmd, **kwargs)
        self.calls.append(list(cmd))
        args = cmd[1:]
        repo = args[args.index("-R") + 1] if "-R" in args else _UPSTREAM
        self.resolved.append((tuple(args), repo))
        return self._gh(cmd, args, repo)

    def _gh(self, cmd, args, repo) -> subprocess.CompletedProcess:
        def done(rc=0, out="") -> subprocess.CompletedProcess:
            return subprocess.CompletedProcess(list(cmd), rc, out, "")

        if args[:2] == ["pr", "view"]:
            if args[2] not in (str(_PR_NUMBER), "feature"):
                return done(1)
            if args[2] == "feature" and repo != _FORK:
                return done(1)
            state = _PR_STATE[repo]
            return done(
                out=json.dumps(
                    {
                        "url": f"https://github.com/{repo}/pull/{_PR_NUMBER}",
                        "number": _PR_NUMBER,
                        "state": state,
                        "mergeStateStatus": "CLEAN" if state == "OPEN" else "UNKNOWN",
                        "statusCheckRollup": [],
                    }
                )
            )
        if args[:2] == ["pr", "checks"]:
            if (
                self.failing_run_check
                and "--json" in args
                and "name,bucket,workflowRunId" in args
            ):
                return done(
                    out=json.dumps(
                        [
                            {
                                "name": "Initialize containers",
                                "bucket": "fail",
                                "workflowRunId": 9,
                            }
                        ]
                    )
                )
            if self.failing_run_check and "--watch" in args:
                return done(1)
            return done()
        return done()

    def upstream_calls(self) -> list[tuple[str, ...]]:
        return [args for args, repo in self.resolved if repo != _FORK]


class ForkResolutionHelperTests(unittest.TestCase):
    def test_pr_is_merged_asks_the_fork_not_the_upstream(self) -> None:
        runner = ForkAwareRunner()
        self.assertFalse(
            land_pr._pr_is_merged(Path("/repo"), _PR_NUMBER, runner, _FORK)
        )
        self.assertEqual(runner.upstream_calls(), [])

    def test_merge_state_guard_reads_the_fork_pr(self) -> None:
        runner = ForkAwareRunner()
        status = land_pr._merge_state_guard(Path("/repo"), _PR_NUMBER, runner, _FORK)
        self.assertEqual(status["state"], "OPEN")
        self.assertEqual(runner.upstream_calls(), [])

    def test_checks_registered_asks_the_fork(self) -> None:
        runner = ForkAwareRunner()
        self.assertTrue(
            land_pr._checks_registered(Path("/repo"), _PR_NUMBER, runner, _FORK)
        )
        self.assertEqual(runner.upstream_calls(), [])

    def test_watch_ci_scopes_checks_and_run_calls_to_the_fork(self) -> None:
        runner = ForkAwareRunner()
        runner.failing_run_check = True
        result = land_pr._watch_ci(
            Path("/repo"), _PR_NUMBER, 30, runner, base_slug=_FORK
        )
        self.assertFalse(result["settled"] and not result["failing_checks"])
        # A transient failing check drives `gh run view --log-failed` and
        # `gh run rerun`; run ids are repo-scoped exactly like PR numbers.
        self.assertTrue(runner.called_with_prefix("gh", "run", "view", "9"))
        self.assertEqual(runner.upstream_calls(), [])

    def test_merge_state_guard_reruns_are_scoped_to_the_fork(self) -> None:
        blocked = {
            "state": "OPEN",
            "mergeStateStatus": "BLOCKED",
            "statusCheckRollup": [
                {"name": "build", "conclusion": "SUCCESS", "databaseId": 1},
                {"name": "build", "conclusion": "CANCELLED", "databaseId": 2},
            ],
        }
        runner = ForkAwareRunner()
        real_gh = runner._gh

        def gh(cmd, args, repo):
            if args[:2] == ["pr", "view"]:
                return subprocess.CompletedProcess(
                    list(cmd), 0, json.dumps(blocked), ""
                )
            return real_gh(cmd, args, repo)

        with mock.patch.object(runner, "_gh", side_effect=gh):
            land_pr._merge_state_guard(Path("/repo"), _PR_NUMBER, runner, _FORK)
        self.assertTrue(runner.called_with_prefix("gh", "run", "rerun", "2"))
        self.assertEqual(runner.upstream_calls(), [])

    def test_review_thread_gate_targets_the_fork_owner_and_name(self) -> None:
        with mock.patch.object(land_pr.check_review_threads, "check") as check:
            land_pr._review_thread_gate(
                Path("/repo"), _PR_NUMBER, None, mock.Mock(), _FORK
            )
        self.assertEqual(check.call_args.kwargs["owner"], "me")
        self.assertEqual(check.call_args.kwargs["name"], "fork")

    def test_without_a_fork_slug_calls_are_unchanged(self) -> None:
        runner = FakeRun().script(
            "gh", "pr", "view", "5", stdout=json.dumps({"state": "MERGED"})
        )
        self.assertTrue(land_pr._pr_is_merged(Path("/repo"), 5, runner))
        self.assertNotIn("-R", runner.calls[0])
        with mock.patch.object(land_pr.check_review_threads, "check") as check:
            land_pr._review_thread_gate(Path("/repo"), 5, None, mock.Mock())
        self.assertIsNone(check.call_args.kwargs["owner"])
        self.assertIsNone(check.call_args.kwargs["name"])


class ForkLandPrEndToEndTests(unittest.TestCase):
    """`land_pr()` over a fork checkout whose upstream PR #15 is MERGED."""

    _run = LandPrResumeTests._run

    def _runner(self) -> ForkAwareRunner:
        runner = ForkAwareRunner()
        runner.script("git", "status", "--porcelain")
        runner.script("git", "symbolic-ref", "--short", "HEAD", stdout="feature\n")
        runner.script("git", "config", "--get", "remote.pushDefault", stdout="fork\n")
        runner.script(
            "git", "remote", "get-url", "fork", stdout=f"git@github.com:{_FORK}.git\n"
        )
        runner.script(
            "git",
            "ls-remote",
            "fork",
            "refs/heads/feature",
            stdout=f"{_HEAD}\trefs/heads/feature\n",
        )
        runner.script("git", "rev-parse", "HEAD", stdout=f"{_HEAD}\n")
        return runner

    def test_open_fork_pr_is_not_reported_merged_by_the_upstream_pr(self) -> None:
        runner = self._runner()
        request = _land_request(runner=runner)
        with mock.patch.object(
            land_pr.check_review_threads,
            "check",
            return_value={"checked": True, "blocking": False},
        ):
            outcome, spy, *_ = self._run(
                runner,
                request,
                open_or_update_pull_request={
                    "pr_url": _FORK_PR_URL,
                    "pr_number": _PR_NUMBER,
                    "refused_step": None,
                    "detail": None,
                },
                _watch_ci=None,
                _merge_state_guard=None,
                _review_thread_gate=None,
                _pr_is_merged=None,
            )
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "completed_pr_open")
        self.assertNotEqual(outcome.merge_result, "merged externally")
        self.assertNotIn(
            "completed_and_merged", [a for c in spy.finish_calls() for a in c]
        )
        self.assertEqual(runner.upstream_calls(), [])
