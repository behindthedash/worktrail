#!/usr/bin/env python3
"""Unit tests for the shared PR-landing pipeline (land_pr.py).

Every git/gh subprocess reply is scripted through `FakeRun` (an injected
`Runner`), matched by the argv shape `_git()`/`_gh()` actually build (a `git
-C <repo> ...` prefix is normalized away so scripts stay repo-path-agnostic).
The heavier engines `land_pr()` composes -- `conductor_compile`, `preflight`,
`check_review_threads`, `run_record` -- are mocked directly at the module
boundary `land_pr.py` imports them through: those each have their own test
file, and re-deriving their internals here would just be a second, weaker
copy of those tests.
"""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import land_pr


def _normalize(cmd: list[str]) -> tuple[str, ...]:
    if cmd and cmd[0] == "git" and len(cmd) > 2 and cmd[1] == "-C":
        return ("git", *cmd[3:])
    return tuple(cmd)


class FakeRun:
    """Injected `Runner`: scripted replies keyed by a normalized argv prefix,
    longest-prefix-first so a specific script (e.g. `git push`) wins over a
    more general one. Unscripted commands succeed with empty output -- most
    scenarios only care about a handful of calls, and failing closed on every
    unmentioned call would make every test script the entire pipeline."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._scripts: dict[tuple[str, ...], list[subprocess.CompletedProcess]] = {}

    def script(
        self, *prefix: str, returncode: int = 0, stdout: str = "", stderr: str = ""
    ) -> FakeRun:
        key = tuple(prefix)
        self._scripts.setdefault(key, []).append(
            subprocess.CompletedProcess(list(prefix), returncode, stdout, stderr)
        )
        return self

    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        normalized = _normalize(list(cmd))
        for length in range(len(normalized), 0, -1):
            key = normalized[:length]
            queue = self._scripts.get(key)
            if queue:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        return subprocess.CompletedProcess(list(cmd), 0, "", "")

    def called_with_prefix(self, *prefix: str) -> bool:
        return any(_normalize(c)[: len(prefix)] == tuple(prefix) for c in self.calls)


class RunRecordSpy:
    """Stand-in for `run_record_module.main` -- records every argv and
    fabricates the minimal stdout `_ensure_run_record`/`_run_record_main`
    need (a `start`'s printed `{"path": ...}` line)."""

    def __init__(self, run_path: str = "runs/fake-run.yaml") -> None:
        self.run_path = run_path
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> int:
        self.calls.append(list(argv))
        if argv[0] == "start":
            print(json.dumps({"path": self.run_path}))
        return 0

    def finish_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "finish"]

    def append_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "append"]

    def set_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "set"]

    def scope_review_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[0] == "scope-review"]


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


class RenderPrBodyTests(unittest.TestCase):
    def test_carries_every_standard_section(self) -> None:
        body = land_pr.render_pr_body(
            summary="Did the thing.",
            route="B",
            epic_feature_spec="epic-1/feature-2/spec-3",
            gate_evidence="worktrail-preflight run: PASS",
            risk="high",
            labels=["go:risk-high", "go:no-automerge"],
            automerge_recommendation="ineligible",
        )
        self.assertIn("## Summary\nDid the thing.", body)
        self.assertIn("## Route\nB", body)
        self.assertIn("epic-1/feature-2/spec-3", body)
        self.assertIn("## Pre-PR Gate Evidence\nworktrail-preflight run: PASS", body)
        self.assertIn("## Risk Assessment\nhigh", body)
        self.assertIn("go:risk-high, go:no-automerge", body)
        self.assertIn("## Auto-Merge Recommendation\nineligible", body)

    def test_no_labels_renders_none_placeholder(self) -> None:
        body = land_pr.render_pr_body(
            summary="x",
            route="A",
            epic_feature_spec="none",
            gate_evidence="x",
            risk="low",
            labels=[],
            automerge_recommendation="eligible",
        )
        self.assertIn("## Labels\n(none)", body)


