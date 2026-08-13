#!/usr/bin/env python3
"""Unit tests for multi-group reconcile-before-create (integrate.py).

Tests the reconciliation behavior when resuming a run with existing PRs
and remote branches. Follows the FakeRun pattern from test_verify.py.

Run: python3 test_integrate.py
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from collections import deque, namedtuple
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import coordinator
from worktrail.orchestrator import integrate
import tempfile

Proc = namedtuple("Proc", "returncode stdout stderr")


def mock_group(name, tasks, depends_on=None):
    """Create a mock group dict."""
    return {"name": name, "tasks": tasks, "reqs": [], "depends_on": depends_on or []}


def mock_task(task_id, status="done"):
    """Create a mock task dict."""
    return {"id": task_id, "status": status, "kind": "impl"}


def integrate_groups(
    repo,
    spec_id,
    tasks,
    remote,
    run_id,
    base,
    cleanup=False,
    journal_path=None,
    smoke_cmd=None,
    assembly_resolve_spawn=None,
    pr_labels=None,
    route=None,
    gates="",
    migration_patterns=None,
):
    """Test driver: integrate every planned group through integrate_one, in
    group order, exactly the way `_pipeline_scheduler._integrate_verify_group`
    does (spec-carrier selection, strip_spec_folder rule, per-group journal
    records, integrate_complete marker). Replaces the deleted production
    `integrate_groups()` loop (scheduler-consolidation stage 2) so the
    integrate-layer tests below keep exercising integrate_one's multi-group
    reconcile semantics through one shared loop.

    Returns the same (prs, group_branch, quarantined) tuple finish_real did.
    """
    assert cleanup is False, "the test driver never supports the cleanup branch"
    groups = coordinator.plan_groups(tasks, migration_patterns=migration_patterns or ())
    status = {t["id"]: t.get("status", "done") for t in tasks}
    group_branch: dict = {}
    quarantined: dict = {}
    prs = []

    # Spec-folder ownership (mirrors _pipeline_scheduler): only the first
    # independent group carries docs/specs/<spec_id>/.
    spec_carrier = next((g["name"] for g in groups if not g.get("depends_on")), None)
    if spec_carrier is None and groups:
        spec_carrier = groups[0]["name"]

    for g in groups:
        result = integrate.integrate_one(
            g, repo, spec_id, tasks, remote, run_id, base,
            journal_path, status, group_branch, quarantined,
            strip_spec_folder=not g.get("depends_on") and g["name"] != spec_carrier,
            smoke_cmd=smoke_cmd,
            assembly_resolve_spawn=assembly_resolve_spawn,
            pr_labels=pr_labels,
            route=route,
            gates=gates,
        )
        if result is not None:
            prs.append(result)

    integrate._mark_integrate_complete_if_terminal(journal_path, groups, tasks)
    return prs, group_branch, quarantined


class FakeRun:
    """Scriptable git+gh runner for testing integrate.py.

    Intercepts subprocess.run calls and returns scripted Proc objects.
    Tracks all calls for verification.
    """

    def __init__(
        self,
        pr_view_responses=None,
        ls_remote_responses=None,
        remote_url="https://github.com/owner/repo.git",
    ):
        """Initialize with mocked responses.

        Args:
            pr_view_responses: dict mapping branch name -> list of pr view dicts
                              (consumed in order; last repeats)
            ls_remote_responses: dict mapping branch name -> bool (True = exists)
            remote_url: mocked git remote URL
        """
        self.pr_view_responses = {k: deque(v) for k, v in (pr_view_responses or {}).items()}
        self.ls_remote_responses = ls_remote_responses or {}
        self.remote_url = remote_url
        self.calls = []

    def __call__(self, *args, **kwargs):
        """Handle subprocess.run or _git calls.

        When called as subprocess.run, first arg is cmd list.
        When called as _git mock, first arg is repo path, rest are git args.
        """
        # Determine if this is a _git call (first arg is Path) or subprocess.run (first arg is list)
        if args and isinstance(args[0], (str, Path)):
            # _git call: repo path is first arg, git args follow
            cmd = list(args[1:])
        else:
            # subprocess.run call: cmd is the first arg (a list)
            cmd = args[0] if args else []

        self.calls.append(cmd)

        # git ls-remote origin <branch>
        if cmd[:3] == ["ls-remote", "origin"] or cmd[:2] == ["ls-remote", "origin"]:
            branch = cmd[3] if len(cmd) > 3 else cmd[2] if len(cmd) > 2 else None
            if branch in self.ls_remote_responses and self.ls_remote_responses[branch]:
                return Proc(0, f"abc123\trefs/heads/{branch}\n", "")
            return Proc(1, "", "")

        # gh pr view <branch> --json number,state,url
        if cmd[:3] == ["gh", "pr", "view"] or (
            len(cmd) >= 3 and cmd[0] == "gh" and cmd[1] == "pr" and cmd[2] == "view"
        ):
            branch = cmd[3] if len(cmd) > 3 else None
            responses = self.pr_view_responses.get(branch)
            if not responses:
                return Proc(1, "", "no pull requests found for branch")
            # Consume one response (or repeat the last)
            resp = responses[0] if len(responses) == 1 else responses.popleft()
            return Proc(0, json.dumps(resp), "")

        # gh pr create
        if cmd[:3] == ["gh", "pr", "create"]:
            return Proc(0, "https://github.com/owner/repo/pull/123\n", "")

        # git remote get-url
        if "remote" in cmd and "get-url" in cmd:
            return Proc(0, self.remote_url, "")

        # git checkout (for branch creation)
        if "checkout" in cmd:
            return Proc(0, "", "")

        # git merge
        if "merge" in cmd:
            return Proc(0, "", "")

        # git push
        if "push" in cmd:
            return Proc(0, "", "")

        # pre_pr_gate --labels-only call
        if len(cmd) >= 6 and "--labels-only" in cmd:
            # Return the risk-level label as the pre-PR gate would
            for i, arg in enumerate(cmd):
                if arg == "--risk" and i + 1 < len(cmd):
                    risk = cmd[i + 1]
                    return Proc(0, f"go:risk-{risk}\n", "")
            return Proc(0, "", "")

        # Default: success
        return Proc(0, "", "")

    def find_calls(self, *prefix):
        """Find all calls matching the given prefix."""
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


class WriteGroupJournalQuarantineReason(unittest.TestCase):
    """_write_group_journal persists quarantine_reason only when non-empty, and
    a later write for the same group (e.g. re-integrated after --fresh) drops
    any stale reason since the record is rebuilt fresh each call."""

    def test_reason_omitted_when_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            integrate._write_group_journal(journal_path, "base", "https://x/pr/1", "b", "OPEN")
            record = json.loads(Path(journal_path).read_text())["groups"]["base"]
            self.assertNotIn("quarantine_reason", record)

    def test_reason_persisted_when_provided(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            integrate._write_group_journal(
                journal_path, "base", "", "b", "QUARANTINED",
                integrate.QUARANTINE_BUDGET_EXHAUSTED,
            )
            record = json.loads(Path(journal_path).read_text())["groups"]["base"]
            self.assertEqual(record["quarantine_reason"], integrate.QUARANTINE_BUDGET_EXHAUSTED)

    def test_reason_dropped_on_transition_to_non_quarantined_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            integrate._write_group_journal(
                journal_path, "base", "", "b", "QUARANTINED",
                integrate.QUARANTINE_BUDGET_EXHAUSTED,
            )
            integrate._write_group_journal(journal_path, "base", "https://x/pr/1", "b", "OPEN")
            record = json.loads(Path(journal_path).read_text())["groups"]["base"]
            self.assertNotIn("quarantine_reason", record)


class ReuseExistingOpenPR(unittest.TestCase):
    """AC-005, AC-013: Reuse existing open PR instead of creating new one."""

    def test_reuse_open_pr(self):
        """Verify the integrate loop reuses an OPEN PR and does not call gh pr create."""
        pr_view = {
            "full-123/base": [
                {"number": 42, "state": "OPEN", "url": "https://github.com/owner/repo/pull/42"}
            ]
        }
        ls_remote = {}  # no existing remote branch for this test

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, gb, quarantined = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "full-123",
                        "main",
                        cleanup=False,
                    )

                    # Should reuse the existing PR
                    self.assertEqual(len(prs), 1)
                    name, target, url = prs[0]
                    self.assertEqual(name, "base")
                    self.assertIn("42", url)  # PR number should appear

                    # Should NOT call gh pr create for this branch
                    create_calls = run.find_calls("gh", "pr", "create")
                    self.assertEqual(
                        len(create_calls), 0, "Should not call gh pr create for existing OPEN PR"
                    )

    def test_reused_draft_pr_is_marked_ready_before_reuse(self):
        """A resumed run must not leave a draft PR outside auto-merge automation."""
        pr_view = {
            "run-draft/base": [
                {
                    "number": 43,
                    "state": "OPEN",
                    "isDraft": True,
                    "url": "https://github.com/owner/repo/pull/43",
                }
            ]
        }
        run = FakeRun(pr_view_responses=pr_view)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    mock_groups.return_value = [mock_group("base", ["T001"])]
                    prs, _, _ = integrate_groups(
                        Path("/repo"), "spec-001", [mock_task("T001")], "origin",
                        "run-draft", "main", cleanup=False,
                    )

        self.assertEqual(len(prs), 1)
        ready_calls = run.find_calls("gh", "pr", "ready")
        self.assertEqual(len(ready_calls), 1)
        self.assertEqual(ready_calls[0][3], "43")

    def test_open_pr_with_existing_remote_branch(self):
        """Verify existing remote branch + OPEN PR are both reused."""
        pr_view = {
            "full-456/base": [
                {"number": 50, "state": "OPEN", "url": "https://github.com/owner/repo/pull/50"}
            ]
        }
        ls_remote = {"full-456/base": True}  # branch exists on remote

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "full-456",
                        "main",
                        cleanup=False,
                    )

                    # Verify the existing PR is reused
                    self.assertEqual(len(prs), 1)
                    name, target, url = prs[0]
                    self.assertEqual(name, "base")
                    self.assertIn("50", url)

                    # Verify no checkout -B (force-reset) is called for this branch
                    checkout_calls = [
                        c for c in run.calls if "checkout" in c and "full-456/base" in c
                    ]
                    self.assertEqual(
                        len(checkout_calls), 0, "Should not force-reset existing remote branch"
                    )


class SkipMergedPRs(unittest.TestCase):
    """AC-007: Skip re-integration for groups with MERGED PRs."""

    def test_merged_pr_skipped(self):
        """Verify the integrate loop skips groups with MERGED PRs."""
        pr_view = {
            "full-789/base": [
                {"number": 30, "state": "MERGED", "url": "https://github.com/owner/repo/pull/30"}
            ]
        }
        ls_remote = {}

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "full-789",
                        "main",
                        cleanup=False,
                    )

                    # MERGED PR should NOT appear in prs list (already integrated)
                    self.assertEqual(len(prs), 0, "MERGED PRs should not be added to prs list")

                    # Verify no checkout -B or gh pr create for this group
                    checkout_calls = [
                        c for c in run.calls if "checkout" in c and "full-789/base" in c
                    ]
                    create_calls = run.find_calls("gh", "pr", "create")
                    self.assertEqual(len(checkout_calls), 0)
                    self.assertEqual(len(create_calls), 0)


class NoExistingBranchOrPR(unittest.TestCase):
    """Regression: Normal create flow when no existing branch/PR (REQ-008)."""

    def test_create_when_no_pr_exists(self):
        """Verify the integrate loop creates PR normally when gh pr view returns error."""
        pr_view = {}  # empty = pr view fails
        ls_remote = {}  # no remote branch

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-new",
                        "main",
                        cleanup=False,
                    )

                    # Should create PR normally
                    self.assertEqual(len(prs), 1)
                    create_calls = run.find_calls("gh", "pr", "create")
                    self.assertEqual(
                        len(create_calls), 1, "Should call gh pr create when no PR exists"
                    )
                    self.assertNotIn(
                        "--draft", create_calls[0],
                        "orchestrator-created PRs must be ready for auto-merge eligibility",
                    )

    def test_closed_pr_creates_new(self):
        """Verify CLOSED PR is treated as absent (new PR created)."""
        pr_view = {
            "run-old/base": [
                {"number": 1, "state": "CLOSED", "url": "https://github.com/owner/repo/pull/1"}
            ]
        }
        ls_remote = {}

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-old",
                        "main",
                        cleanup=False,
                    )

                    # Should create a new PR (CLOSED is treated as no PR)
                    self.assertEqual(len(prs), 1)
                    create_calls = run.find_calls("gh", "pr", "create")
                    self.assertEqual(len(create_calls), 1)


class FailedLabelAddDoesNotCorruptPrUrl(unittest.TestCase):
    """brief 20260723-102500 Bug 1: `gh pr create --label X` fails the WHOLE
    command (no PR created, non-zero exit) on an unresolvable label, printing
    e.g. "could not add label: 'go:risk-medium' not found" on stderr with
    empty stdout. The old code took `(stdout or stderr)`'s last line
    unconditionally and journaled it AS `pr_url` with state OPEN -- a bogus
    non-URL value that a later --pipeline resume then reads back as truthy
    (see PipelineReIntegrateTest). integrate_one must instead quarantine the
    group and record an EMPTY pr_url."""

    def _failing_run(self):
        """A side_effect callable (NOT relying on instance __call__ override --
        Python special-method lookup bypasses an instance-assigned __call__)
        that fails only `gh pr create`, delegating everything else to a real
        FakeRun."""
        run = FakeRun(pr_view_responses={}, ls_remote_responses={})

        def call(*args, **kwargs):
            cmd = args[0] if args and not isinstance(args[0], (str, Path)) else list(args[1:])
            if cmd[:3] == ["gh", "pr", "create"]:
                return Proc(1, "", "could not add label: 'go:risk-medium' not found")
            return run(*args, **kwargs)

        return call

    def test_integrate_one_quarantines_instead_of_recording_error_as_pr_url(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            run = self._failing_run()
            group = mock_group("base", ["T001"])
            quarantined: dict = {}

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.integrate_one(
                        group,
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-fl",
                        "main",
                        journal_path,
                        {"T001": "done"},
                        {},
                        quarantined,
                    )

            self.assertIsNone(result, "a failed PR create must not return a PR tuple")
            self.assertIn("base", quarantined)
            self.assertIn("could not add label", quarantined["base"])

            journal = json.loads(Path(journal_path).read_text())
            record = journal["groups"]["base"]
            self.assertEqual(record["pr_url"], "", "pr_url must stay empty, never the error text")
            self.assertNotIn("could not add label", record["pr_url"])
            self.assertEqual(record["state"], "QUARANTINED")
            self.assertEqual(record["quarantine_reason"], integrate.QUARANTINE_INTEGRATION_ERROR)

    def test_group_loop_quarantines_group_on_label_failure(self):
        """End-to-end via the integrate loop: no PR recorded, group quarantined."""
        run = self._failing_run()

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, _gb, quarantined = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-fl2",
                        "main",
                        cleanup=False,
                    )

                    self.assertEqual(prs, [], "no PR should be recorded for the failed group")
                    self.assertIn("base", quarantined)


class TransientGhFailureRetry(unittest.TestCase):
    """brief 20260724-142000 item 1: a single transient GitHub failure (GraphQL
    "Something went wrong" / HTTP 5xx / timeout / connection error) on the
    integrate tail's gh calls must be retried instead of quarantining a group
    whose implement, review, merge, and smoke all already succeeded (the
    datalena 097-app-shell run lost a 10-task group to exactly one such 500 on
    `gh pr create`). Deterministic failures (validation errors, unresolvable
    labels, "already exists", auth) keep the existing single-shot behavior."""

    TRANSIENT_ERR = "GraphQL: Something went wrong while executing your query. (HTTP 500)"

    def _flaky(self, fail_prefix, n_failures, stderr, base_run=None):
        """Side-effect callable failing the first `n_failures` calls matching
        `fail_prefix` with `stderr`, delegating everything else to a FakeRun.
        Returns (callable, fake_run) so tests can inspect fake_run.calls."""
        run = base_run or FakeRun(pr_view_responses={}, ls_remote_responses={})
        remaining = {"n": n_failures}

        def call(*args, **kwargs):
            cmd = args[0] if args and not isinstance(args[0], (str, Path)) else list(args[1:])
            if cmd[: len(fail_prefix)] == list(fail_prefix) and remaining["n"] > 0:
                remaining["n"] -= 1
                run.calls.append(cmd)  # keep find_calls() accounting complete
                return Proc(1, "", stderr)
            return run(*args, **kwargs)

        return call, run

    def _integrate(self, side_effect, journal_path, sleeps, run_id="run-tr"):
        group = mock_group("base", ["T001"])
        quarantined: dict = {}
        with patch("worktrail.orchestrator.integrate._retry_sleep", new=sleeps.append):
            with patch("worktrail.orchestrator.integrate._git", side_effect=side_effect):
                with patch(
                    "worktrail.orchestrator.integrate.subprocess.run", side_effect=side_effect
                ):
                    result = integrate.integrate_one(
                        group,
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        run_id,
                        "main",
                        journal_path,
                        {"T001": "done"},
                        {},
                        quarantined,
                    )
        return result, quarantined

    def test_pr_create_transient_failure_retries_then_succeeds(self):
        """One GraphQL 500 on `gh pr create`, then success: the group must get
        its PR recorded (state OPEN, real URL) and must NOT be quarantined."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            call, run = self._flaky(["gh", "pr", "create"], 1, self.TRANSIENT_ERR)
            sleeps: list = []

            result, quarantined = self._integrate(call, journal_path, sleeps)

            self.assertEqual(quarantined, {}, "transient failure must not quarantine")
            self.assertIsNotNone(result)
            self.assertEqual(result[2], "https://github.com/owner/repo/pull/123")
            self.assertEqual(len(run.find_calls("gh", "pr", "create")), 2)
            self.assertEqual(sleeps, [integrate.GH_TRANSIENT_BACKOFF_S])

            record = json.loads(Path(journal_path).read_text())["groups"]["base"]
            self.assertEqual(record["state"], "OPEN")
            self.assertEqual(record["pr_url"], "https://github.com/owner/repo/pull/123")

    def test_pr_create_deterministic_failure_not_retried(self):
        """A deterministic failure (unresolvable label) quarantines immediately:
        exactly one attempt, no sleep -- existing behavior unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            call, run = self._flaky(
                ["gh", "pr", "create"], 99,
                "could not add label: 'go:risk-medium' not found",
            )
            sleeps: list = []

            result, quarantined = self._integrate(call, journal_path, sleeps)

            self.assertIsNone(result)
            self.assertIn("base", quarantined)
            self.assertEqual(len(run.find_calls("gh", "pr", "create")), 1,
                             "deterministic failures must not retry")
            self.assertEqual(sleeps, [])
            record = json.loads(Path(journal_path).read_text())["groups"]["base"]
            self.assertEqual(record["state"], "QUARANTINED")
            self.assertEqual(record["pr_url"], "")

    def test_pr_create_persistent_transient_failure_quarantines(self):
        """A transient error that never clears exhausts the bounded attempts
        (with linear backoff between them) and then quarantines as before."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            call, run = self._flaky(["gh", "pr", "create"], 99, self.TRANSIENT_ERR)
            sleeps: list = []

            result, quarantined = self._integrate(call, journal_path, sleeps)

            self.assertIsNone(result)
            self.assertIn("base", quarantined)
            self.assertIn("Something went wrong", quarantined["base"])
            self.assertEqual(
                len(run.find_calls("gh", "pr", "create")),
                integrate.GH_TRANSIENT_ATTEMPTS,
            )
            self.assertEqual(
                sleeps,
                [integrate.GH_TRANSIENT_BACKOFF_S * n
                 for n in range(1, integrate.GH_TRANSIENT_ATTEMPTS)],
            )
            record = json.loads(Path(journal_path).read_text())["groups"]["base"]
            self.assertEqual(record["state"], "QUARANTINED")
            self.assertEqual(record["quarantine_reason"], integrate.QUARANTINE_INTEGRATION_ERROR)

    def test_pr_view_transient_failure_retried_reuses_open_pr(self):
        """A transient 5xx on the reconcile `gh pr view` must not fall through
        to `gh pr create` (which would fail 'already exists' and quarantine);
        the retry sees the existing OPEN PR and reuses it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            base_run = FakeRun(
                pr_view_responses={
                    "run-pv/base": [{
                        "number": 5, "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/5",
                        "headRefName": "run-pv/base", "isDraft": False,
                    }]
                },
                ls_remote_responses={"run-pv/base": True},
            )
            call, run = self._flaky(
                ["gh", "pr", "view"], 1, self.TRANSIENT_ERR, base_run=base_run
            )
            sleeps: list = []

            result, quarantined = self._integrate(call, journal_path, sleeps, run_id="run-pv")

            self.assertEqual(quarantined, {})
            self.assertIsNotNone(result)
            self.assertEqual(result[2], "https://github.com/owner/repo/pull/5")
            self.assertEqual(run.find_calls("gh", "pr", "create"), [],
                             "existing OPEN PR must be reused, not re-created")
            self.assertEqual(sleeps, [integrate.GH_TRANSIENT_BACKOFF_S])

    def test_pr_list_transient_failure_retried_discovers_operator_pr(self):
        """Operator-PR discovery (`gh pr list --search`) retries a transient
        failure and then reuses the discovered PR."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            run = FakeRun(pr_view_responses={}, ls_remote_responses={})
            state = {"failed": False}

            def call(*args, **kwargs):
                cmd = args[0] if args and not isinstance(args[0], (str, Path)) else list(args[1:])
                if cmd[:3] == ["gh", "pr", "list"]:
                    run.calls.append(cmd)
                    if not state["failed"]:
                        state["failed"] = True
                        return Proc(1, "", "HTTP 502: Bad Gateway")
                    return Proc(0, json.dumps([{
                        "number": 7, "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/7",
                        "headRefName": "operator/base", "isDraft": False,
                    }]), "")
                return run(*args, **kwargs)

            sleeps: list = []
            result, quarantined = self._integrate(call, journal_path, sleeps)

            self.assertEqual(quarantined, {})
            self.assertIsNotNone(result)
            self.assertEqual(result[2], "https://github.com/owner/repo/pull/7")
            self.assertEqual(run.find_calls("gh", "pr", "create"), [])
            self.assertEqual(sleeps, [integrate.GH_TRANSIENT_BACKOFF_S])

    def test_pr_ready_transient_failure_retried(self):
        """Marking a reused draft PR ready (`gh pr ready`) retries a transient
        failure instead of quarantining the group."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            base_run = FakeRun(
                pr_view_responses={
                    "run-rd/base": [{
                        "number": 9, "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/9",
                        "headRefName": "run-rd/base", "isDraft": True,
                    }]
                },
                ls_remote_responses={"run-rd/base": True},
            )
            call, run = self._flaky(
                ["gh", "pr", "ready"], 1, self.TRANSIENT_ERR, base_run=base_run
            )
            sleeps: list = []

            result, quarantined = self._integrate(call, journal_path, sleeps, run_id="run-rd")

            self.assertEqual(quarantined, {})
            self.assertIsNotNone(result)
            self.assertEqual(result[2], "https://github.com/owner/repo/pull/9")
            self.assertEqual(len(run.find_calls("gh", "pr", "ready")), 2)
            self.assertEqual(sleeps, [integrate.GH_TRANSIENT_BACKOFF_S])

    def test_transient_classification(self):
        transient = [
            "GraphQL: Something went wrong while executing your query.",
            "HTTP 502: Bad Gateway",
            "HTTP 503: Service Unavailable",
            "Post \"https://api.github.com/graphql\": dial tcp: i/o timeout",
            "net/http: TLS handshake timeout",
            "dial tcp: lookup api.github.com: connection refused",
        ]
        deterministic = [
            "could not add label: 'go:risk-medium' not found",
            'a pull request for branch "x" into branch "main" already exists',
            "HTTP 422: Validation Failed (createPullRequest)",
            "gh: To get started with GitHub CLI, please run: gh auth login",
            "no pull requests found for branch",
            "",
        ]
        for msg in transient:
            self.assertTrue(integrate._gh_error_is_transient(msg), msg)
        for msg in deterministic:
            self.assertFalse(integrate._gh_error_is_transient(msg), msg or "(empty)")


class PRLabels(unittest.TestCase):
    """The orchestrator carries the GO gate's exact labels to group PRs."""

    def test_create_refreshes_labels_before_new_pr(self):
        """Verify labels are refreshed via pre_pr_gate --labels-only before new PR creation."""
        fd, fake_gate = tempfile.mkstemp(suffix="pre_pr_gate.py")
        os.close(fd)

        try:
            run = FakeRun(pr_view_responses={}, ls_remote_responses={})

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        with patch("worktrail.orchestrator.integrate._resolve_pre_pr_gate",
                                   return_value=Path(fake_gate)):
                            mock_groups.return_value = [mock_group("base", ["T001"])]

                            integrate_groups(
                                Path("/repo"),
                                "spec-001",
                                [mock_task("T001")],
                                "origin",
                                "run-labels",
                                "main",
                                cleanup=False,
                                pr_labels=["go:risk-high", "go:no-automerge"],
                            )

            # Verify pre_pr_gate --labels-only was called with --risk high
            refresh_calls = [
                c for c in run.calls
                if "--labels-only" in c and "--risk" in c
            ]
            self.assertGreaterEqual(len(refresh_calls), 1,
                                    "Should call pre_pr_gate --labels-only for new PR")
            risk_idx = refresh_calls[0].index("--risk") + 1
            self.assertEqual(refresh_calls[0][risk_idx], "high")

            # Verify gh pr create received the FRESH labels (go:risk-high from the mock)
            create_calls = run.find_calls("gh", "pr", "create")
            self.assertEqual(len(create_calls), 1)
            self.assertEqual(
                create_calls[0][create_calls[0].index("--label"):create_calls[0].index("--title")],
                ["--label", "go:risk-high"],
            )
        finally:
            os.unlink(fake_gate)

    def test_create_passes_eligible_label_only(self):
        run = FakeRun(pr_view_responses={}, ls_remote_responses={})

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    mock_groups.return_value = [mock_group("base", ["T001"])]
                    integrate_groups(
                        Path("/repo"), "spec-001", [mock_task("T001")], "origin",
                        "run-eligible", "main", cleanup=False, pr_labels=["go:risk-low"],
                    )

        create_calls = run.find_calls("gh", "pr", "create")
        labels = create_calls[0][create_calls[0].index("--label"):create_calls[0].index("--title")]
        self.assertEqual(labels, ["--label", "go:risk-low"])

    def test_refresh_labels_passes_route_when_set(self):
        """A classified route must reach pre_pr_gate.py --labels-only's --route so
        policy's require_human_routes check applies to orchestrator group PRs the
        same way it applies to one-off PRs (worktrail-preflight run)."""
        fd, fake_gate = tempfile.mkstemp(suffix="pre_pr_gate.py")
        os.close(fd)

        try:
            run = FakeRun(pr_view_responses={}, ls_remote_responses={})

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        with patch("worktrail.orchestrator.integrate._resolve_pre_pr_gate",
                                   return_value=Path(fake_gate)):
                            mock_groups.return_value = [mock_group("base", ["T001"])]

                            integrate_groups(
                                Path("/repo"),
                                "spec-001",
                                [mock_task("T001")],
                                "origin",
                                "run-route",
                                "main",
                                cleanup=False,
                                pr_labels=["go:risk-high"],
                                route="J",
                                gates="routing_cassette_required",
                            )

            refresh_calls = [c for c in run.calls if "--labels-only" in c]
            self.assertGreaterEqual(len(refresh_calls), 1)
            route_idx = refresh_calls[0].index("--route") + 1
            self.assertEqual(refresh_calls[0][route_idx], "J")
            gates_idx = refresh_calls[0].index("--gates") + 1
            self.assertEqual(refresh_calls[0][gates_idx], "routing_cassette_required")
        finally:
            os.unlink(fake_gate)

    def test_refresh_labels_omits_route_when_unset(self):
        """Existing callers that never pass route (e.g. finish() golden-record path,
        callers on an unclassified run) must keep working unchanged: no --route flag
        is sent to the gate script at all."""
        fd, fake_gate = tempfile.mkstemp(suffix="pre_pr_gate.py")
        os.close(fd)

        try:
            run = FakeRun(pr_view_responses={}, ls_remote_responses={})

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        with patch("worktrail.orchestrator.integrate._resolve_pre_pr_gate",
                                   return_value=Path(fake_gate)):
                            mock_groups.return_value = [mock_group("base", ["T001"])]

                            integrate_groups(
                                Path("/repo"),
                                "spec-001",
                                [mock_task("T001")],
                                "origin",
                                "run-noroute",
                                "main",
                                cleanup=False,
                                pr_labels=["go:risk-high"],
                            )

            refresh_calls = [c for c in run.calls if "--labels-only" in c]
            self.assertGreaterEqual(len(refresh_calls), 1)
            self.assertNotIn("--route", refresh_calls[0])
        finally:
            os.unlink(fake_gate)


