#!/usr/bin/env python3
"""Tests for `land_pr()`'s step-0 resume fast path (`_resume_state`).

Same fake-runner conventions as `test_land_pr.py` (`FakeRun`/`RunRecordSpy`
are reused from there rather than re-declared): every git/gh reply is
scripted by the argv shape `_git()`/`_gh()` build. Unlike
`LandPrOrchestrationTests`, these tests deliberately do NOT patch
`_resume_state`'s own probes -- the probe sequence is what is under test --
and instead patch only the heavy steps downstream of it.
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from worktrail.router import land_pr

from .test_land_pr import FakeRun, RunRecordSpy, _land_request

_HEAD = "abc123def456"
_PR_URL = "https://github.com/o/r/pull/7"


def _resumable_runner(
    *,
    state: str = "OPEN",
    status_stdout: str = "",
    status_rc: int = 0,
    remote_sha: str = _HEAD,
    ls_remote_rc: int = 0,
    pr_view_rc: int = 0,
    pr_view_stdout: str | None = None,
) -> FakeRun:
    if pr_view_stdout is None:
        pr_view_stdout = json.dumps({"url": _PR_URL, "number": 7, "state": state})
    runner = FakeRun()
    runner.script(
        "git", "status", "--porcelain", returncode=status_rc, stdout=status_stdout
    )
    runner.script("git", "symbolic-ref", "--short", "HEAD", stdout="feature\n")
    runner.script("git", "config", "--get", "remote.pushDefault", returncode=1)
    runner.script(
        "git",
        "ls-remote",
        "origin",
        "refs/heads/feature",
        returncode=ls_remote_rc,
        stdout=f"{remote_sha}\trefs/heads/feature\n" if remote_sha else "",
    )
    runner.script("git", "rev-parse", "HEAD", stdout=f"{_HEAD}\n")
    runner.script(
        "gh", "pr", "view", "feature", returncode=pr_view_rc, stdout=pr_view_stdout
    )
    return runner


class ResumeStateProbeTests(unittest.TestCase):
    def _state(self, runner: FakeRun):
        return land_pr._resume_state(land_pr.Path("/tmp/repo"), _land_request(), runner)

    def test_clean_matching_tip_with_pr_is_a_hit(self) -> None:
        state = self._state(_resumable_runner())
        assert state is not None
        self.assertEqual(state["branch"], "feature")
        self.assertEqual(state["pr_url"], _PR_URL)
        self.assertEqual(state["pr_number"], 7)
        self.assertEqual(state["state"], "OPEN")
        self.assertEqual(state["head_sha"], _HEAD)

    def test_dirty_tree_declines(self) -> None:
        self.assertIsNone(self._state(_resumable_runner(status_stdout="M f.py\n")))

    def test_failed_status_declines(self) -> None:
        self.assertIsNone(self._state(_resumable_runner(status_rc=1)))

    def test_remote_tip_differing_from_head_declines(self) -> None:
        self.assertIsNone(self._state(_resumable_runner(remote_sha="0" * 12)))

    def test_no_remote_branch_declines(self) -> None:
        self.assertIsNone(self._state(_resumable_runner(remote_sha="")))

    def test_failing_ls_remote_declines(self) -> None:
        self.assertIsNone(self._state(_resumable_runner(ls_remote_rc=1)))

    def test_no_pr_for_branch_declines(self) -> None:
        self.assertIsNone(self._state(_resumable_runner(pr_view_rc=1)))

    def test_unparseable_pr_view_declines(self) -> None:
        self.assertIsNone(self._state(_resumable_runner(pr_view_stdout="not json")))

    def test_pr_view_missing_fields_declines(self) -> None:
        self.assertIsNone(
            self._state(_resumable_runner(pr_view_stdout=json.dumps({"state": "OPEN"})))
        )

    def test_fork_slug_is_passed_to_gh_pr_view(self) -> None:
        runner = _resumable_runner()
        runner._scripts[("git", "config", "--get", "remote.pushDefault")] = []
        runner.script("git", "config", "--get", "remote.pushDefault", stdout="fork\n")
        runner.script(
            "git", "remote", "get-url", "fork", stdout="git@github.com:me/r.git\n"
        )
        runner.script(
            "git",
            "ls-remote",
            "fork",
            "refs/heads/feature",
            stdout=f"{_HEAD}\trefs/heads/feature\n",
        )
        state = land_pr._resume_state(
            land_pr.Path("/tmp/repo"), _land_request(), runner
        )
        assert state is not None
        self.assertEqual(state["base_slug"], "me/r")
        self.assertTrue(runner.called_with_prefix("gh", "pr", "view", "feature"))
        view_call = next(
            c for c in runner.calls if c[:4] == ["gh", "pr", "view", "feature"]
        )
        self.assertIn("-R", view_call)
        self.assertIn("me/r", view_call)


class LandPrResumeTests(unittest.TestCase):
    """`land_pr()` end-to-end over the real probe sequence, with only the
    heavy post-resume steps patched."""

    def _run(self, runner: FakeRun, request=None, **overrides):
        request = request or _land_request(runner=runner)
        spy = RunRecordSpy()
        defaults = {
            "open_or_update_pull_request": {
                "pr_url": _PR_URL,
                "pr_number": 7,
                "refused_step": None,
                "detail": None,
            },
            "_watch_ci": {
                "settled": True,
                "failing_checks": [],
                "log_excerpt": "",
                "budget_exhausted": False,
            },
            "_merge_state_guard": {"state": "OPEN", "mergeStateStatus": "CLEAN"},
            "_review_thread_gate": {"checked": True, "blocking": False},
            "_pr_is_merged": False,
        }
        defaults.update(overrides)
        # A `None` override means "do not patch this here" -- the test
        # supplies its own outer patch for that seam.
        patchers = [
            mock.patch.object(land_pr, name, return_value=value)
            for name, value in defaults.items()
            if value is not None
        ]
        with (
            mock.patch.object(land_pr.run_record_module, "main", spy),
            mock.patch.object(
                land_pr.pre_pr_gate,
                "resolve_pr_labels",
                return_value=(["go:risk-low"], True, ""),
            ),
            mock.patch.object(land_pr, "load_policy", return_value={}),
            mock.patch.object(land_pr, "preflight") as preflight_mock,
            mock.patch.object(
                land_pr, "_commit_pending", return_value=None
            ) as commit_mock,
            mock.patch.object(land_pr, "_push", return_value=None) as push_mock,
            mock.patch.object(
                land_pr, "_ensure_compile_markers", return_value=(None, None)
            ),
        ):
            for p in patchers:
                p.start()
            try:
                outcome = land_pr.land_pr(request)
            finally:
                for p in patchers:
                    p.stop()
        return outcome, spy, preflight_mock, commit_mock, push_mock

    def test_open_pr_skips_commit_push_and_preflight_but_still_updates_and_watches(
        self,
    ) -> None:
        runner = _resumable_runner()
        with mock.patch.object(land_pr, "open_or_update_pull_request") as pr_mock:
            pr_mock.return_value = {
                "pr_url": _PR_URL,
                "pr_number": 7,
                "refused_step": None,
                "detail": None,
            }
            with mock.patch.object(land_pr, "_watch_ci") as watch_mock:
                watch_mock.return_value = {
                    "settled": True,
                    "failing_checks": [],
                    "log_excerpt": "",
                    "budget_exhausted": False,
                }
                outcome, _spy, preflight_mock, commit_mock, push_mock = self._run(
                    runner,
                    open_or_update_pull_request=None,
                    _watch_ci=None,
                )
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "completed_pr_open")
        self.assertEqual(outcome.pr_url, _PR_URL)
        commit_mock.assert_not_called()
        push_mock.assert_not_called()
        preflight_mock.main.assert_not_called()
        self.assertFalse(runner.called_with_prefix("git", "commit"))
        self.assertFalse(runner.called_with_prefix("git", "push"))
        pr_mock.assert_called_once()
        watch_mock.assert_called_once()
        body = pr_mock.call_args[0][4]
        self.assertIn("already pushed", body)

    def test_merged_pr_lands_completed_and_merged_without_creating_a_pr(self) -> None:
        runner = _resumable_runner(state="MERGED")
        with mock.patch.object(land_pr, "open_or_update_pull_request") as pr_mock:
            outcome, spy, _preflight, commit_mock, push_mock = self._run(
                runner, open_or_update_pull_request=None
            )
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "completed_and_merged")
        self.assertEqual(outcome.pr_url, _PR_URL)
        pr_mock.assert_not_called()
        commit_mock.assert_not_called()
        push_mock.assert_not_called()
        self.assertFalse(runner.called_with_prefix("gh", "pr", "create"))
        self.assertEqual([c[0] for c in spy.finish_calls()], ["finish"])
        self.assertIn("completed_and_merged", spy.finish_calls()[0])
        self.assertTrue(spy.set_calls())

    def test_closed_unmerged_pr_is_a_ceiling(self) -> None:
        runner = _resumable_runner(state="CLOSED")
        with mock.patch.object(land_pr, "open_or_update_pull_request") as pr_mock:
            outcome, _spy, _preflight, commit_mock, push_mock = self._run(
                runner, open_or_update_pull_request=None
            )
        self.assertEqual(outcome.outcome, "ceiling")
        self.assertEqual(outcome.refused_step, "pr_closed")
        self.assertIn(_PR_URL, outcome.detail or "")
        pr_mock.assert_not_called()
        commit_mock.assert_not_called()
        push_mock.assert_not_called()
        self.assertFalse(runner.called_with_prefix("gh", "pr", "create"))

    def _assert_full_pipeline(self, runner: FakeRun) -> None:
        outcome, _spy, _preflight, commit_mock, push_mock = self._run(
            runner, _run_preflight_and_labels=(None, ["go:risk-low"])
        )
        commit_mock.assert_called_once()
        push_mock.assert_called_once()
        self.assertEqual(outcome.outcome, "landed")

    def test_dirty_tree_falls_back_to_full_pipeline(self) -> None:
        self._assert_full_pipeline(_resumable_runner(status_stdout="M f.py\n"))

    def test_remote_tip_mismatch_falls_back_to_full_pipeline(self) -> None:
        self._assert_full_pipeline(_resumable_runner(remote_sha="9" * 12))

    def test_no_pr_falls_back_to_full_pipeline(self) -> None:
        self._assert_full_pipeline(_resumable_runner(pr_view_rc=1))

    def test_failing_ls_remote_falls_back_to_full_pipeline(self) -> None:
        self._assert_full_pipeline(_resumable_runner(ls_remote_rc=1))

    def test_unparseable_pr_view_falls_back_to_full_pipeline(self) -> None:
        self._assert_full_pipeline(_resumable_runner(pr_view_stdout="{oops"))


if __name__ == "__main__":
    unittest.main()