class CommitPendingTests(unittest.TestCase):
    def test_dirty_tree_without_commit_message_refuses(self) -> None:
        runner = FakeRun().script("git", "status", "--porcelain", stdout="M f.py\n")
        result = land_pr._commit_pending(Path("/repo"), None, runner)
        self.assertEqual(result, "dirty_tree")
        self.assertFalse(runner.called_with_prefix("git", "commit"))

    def test_clean_tree_never_touches_commit(self) -> None:
        runner = FakeRun().script("git", "status", "--porcelain", stdout="")
        result = land_pr._commit_pending(Path("/repo"), "msg", runner)
        self.assertIsNone(result)
        self.assertFalse(runner.called_with_prefix("git", "add"))

    def test_dirty_tree_with_commit_message_commits(self) -> None:
        runner = (
            FakeRun()
            .script("git", "status", "--porcelain", stdout="M f.py\n")
            .script("git", "add", "-A")
            .script("git", "commit", "-m")
        )
        result = land_pr._commit_pending(Path("/repo"), "chore: commit", runner)
        self.assertIsNone(result)
        self.assertTrue(runner.called_with_prefix("git", "commit", "-m"))

    def test_failed_git_status_fails_closed(self) -> None:
        runner = FakeRun().script(
            "git", "status", "--porcelain", returncode=1, stderr="index.lock"
        )
        result = land_pr._commit_pending(Path("/repo"), "msg", runner)
        self.assertEqual(result, "dirty_tree")


class EnsureCompileMarkersTests(unittest.TestCase):
    def test_nothing_touched_passes_without_compiling(self) -> None:
        runner = FakeRun().script("git", "fetch", "origin", "main")
        with mock.patch.object(
            land_pr.check_compile_markers, "changed_change_dirs", return_value=[]
        ) as changed:
            refused, detail = land_pr._ensure_compile_markers(
                Path("/repo"), "main", runner
            )
        self.assertIsNone(refused)
        self.assertIsNone(detail)
        changed.assert_called_once()

    def test_failed_fetch_fails_closed(self) -> None:
        runner = FakeRun().script(
            "git", "fetch", "origin", "main", returncode=1, stderr="network"
        )
        refused, detail = land_pr._ensure_compile_markers(Path("/repo"), "main", runner)
        self.assertEqual(refused, "compile_marker")
        self.assertIn("network", str(detail))

    def test_compile_failure_refuses(self) -> None:
        runner = FakeRun().script("git", "fetch", "origin", "main")
        with (
            mock.patch.object(
                land_pr.check_compile_markers,
                "changed_change_dirs",
                return_value=[Path("/repo/openspec/changes/x")],
            ),
            mock.patch.object(
                land_pr.conductor_compile, "main", return_value=1
            ) as compile_main,
        ):
            refused, _detail = land_pr._ensure_compile_markers(
                Path("/repo"), "main", runner
            )
        self.assertEqual(refused, "compile_marker")
        compile_main.assert_called_once()

    def test_stale_marker_after_compile_refuses(self) -> None:
        runner = FakeRun().script("git", "fetch", "origin", "main")
        change_dir = Path("/repo/openspec/changes/x")
        with (
            mock.patch.object(
                land_pr.check_compile_markers,
                "changed_change_dirs",
                return_value=[change_dir],
            ),
            mock.patch.object(land_pr.conductor_compile, "main", return_value=0),
            mock.patch.object(
                land_pr.check_compile_markers,
                "check_marker",
                return_value={"change": "x", "status": "stale"},
            ),
        ):
            refused, detail = land_pr._ensure_compile_markers(
                Path("/repo"), "main", runner
            )
        self.assertEqual(refused, "compile_marker")
        self.assertEqual(detail, [{"change": "x", "status": "stale"}])

    def test_fresh_marker_is_committed(self) -> None:
        runner = (
            FakeRun()
            .script("git", "fetch", "origin", "main")
            .script("git", "add", "--")
            .script(
                "git",
                "diff",
                "--cached",
                "--name-only",
                "--",
                stdout=stdout(".compile-ok"),
            )
            .script("git", "commit", "-m")
        )
        change_dir = Path("/repo/openspec/changes/x")
        with (
            mock.patch.object(
                land_pr.check_compile_markers,
                "changed_change_dirs",
                return_value=[change_dir],
            ),
            mock.patch.object(land_pr.conductor_compile, "main", return_value=0),
            mock.patch.object(
                land_pr.check_compile_markers,
                "check_marker",
                return_value={"change": "x", "status": "ok"},
            ),
            mock.patch.object(
                land_pr.conductor_compile,
                "marker_path",
                return_value=Path("/repo/openspec/changes/x/.compile-ok"),
            ),
        ):
            refused, _detail = land_pr._ensure_compile_markers(
                Path("/repo"), "main", runner
            )
        self.assertIsNone(refused)
        self.assertTrue(runner.called_with_prefix("git", "commit", "-m"))