class NoForceResetExistingRemoteBranch(unittest.TestCase):
    """AC-006: Do not force-reset existing remote branches."""

    def test_existing_remote_branch_not_reset(self):
        """Verify ls-remote detects existing branch and skips checkout -B."""
        pr_view = {}  # no PR yet
        ls_remote = {"run-resume/base": True}  # branch exists remotely

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-resume",
                        "main",
                        cleanup=False,
                    )

                    # Verify no checkout -B for existing remote branch
                    checkout_b_calls = [
                        c
                        for c in run.calls
                        if "checkout" in c and "-B" in c and "run-resume/base" in c
                    ]
                    self.assertEqual(
                        len(checkout_b_calls), 0, "Should not force-reset existing remote branch"
                    )


class EdgeCases(unittest.TestCase):
    """Edge cases: remote query failures, JSON parse errors."""

    def test_remote_query_fails_falls_back_to_create(self):
        """Verify failed ls-remote falls back to creating branch."""
        pr_view = {}
        ls_remote = {}  # query will fail

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-fail",
                        "main",
                        cleanup=False,
                    )

                    # Should fall back to creating PR
                    create_calls = run.find_calls("gh", "pr", "create")
                    self.assertEqual(
                        len(create_calls), 1, "Should fallback to create when ls-remote fails"
                    )

    def test_pr_view_invalid_json_falls_back(self):
        """Verify invalid JSON from gh pr view falls back to creating PR."""
        # Use a Proc that returns invalid JSON
        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git") as mock_git:
                with patch("worktrail.orchestrator.integrate.subprocess.run") as mock_run:
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    # ls-remote succeeds but pr-view returns invalid JSON
                    def run_side_effect(*args, **kwargs):
                        # Handle both _git (Path first) and subprocess.run (list first)
                        if args and isinstance(args[0], (str, Path)):
                            cmd = list(args[1:])
                        else:
                            cmd = args[0] if args else []
                        if "ls-remote" in cmd:
                            return Proc(1, "", "")  # no remote branch
                        if "pr" in cmd and "view" in cmd:
                            return Proc(0, "not json", "")  # invalid JSON
                        if "checkout" in cmd:
                            return Proc(0, "", "")
                        if "merge" in cmd:
                            return Proc(0, "", "")
                        if "push" in cmd:
                            return Proc(0, "", "")
                        if "pr" in cmd and "create" in cmd:
                            return Proc(0, "https://github.com/o/r/pull/99\n", "")
                        return Proc(0, "", "")

                    mock_git.side_effect = run_side_effect
                    mock_run.side_effect = run_side_effect

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-bad-json",
                        "main",
                        cleanup=False,
                    )

                    # Should still create PR (invalid JSON treated as error)
                    create_calls = [
                        c for c in mock_run.call_args_list if c[0][0][:3] == ["gh", "pr", "create"]
                    ]
                    self.assertTrue(create_calls, "Should fallback to create on invalid JSON")