def stdout(value: str) -> str:
    return value + "\n"


class RunPreflightAndLabelsTests(unittest.TestCase):
    def test_nonzero_exit_refuses(self) -> None:
        with mock.patch.object(land_pr.preflight, "main", return_value=1):
            refused, labels = land_pr._run_preflight_and_labels(
                Path("/repo"), "main", "low", [], "B", None
            )
        self.assertEqual(refused, "preflight")
        self.assertEqual(labels, [])

    def test_systemexit_from_bad_risk_refuses(self) -> None:
        with mock.patch.object(land_pr.preflight, "main", side_effect=SystemExit(2)):
            refused, _labels = land_pr._run_preflight_and_labels(
                Path("/repo"), "main", "not-a-risk", [], "B", None
            )
        self.assertEqual(refused, "preflight")

    def test_unreadable_marker_refuses(self) -> None:
        with (
            mock.patch.object(land_pr.preflight, "main", return_value=0),
            mock.patch.object(land_pr.preflight, "read_marker", return_value=None),
        ):
            refused, _labels = land_pr._run_preflight_and_labels(
                Path("/repo"), "main", "low", [], "B", None
            )
        self.assertEqual(refused, "preflight")

    def test_stale_marker_state_refuses(self) -> None:
        with (
            mock.patch.object(land_pr.preflight, "main", return_value=0),
            mock.patch.object(
                land_pr.preflight,
                "read_marker",
                return_value={"state": "old", "labels": ["go:risk-low"]},
            ),
            mock.patch.object(land_pr.preflight, "tree_state", return_value="new"),
        ):
            refused, _labels = land_pr._run_preflight_and_labels(
                Path("/repo"), "main", "low", [], "B", None
            )
        self.assertEqual(refused, "preflight")

    def test_matching_marker_returns_its_labels(self) -> None:
        with (
            mock.patch.object(land_pr.preflight, "main", return_value=0),
            mock.patch.object(
                land_pr.preflight,
                "read_marker",
                return_value={"state": "s", "labels": ["go:risk-high"]},
            ),
            mock.patch.object(land_pr.preflight, "tree_state", return_value="s"),
        ):
            refused, labels = land_pr._run_preflight_and_labels(
                Path("/repo"), "main", "high", [], "B", None
            )
        self.assertIsNone(refused)
        self.assertEqual(labels, ["go:risk-high"])


class OpenOrUpdatePullRequestTests(unittest.TestCase):
    def test_existing_open_pr_updates_without_creating(self) -> None:
        runner = (
            FakeRun()
            .script(
                "gh",
                "pr",
                "view",
                "feature",
                "--json",
                "url,number,state,labels",
                stdout=json.dumps(
                    {
                        "url": "https://github.com/o/r/pull/9",
                        "number": 9,
                        "state": "OPEN",
                        "labels": [],
                    }
                ),
            )
            .script("gh", "pr", "edit", "9")
            .script(
                "gh",
                "pr",
                "view",
                "https://github.com/o/r/pull/9",
                "--json",
                "labels",
                stdout=json.dumps(
                    {"labels": [{"name": "go:risk-low"}, {"name": "go:no-automerge"}]}
                ),
            )
        )
        with (
            mock.patch.object(land_pr.pr_labels, "ensure_pr_risk_label") as risk_label,
            mock.patch.object(
                land_pr.pr_labels, "ensure_pr_no_automerge_label"
            ) as no_automerge_label,
        ):
            result = land_pr.open_or_update_pull_request(
                Path("/repo"),
                "main",
                "feature",
                "Title",
                "Body",
                "low",
                ["go:risk-low", "go:no-automerge"],
                "B",
                runner,
            )
        self.assertIsNone(result["refused_step"])
        self.assertEqual(result["pr_url"], "https://github.com/o/r/pull/9")
        risk_label.assert_called_once()
        no_automerge_label.assert_called_once()
        self.assertFalse(runner.called_with_prefix("gh", "pr", "create"))

    def test_no_existing_pr_creates_one(self) -> None:
        runner = (
            FakeRun()
            .script(
                "gh",
                "pr",
                "view",
                "feature",
                "--json",
                "url,number,state,labels",
                returncode=1,
                stderr="no pull requests found",
            )
            .script(
                "gh",
                "pr",
                "create",
                stdout="https://github.com/o/r/pull/10\n",
            )
            .script(
                "gh",
                "pr",
                "view",
                "https://github.com/o/r/pull/10",
                "--json",
                "number",
                stdout=json.dumps({"number": 10}),
            )
        )
        result = land_pr.open_or_update_pull_request(
            Path("/repo"),
            "main",
            "feature",
            "Title",
            "Body",
            "low",
            ["go:risk-low"],
            "B",
            runner,
        )
        self.assertIsNone(result["refused_step"])
        self.assertEqual(result["pr_url"], "https://github.com/o/r/pull/10")
        self.assertEqual(result["pr_number"], 10)
        create_calls = [
            c
            for c in runner.calls
            if _normalize(c)[:2] == ("gh", "pr") and "create" in c
        ]
        self.assertEqual(len(create_calls), 1)
        self.assertIn("--label", create_calls[0])
        self.assertIn("go:risk-low", create_calls[0])


class WatchCiTests(unittest.TestCase):
    def test_transient_check_reruns_and_settles(self) -> None:
        runner = (
            FakeRun()
            .script("gh", "pr", "checks", "5", "--json", "name")
            .script("gh", "pr", "checks", "5", "--watch", "--fail-fast", returncode=1)
            .script("gh", "pr", "checks", "5", "--watch", "--fail-fast", returncode=0)
            .script(
                "gh",
                "pr",
                "checks",
                "5",
                "--json",
                "name,bucket,workflowRunId",
                stdout=json.dumps(
                    [
                        {
                            "name": "Initialize containers",
                            "bucket": "fail",
                            "workflowRunId": 123,
                        }
                    ]
                ),
            )
            .script("gh", "run", "view", "123", "--log-failed", stdout="log")
            .script("gh", "run", "rerun", "123", "--failed")
        )
        result = land_pr._watch_ci(Path("/repo"), 5, 30, runner)
        self.assertTrue(result["settled"])
        self.assertFalse(result["budget_exhausted"])
        self.assertTrue(
            runner.called_with_prefix("gh", "run", "rerun", "123", "--failed")
        )

    def test_non_transient_failure_reports_settled_false(self) -> None:
        runner = (
            FakeRun()
            .script("gh", "pr", "checks", "5", "--json", "name")
            .script("gh", "pr", "checks", "5", "--watch", "--fail-fast", returncode=1)
            .script(
                "gh",
                "pr",
                "checks",
                "5",
                "--json",
                "name,bucket,workflowRunId",
                stdout=json.dumps(
                    [{"name": "unit-tests", "bucket": "fail", "workflowRunId": 7}]
                ),
            )
            .script("gh", "run", "view", "7", "--log-failed", stdout="AssertionError")
        )
        result = land_pr._watch_ci(Path("/repo"), 5, 30, runner)
        self.assertFalse(result["settled"])
        self.assertFalse(result["budget_exhausted"])
        self.assertEqual(result["failing_checks"], ["unit-tests"])
        self.assertFalse(runner.called_with_prefix("gh", "run", "rerun"))

    def test_checks_never_registering_exhausts_grace_budget(self) -> None:
        runner = FakeRun().script(
            "gh",
            "pr",
            "checks",
            "5",
            "--json",
            "name",
            returncode=1,
            stderr="no checks reported for this PR",
        )
        with mock.patch.object(land_pr.time, "sleep"):
            result = land_pr._watch_ci(Path("/repo"), 5, 30, runner)
        self.assertFalse(result["settled"])
        self.assertTrue(result["budget_exhausted"])