class MultipleGroupsReconciliation(unittest.TestCase):
    """Test reconciliation with multiple groups and mixed states."""

    def test_mixed_states_multiple_groups(self):
        """Verify reconciliation handles mixed states across groups."""
        pr_view = {
            "multi-run/base": [
                {"number": 10, "state": "OPEN", "url": "https://github.com/owner/repo/pull/10"}
            ],
            "multi-run/feature-1": [
                {"number": 20, "state": "MERGED", "url": "https://github.com/owner/repo/pull/20"}
            ],
            # feature-2 has no PR yet
        }
        ls_remote = {
            "multi-run/base": True,
            # feature-1 remote branch will fail ls-remote (simulate not yet pushed)
        }

        run = FakeRun(pr_view_responses=pr_view, ls_remote_responses=ls_remote)

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    base = mock_group("base", ["T001"])
                    feat1 = mock_group("feature-1", ["T002"], depends_on=["base"])
                    feat2 = mock_group("feature-2", ["T003"], depends_on=["base"])
                    mock_groups.return_value = [base, feat1, feat2]

                    prs, gb, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001"), mock_task("T002"), mock_task("T003")],
                        "origin",
                        "multi-run",
                        "main",
                        cleanup=False,
                    )

                    # base: OPEN PR reused
                    # feature-1: MERGED PR skipped
                    # feature-2: new PR created
                    self.assertEqual(
                        len(prs), 2, "Should have 2 PRs (base OPEN reused, feature-2 created)"
                    )

                    pr_names = [p[0] for p in prs]
                    self.assertIn("base", pr_names)
                    self.assertIn("feature-2", pr_names)
                    self.assertNotIn("feature-1", pr_names, "MERGED PR should not be in list")