class MergeStateGuardTests(unittest.TestCase):
    def test_cancelled_success_pair_reruns_up_to_ceiling(self) -> None:
        blocked_payload = json.dumps(
            {
                "state": "OPEN",
                "mergeStateStatus": "BLOCKED",
                "statusCheckRollup": [
                    {"name": "build", "conclusion": "SUCCESS", "databaseId": 1},
                    {"name": "build", "conclusion": "CANCELLED", "databaseId": 2},
                ],
            }
        )
        clean_payload = json.dumps({"state": "OPEN", "mergeStateStatus": "CLEAN"})
        runner = (
            FakeRun()
            .script("gh", "pr", "view", "5", stdout=blocked_payload)
            .script("gh", "pr", "view", "5", stdout=blocked_payload)
            .script("gh", "pr", "view", "5", stdout=clean_payload)
            .script("gh", "run", "rerun", "2")
        )
        result = land_pr._merge_state_guard(Path("/repo"), 5, runner)
        rerun_calls = [
            c for c in runner.calls if _normalize(c)[:3] == ("gh", "run", "rerun")
        ]
        self.assertLessEqual(len(rerun_calls), land_pr.MERGE_STATE_RERUN_MAX)
        self.assertEqual(len(rerun_calls), 2)
        self.assertEqual(result["mergeStateStatus"], "CLEAN")

    def test_unparseable_response_returns_empty_dict(self) -> None:
        runner = FakeRun().script("gh", "pr", "view", "5", returncode=1)
        result = land_pr._merge_state_guard(Path("/repo"), 5, runner)
        self.assertEqual(result, {})


class EnsureRunRecordTests(unittest.TestCase):
    def test_existing_run_path_is_reused(self) -> None:
        spy = RunRecordSpy()
        with mock.patch.object(land_pr.run_record_module, "main", spy):
            result = land_pr._ensure_run_record(
                Path("/repo"), "B", "low", "summary", "runs/existing.yaml"
            )
        self.assertEqual(result, "runs/existing.yaml")
        self.assertEqual(spy.calls, [])

    def test_no_run_path_starts_one(self) -> None:
        spy = RunRecordSpy(run_path="runs/new.yaml")
        with mock.patch.object(land_pr.run_record_module, "main", spy):
            result = land_pr._ensure_run_record(
                Path("/repo"), "B", "low", "summary", None
            )
        self.assertEqual(result, "runs/new.yaml")
        self.assertEqual(spy.calls[0][0], "start")