class SingleGroupIntegrateEntry(unittest.TestCase):
    """AC-007: integrate_one integrates exactly one group and records its result."""

    def test_integrate_one_writes_journal_for_only_that_group(self):
        """integrate_one writes journal["groups"][name] and does not overwrite other groups."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            # Pre-populate journal with a different group to verify isolation
            Path(journal_path).write_text(
                json.dumps({"groups": {"other": {"pr_url": "x", "head_branch": "y", "state": "OPEN"}}})
            )
            run = FakeRun()
            group = mock_group("base", ["T001"])

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.integrate_one(
                        group,
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "run-sg",
                        "main",
                        journal_path,
                        {"T001": "done"},
                        {},
                        {},
                    )

                    self.assertIsNotNone(result)
                    name, target, pr_url = result
                    self.assertEqual(name, "base")

                    journal = json.loads(Path(journal_path).read_text())
                    record = journal["groups"]["base"]
                    self.assertIn("pr_url", record)
                    self.assertIn("head_branch", record)
                    self.assertIn("state", record)
                    self.assertEqual(record["state"], "OPEN")
                    # "other" group untouched — only "base" was written
                    self.assertIn("other", journal["groups"])

    def test_integrate_one_merged_group_returns_none_no_branch(self):
        """integrate_one returns None for an already-MERGED group; no branch or PR created."""
        pr_view = {
            "run-mg/base": [
                {"number": 5, "state": "MERGED", "url": "https://github.com/o/r/pull/5",
                 "headRefName": "run-mg/base"}
            ]
        }
        run = FakeRun(pr_view_responses=pr_view)
        group_branch: dict = {}
        quarantined: dict = {}

        with patch("worktrail.orchestrator.integrate._git", side_effect=run):
            with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                result = integrate.integrate_one(
                    mock_group("base", ["T001"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001")],
                    "origin",
                    "run-mg",
                    "main",
                    None,
                    {"T001": "done"},
                    group_branch,
                    quarantined,
                )

                self.assertIsNone(result, "MERGED group must return None")
                self.assertIn(
                    "base", group_branch,
                    "a MERGED group must still register in group_branch so callers "
                    "see it as integrated rather than concluding "
                    "'nothing to assemble' and bailing out before tail dispatch",
                )
                checkout_b = [c for c in run.calls if "checkout" in c and "-B" in c]
                self.assertEqual(len(checkout_b), 0, "Should not create branch for MERGED group")
                self.assertEqual(
                    len(run.find_calls("gh", "pr", "create")), 0,
                    "Should not create PR for MERGED group",
                )

    def test_integrate_one_reuses_open_pr(self):
        """integrate_one reuses an existing OPEN PR without calling gh pr create."""
        pr_view = {
            "run-op/base": [
                {"number": 77, "state": "OPEN", "url": "https://github.com/o/r/pull/77",
                 "headRefName": "run-op/base"}
            ]
        }
        run = FakeRun(pr_view_responses=pr_view)
        group_branch: dict = {}

        with patch("worktrail.orchestrator.integrate._git", side_effect=run):
            with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                result = integrate.integrate_one(
                    mock_group("base", ["T001"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001")],
                    "origin",
                    "run-op",
                    "main",
                    None,
                    {"T001": "done"},
                    group_branch,
                    {},
                )

                self.assertIsNotNone(result)
                name, target, pr_url = result
                self.assertIn("77", pr_url)
                self.assertEqual(
                    len(run.find_calls("gh", "pr", "create")), 0,
                    "Should not call gh pr create when OPEN PR exists",
                )

    def test_integrate_one_empty_deliverable_quarantines(self):
        """integrate_one quarantines a group whose every task has failed (no PR)."""
        run = FakeRun()
        quarantined: dict = {}

        with patch("worktrail.orchestrator.integrate._git", side_effect=run):
            with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                result = integrate.integrate_one(
                    mock_group("base", ["T001"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001", status="failed")],
                    "origin",
                    "run-eq",
                    "main",
                    None,
                    {"T001": "failed"},
                    {},
                    quarantined,
                )

                self.assertIsNone(result, "Empty deliverable must return None")
                self.assertIn("base", quarantined, "Group must be quarantined")
                self.assertEqual(
                    len(run.find_calls("gh", "pr", "create")), 0,
                    "Should not create PR for quarantined group",
                )

    def test_integrate_one_empty_deliverable_records_task_failure_reason(self):
        """The journal record for an empty-deliverable quarantine carries the
        structured task_failure reason (distinct from budget_exhausted -- this
        group's tasks actually failed, so it is NOT safely re-runnable as-is)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            run = FakeRun()
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.integrate_one(
                        mock_group("base", ["T001"]),
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001", status="failed")],
                        "origin",
                        "run-eq",
                        "main",
                        journal_path,
                        {"T001": "failed"},
                        {},
                        {},
                    )
            self.assertIsNone(result)
            record = json.loads(Path(journal_path).read_text())["groups"]["base"]
            self.assertEqual(record["state"], "QUARANTINED")
            self.assertEqual(record["quarantine_reason"], integrate.QUARANTINE_TASK_FAILURE)

    def test_dep_on_quarantined_cascades_records_dependency_reason(self):
        """A cascade-quarantined dependent group's journal record carries the
        dependency_quarantined reason, distinct from its base's own reason."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            run = FakeRun()
            group_branch: dict = {}
            quarantined = {"base": "incomplete task(s): T001"}
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.integrate_one(
                        mock_group("feature", ["T002"], depends_on=["base"]),
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001", status="failed"), mock_task("T002")],
                        "origin",
                        "run-cas",
                        "main",
                        journal_path,
                        {"T001": "failed", "T002": "done"},
                        group_branch,
                        quarantined,
                    )
            self.assertIsNone(result)
            record = json.loads(Path(journal_path).read_text())["groups"]["feature"]
            self.assertEqual(record["state"], "QUARANTINED")
            self.assertEqual(
                record["quarantine_reason"], integrate.QUARANTINE_DEPENDENCY_QUARANTINED
            )


class MultiGroupLoopShapes(unittest.TestCase):
    """AC-009, AC-010: the per-group integrate_one seam, driven in a loop,
    produces the multi-group shapes the scheduler relies on."""

    def test_loop_correct_prs_journal_and_field_shapes(self):
        """The loop produces correct prs, group_branch, quarantined, and journal records."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = str(Path(tmpdir) / "journal.json")
            Path(journal_path).write_text(json.dumps({"run_id": "run-fr"}) + "\n")

            pr_view = {
                "run-fr/base": [
                    {"number": 10, "state": "OPEN", "url": "https://github.com/o/r/pull/10",
                     "headRefName": "run-fr/base"}
                ],
            }
            run = FakeRun(pr_view_responses=pr_view)

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        mock_groups.return_value = [
                            mock_group("base", ["T001"]),
                            mock_group("feature", ["T002"], depends_on=["base"]),
                        ]

                        prs, gb, quarantined = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001"), mock_task("T002")],
                            "origin",
                            "run-fr",
                            "main",
                            cleanup=False,
                            journal_path=journal_path,
                        )

                        # base: OPEN PR reused; feature: new PR created
                        self.assertEqual(len(prs), 2)
                        pr_names = [p[0] for p in prs]
                        self.assertIn("base", pr_names)
                        self.assertIn("feature", pr_names)

                        self.assertIn("base", gb)
                        self.assertIn("feature", gb)
                        self.assertEqual(quarantined, {})

                        journal = json.loads(Path(journal_path).read_text())
                        self.assertTrue(journal.get("integrate_complete"))
                        for grp in ("base", "feature"):
                            record = journal["groups"][grp]
                            self.assertIn("pr_url", record, f"{grp} missing pr_url")
                            self.assertIn("head_branch", record, f"{grp} missing head_branch")
                            self.assertIn("state", record, f"{grp} missing state")
                        self.assertEqual(journal["groups"]["base"]["state"], "OPEN")


class DepOnQuarantinedCascade(unittest.TestCase):
    """AC-007: dep-on-quarantined cascade is preserved by the single-group seam."""

    def test_dep_on_quarantined_cascades_via_integrate_one(self):
        """A dependent group is quarantined when its base group was quarantined."""
        run = FakeRun()
        group_branch: dict = {}
        quarantined: dict = {}

        with patch("worktrail.orchestrator.integrate._git", side_effect=run):
            with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                # base: all tasks failed → quarantined
                r_base = integrate.integrate_one(
                    mock_group("base", ["T001"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001", status="failed")],
                    "origin",
                    "run-cas",
                    "main",
                    None,
                    {"T001": "failed"},
                    group_branch,
                    quarantined,
                )
                self.assertIsNone(r_base)
                self.assertIn("base", quarantined)

                # feature: depends_on=["base"] which is quarantined → cascade quarantine
                r_feat = integrate.integrate_one(
                    mock_group("feature", ["T002"], depends_on=["base"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001", status="failed"), mock_task("T002")],
                    "origin",
                    "run-cas",
                    "main",
                    None,
                    {"T001": "failed", "T002": "done"},
                    group_branch,
                    quarantined,
                )
                self.assertIsNone(r_feat, "Dependent of quarantined base must return None")
                self.assertIn("feature", quarantined)
                self.assertIn("base", quarantined["feature"],
                              "Quarantine reason must name the quarantined dependency")
                self.assertEqual(
                    len(run.find_calls("gh", "pr", "create")), 0,
                    "Should not create PR for either quarantined group",
                )


def _run(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


class IntegrationWorktreeIsolation(unittest.TestCase):
    """item 3: building a group branch must NOT hijack the spec worktree's HEAD.

    The old `git checkout -B <group> <start>` ran inside `repo` (the spec worktree),
    moving HEAD off the spec branch and discarding uncommitted `files:` edits.
    `_integration_worktree` builds the branch in an isolated checkout instead.
    """

    def _init_repo(self, root):
        repo = Path(root) / "repo"
        repo.mkdir()
        _run(repo, "init", "-q")
        _run(repo, "config", "user.email", "t@t")
        _run(repo, "config", "user.name", "T")
        (repo / "base.txt").write_text("base\n")
        _run(repo, "add", "-A")
        _run(repo, "commit", "-q", "-m", "init")  # commit on default branch == base
        base = _run(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        return repo, base

    def test_group_branch_built_without_moving_repo_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._init_repo(tmp)

            # A task branch off base with its own commit (what integrate merges).
            _run(repo, "checkout", "-q", "-b", "spec-001/task-001", base)
            (repo / "feature.txt").write_text("from task 001\n")
            _run(repo, "add", "-A")
            _run(repo, "commit", "-q", "-m", "feat: task 001")

            # The spec worktree sits on the spec branch with an UNCOMMITTED edit.
            _run(repo, "checkout", "-q", "-b", "spec/x", base)
            (repo / "wip.txt").write_text("uncommitted spec edit\n")

            with integrate._integration_worktree(repo, "run-1/base", base) as iw:
                self.assertTrue(Path(iw).exists(), "worktree dir should exist inside context")
                m = subprocess.run(
                    ["git", "-C", str(iw), "merge", "--no-edit", "spec-001/task-001"],
                    capture_output=True, text=True,
                )
                self.assertEqual(m.returncode, 0, f"merge failed: {m.stderr}")
                # The merge landed in the ISOLATED tree, not in the spec worktree.
                self.assertTrue((Path(iw) / "feature.txt").exists())

            # HEAD never moved: still on the spec branch with the edit intact.
            self.assertEqual(
                _run(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "spec/x"
            )
            self.assertTrue((repo / "wip.txt").exists(), "uncommitted spec edit must survive")
            self.assertFalse((repo / "feature.txt").exists(), "merge must not touch spec worktree")

            # The group branch exists and carries the merged task commit.
            log = _run(repo, "log", "--oneline", "run-1/base").stdout
            self.assertIn("task 001", log)
            # The temp worktree was torn down.
            self.assertFalse(Path(iw).exists(), "integration worktree must be removed")

    def test_worktree_removed_even_on_merge_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, base = self._init_repo(tmp)
            captured = {}
            with integrate._integration_worktree(repo, "run-1/base", base) as iw:
                captured["iw"] = iw
                self.assertTrue(Path(iw).exists())
            # Context exit must remove the worktree regardless of what happened inside.
            self.assertFalse(Path(captured["iw"]).exists())
            self.assertEqual(
                _run(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), base
            )


class SpecFolderOwnership(unittest.TestCase):
    """Fix 1: only the spec-carrier group carries docs/specs/<spec_id>/.

    Sibling independent groups reset the spec folder to their target base ref to
    prevent add/add conflicts when the first sibling merges into the real base.
    """

    def _make_strip_tracker(self):
        """Return a FakeRun subclass that records git checkout spec-folder calls."""
        checkout_spec_calls = []

        class TrackingRun(FakeRun):
            def __call__(self, *args, **kwargs):
                if args and isinstance(args[0], (str, Path)):
                    cmd = list(args[1:])
                else:
                    cmd = args[0] if args else []
                if (
                    "checkout" in cmd
                    and "--" in cmd
                    and any("docs/specs" in str(a) for a in cmd)
                ):
                    checkout_spec_calls.append(cmd)
                return super().__call__(*args, **kwargs)

        run = TrackingRun()
        return run, checkout_spec_calls

    def test_nested_devkit_change_is_selected_for_spec_folder_operations(self):
        """Nested change specs must win over the flat leaf-name fallback."""
        with tempfile.TemporaryDirectory() as tmp:
            iw = Path(tmp)
            nested = (
                iw / "docs" / "specs" / "053-parent" / "changes" / "2026-07-21-output-contract"
            )
            nested.mkdir(parents=True)

            self.assertEqual(
                integrate._spec_path_for(iw, "2026-07-21-output-contract"), nested
            )

    def test_carrier_group_does_not_strip(self):
        """The designated spec-carrier group must NOT reset the spec folder."""
        run, spec_checkouts = self._make_strip_tracker()

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    # Single base group — it is the spec carrier
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    integrate_groups(
                        Path("/repo"), "spec-048",
                        [mock_task("T001")], "origin", "full-123", "main",
                        cleanup=False,
                    )

                    self.assertEqual(
                        len(spec_checkouts), 0,
                        "Spec carrier must not reset spec folder"
                    )

    def test_sibling_independent_group_strips_spec_folder(self):
        """An independent sibling (depends_on=[]) that is not the carrier must strip."""
        run, spec_checkouts = self._make_strip_tracker()

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    base_g = mock_group("base", ["T001"])
                    feat_g = mock_group("feature-2", ["T002"], depends_on=[])  # independent
                    mock_groups.return_value = [base_g, feat_g]

                    integrate_groups(
                        Path("/repo"), "spec-048",
                        [mock_task("T001"), mock_task("T002")], "origin", "full-123", "main",
                        cleanup=False,
                    )

                    self.assertEqual(
                        len(spec_checkouts), 1,
                        "Independent non-carrier sibling must reset spec folder once"
                    )
                    # The reset should target the base branch
                    self.assertIn("main", spec_checkouts[0])
                    self.assertTrue(
                        any("docs/specs/spec-048" in str(a) for a in spec_checkouts[0]),
                        "Reset must target the spec-048 folder"
                    )

    def test_stacked_group_does_not_strip(self):
        """A group stacked on base (depends_on=['base']) must NOT strip spec folder."""
        run, spec_checkouts = self._make_strip_tracker()

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    base_g = mock_group("base", ["T001"])
                    feat_g = mock_group("feature-1", ["T002"], depends_on=["base"])
                    mock_groups.return_value = [base_g, feat_g]

                    integrate_groups(
                        Path("/repo"), "spec-048",
                        [mock_task("T001"), mock_task("T002")], "origin", "full-123", "main",
                        cleanup=False,
                    )

                    self.assertEqual(
                        len(spec_checkouts), 0,
                        "Stacked group must not strip spec folder (no sibling conflict risk)"
                    )

    def test_strip_spec_folder_kwarg_passed_through_integrate_one(self):
        """integrate_one with strip_spec_folder=True emits a spec-folder checkout call."""
        run, spec_checkouts = self._make_strip_tracker()

        with patch("worktrail.orchestrator.integrate._git", side_effect=run):
            with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                g = mock_group("feature-2", ["T001"])
                status = {"T001": "done"}
                group_branch: dict = {}
                quarantined: dict = {}

                integrate.integrate_one(
                    g, Path("/repo"), "spec-048",
                    [mock_task("T001")], "origin", "full-123", "main",
                    None, status, group_branch, quarantined,
                    strip_spec_folder=True,
                )

                self.assertEqual(
                    len(spec_checkouts), 1,
                    "strip_spec_folder=True must reset the spec folder"
                )


class ResolvePrePrGateResolution(unittest.TestCase):
    """The gate resolves from an explicit override or Worktrail itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._env_patch = patch.dict(os.environ, {}, clear=False)
        self._env_patch.start()
        os.environ.pop("PRE_PR_GATE_SCRIPT", None)
        self.addCleanup(self._env_patch.stop)

    def test_explicit_override_wins(self):
        gate = self.root / "pre_pr_gate.py"
        gate.write_text("")
        os.environ["PRE_PR_GATE_SCRIPT"] = str(gate)
        self.assertEqual(integrate._resolve_pre_pr_gate(self.root), gate)

    @patch("worktrail.orchestrator.integrate.shutil.which", return_value=None)
    def test_no_external_plugin_path_is_scanned(self, _which):
        self.assertIsNone(integrate._resolve_pre_pr_gate(self.root))


if __name__ == "__main__":
    unittest.main()