class LandPrOrchestrationTests(unittest.TestCase):
    """Full `land_pr()` runs with every step patched at the seam it composes
    through, so each test controls exactly the one behavior it names."""

    def _patched(self, **overrides):
        defaults = {
            "_commit_pending": None,
            "_ensure_compile_markers": (None, None),
            "_run_preflight_and_labels": (None, ["go:risk-low"]),
            "_current_branch": "feature",
            "_push_target": ("origin", None),
            "_push": None,
            "open_or_update_pull_request": {
                "pr_url": "https://github.com/o/r/pull/1",
                "pr_number": 1,
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
        }
        defaults.update(overrides)
        patchers = [
            mock.patch.object(land_pr, name, return_value=value)
            for name, value in defaults.items()
        ]
        return patchers

    def _run(self, request, run_record_spy=None, **overrides):
        run_record_spy = run_record_spy or RunRecordSpy()
        patchers = self._patched(**overrides)
        with mock.patch.object(land_pr.run_record_module, "main", run_record_spy):
            for p in patchers:
                p.start()
            try:
                outcome = land_pr.land_pr(request)
            finally:
                for p in patchers:
                    p.stop()
        return outcome, run_record_spy

    def test_invalid_route_refuses_before_touching_anything(self) -> None:
        request = _land_request(route="Z")
        outcome, spy = self._run(request, _commit_pending=None)
        self.assertEqual(outcome.outcome, "refused")
        self.assertEqual(outcome.refused_step, "route")
        self.assertEqual(spy.calls, [])

    def test_dirty_tree_refuses_and_never_pushes(self) -> None:
        request = _land_request()
        with mock.patch.object(land_pr, "_push") as push_mock:
            outcome, _ = self._run(request, _commit_pending="dirty_tree")
        self.assertEqual(outcome.outcome, "refused")
        self.assertEqual(outcome.refused_step, "dirty_tree")
        push_mock.assert_not_called()

    def test_compile_gap_refuses_and_never_pushes(self) -> None:
        request = _land_request()
        with mock.patch.object(land_pr, "_push") as push_mock:
            outcome, _ = self._run(
                request,
                _ensure_compile_markers=("compile_marker", "gap detail"),
            )
        self.assertEqual(outcome.outcome, "refused")
        self.assertEqual(outcome.refused_step, "compile_marker")
        push_mock.assert_not_called()

    def test_preflight_failure_refuses_and_never_pushes(self) -> None:
        request = _land_request()
        with mock.patch.object(land_pr, "_push") as push_mock:
            outcome, _ = self._run(request, _run_preflight_and_labels=("preflight", []))
        self.assertEqual(outcome.outcome, "refused")
        self.assertEqual(outcome.refused_step, "preflight")
        push_mock.assert_not_called()

    def test_non_transient_code_defect_increments_iteration_no_finish(self) -> None:
        request = _land_request()
        with mock.patch.object(
            land_pr, "_load_run_record", return_value={"ci_patch_iterations": 0}
        ):
            outcome, spy = self._run(
                request,
                _watch_ci={
                    "settled": False,
                    "failing_checks": ["unit-tests"],
                    "log_excerpt": "boom",
                    "budget_exhausted": False,
                },
            )
        self.assertEqual(outcome.outcome, "code_defect")
        self.assertEqual(outcome.patch_iteration, 1)
        self.assertEqual(spy.finish_calls(), [])
        self.assertIn(
            ["set", "runs/existing.yaml", "ci_patch_iterations", "1"], spy.calls
        )

    def test_fifth_defect_ceiling_finishes_failed_recoverable(self) -> None:
        request = _land_request()
        with mock.patch.object(
            land_pr, "_load_run_record", return_value={"ci_patch_iterations": 4}
        ):
            outcome, spy = self._run(
                request,
                _watch_ci={
                    "settled": False,
                    "failing_checks": ["unit-tests"],
                    "log_excerpt": "boom",
                    "budget_exhausted": False,
                },
            )
        self.assertEqual(outcome.outcome, "ceiling")
        self.assertEqual(outcome.final_status, "failed_recoverable")
        finish_calls = spy.finish_calls()
        self.assertEqual(len(finish_calls), 1)
        self.assertIn("failed_recoverable", finish_calls[0])

    def test_merged_state_finishes_completed_and_merged(self) -> None:
        request = _land_request()
        outcome, spy = self._run(
            request,
            _merge_state_guard={"state": "MERGED", "mergeStateStatus": "CLEAN"},
        )
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "completed_and_merged")
        finish_calls = spy.finish_calls()
        self.assertEqual(len(finish_calls), 1)
        self.assertIn("completed_and_merged", finish_calls[0])

    def test_review_threads_blocking_stops_before_finish(self) -> None:
        request = _land_request()
        outcome, spy = self._run(
            request,
            _merge_state_guard={"state": "MERGED", "mergeStateStatus": "CLEAN"},
            _review_thread_gate={
                "checked": True,
                "blocking": True,
                "unaddressed": ["thread-1"],
            },
        )
        self.assertEqual(outcome.outcome, "review_threads_blocking")
        self.assertEqual(spy.finish_calls(), [])

    def test_automerge_armed_names_mechanism(self) -> None:
        request = _land_request()
        outcome, _ = self._run(
            request,
            _merge_state_guard={
                "state": "OPEN",
                "mergeStateStatus": "CLEAN",
                "autoMergeRequest": {
                    "mergeMethod": "squash",
                    "enabledBy": {"login": "alice"},
                },
            },
        )
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "completed_pr_open")
        self.assertIn("auto-merge armed (squash) by alice", outcome.merge_result)

    def test_checkpoint_true_appends_decision_instead_of_finishing(self) -> None:
        request = _land_request(checkpoint=True)
        outcome, spy = self._run(request)
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(spy.finish_calls(), [])
        self.assertEqual(len(spy.append_calls()), 1)
        self.assertIn("decisions", spy.append_calls()[0])

    def test_run_none_starts_a_run_record_and_returns_it(self) -> None:
        request = _land_request(run=None)
        spy = RunRecordSpy(run_path="runs/brand-new.yaml")
        outcome, spy = self._run(request, run_record_spy=spy)
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.run, "runs/brand-new.yaml")
        self.assertEqual(spy.calls[0][0], "start")

    def test_blocked_merge_state_after_gates_lands_as_blocked_product_decision(
        self,
    ) -> None:
        request = _land_request()
        outcome, spy = self._run(
            request,
            _merge_state_guard={"state": "OPEN", "mergeStateStatus": "BLOCKED"},
        )
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(outcome.final_status, "blocked_product_decision")
        finish_calls = spy.finish_calls()
        self.assertEqual(len(finish_calls), 1)

    def _assert_one_scope_review_before_finish(self, spy: RunRecordSpy) -> None:
        scope_calls = spy.scope_review_calls()
        self.assertEqual(len(scope_calls), 1)
        self.assertEqual(scope_calls[0][1], spy.run_path)
        self.assertIn("--item", scope_calls[0])
        self.assertIn("queue-triage apply brief-1", scope_calls[0])
        self.assertIn("complete", scope_calls[0])
        self.assertIn("--evidence", scope_calls[0])
        evidence = scope_calls[0][scope_calls[0].index("--evidence") + 1]
        self.assertIn("on feature -> https://github.com/o/r/pull/1", evidence)
        kinds = [c[0] for c in spy.calls]
        self.assertLess(kinds.index("scope-review"), kinds.index("finish"))

    def test_run_none_open_branch_records_scope_review_before_finish(self) -> None:
        request = _land_request(run=None, request_summary="queue-triage apply brief-1")
        outcome, spy = self._run(request, run_record_spy=RunRecordSpy("runs/n.yaml"))
        self.assertEqual(outcome.final_status, "completed_pr_open")
        self._assert_one_scope_review_before_finish(spy)

    def test_run_none_merged_branch_records_scope_review_before_finish(
        self,
    ) -> None:
        request = _land_request(run=None, request_summary="queue-triage apply brief-1")
        outcome, spy = self._run(
            request,
            run_record_spy=RunRecordSpy("runs/n.yaml"),
            _merge_state_guard={"state": "MERGED", "mergeStateStatus": "CLEAN"},
        )
        self.assertEqual(outcome.final_status, "completed_and_merged")
        self._assert_one_scope_review_before_finish(spy)

    def test_run_none_blocked_branch_records_scope_review_before_finish(
        self,
    ) -> None:
        request = _land_request(run=None, request_summary="queue-triage apply brief-1")
        outcome, spy = self._run(
            request,
            run_record_spy=RunRecordSpy("runs/n.yaml"),
            _merge_state_guard={"state": "OPEN", "mergeStateStatus": "BLOCKED"},
        )
        self.assertEqual(outcome.final_status, "blocked_product_decision")
        self._assert_one_scope_review_before_finish(spy)

    def test_run_none_checkpoint_mode_records_no_scope_review(self) -> None:
        request = _land_request(run=None, checkpoint=True)
        outcome, spy = self._run(request)
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(spy.scope_review_calls(), [])
        self.assertEqual(spy.finish_calls(), [])

    def test_caller_supplied_run_records_no_scope_review(self) -> None:
        request = _land_request()
        outcome, spy = self._run(request)
        self.assertEqual(outcome.outcome, "landed")
        self.assertEqual(spy.scope_review_calls(), [])
        self.assertEqual(len(spy.finish_calls()), 1)

    def test_finish_systemexit_string_surfaces_as_ceiling_detail(self) -> None:
        class GateRefusingSpy(RunRecordSpy):
            def __call__(self, argv: list[str]) -> int:
                if argv[0] == "finish":
                    self.calls.append(list(argv))
                    raise SystemExit("scope_completeness_gate: 1 item(s) unreviewed")
                return super().__call__(argv)

        request = _land_request(run=None)
        outcome, spy = self._run(request, run_record_spy=GateRefusingSpy())
        self.assertEqual(outcome.outcome, "ceiling")
        self.assertEqual(outcome.final_status, "failed_recoverable")
        self.assertEqual(
            outcome.merge_result, "PR open but run record could not be completed"
        )
        self.assertIn("scope_completeness_gate: 1 item(s) unreviewed", outcome.detail)
        self.assertEqual(len(spy.finish_calls()), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
