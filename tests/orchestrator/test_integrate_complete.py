#!/usr/bin/env python3
"""Unit tests for integrate_complete marker, per-group records, and operator PR discovery.

Tests for AC-019 (integrate_complete marker), AC-021/AC-024 (per-group records),
and AC-022/AC-028 (operator PR discovery).

Follows the FakeRun pattern from test_integrate.py.

Run: python3 test_integrate_complete.py
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import deque, namedtuple
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import coordinator
from worktrail.orchestrator import integrate

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_integrate import integrate_groups  # noqa: E402

Proc = namedtuple("Proc", "returncode stdout stderr")


def mock_group(name, tasks, depends_on=None):
    """Create a mock group dict."""
    return {"name": name, "tasks": tasks, "reqs": [], "depends_on": depends_on or []}


def mock_task(task_id, status="done"):
    """Create a mock task dict."""
    return {"id": task_id, "status": status, "kind": "impl"}


class FakeRunWithOperator(unittest.TestCase):
    """Scriptable git+gh runner for testing operator PR discovery."""

    class FakeRunHelper:
        """Intercepts subprocess.run calls and returns scripted Proc objects."""

        def __init__(
            self,
            pr_view_responses=None,
            pr_list_responses=None,
            ls_remote_responses=None,
            remote_url="https://github.com/owner/repo.git",
        ):
            """Initialize with mocked responses.

            Args:
                pr_view_responses: dict mapping branch/PR URL -> list of pr view dicts
                pr_list_responses: dict of pr list --search responses
                ls_remote_responses: dict mapping branch name -> bool (True = exists)
                remote_url: mocked git remote URL
            """
            self.pr_view_responses = {k: deque(v) for k, v in (pr_view_responses or {}).items()}
            self.pr_list_responses = pr_list_responses or {}
            self.ls_remote_responses = ls_remote_responses or {}
            self.remote_url = remote_url
            self.calls = []

        def __call__(self, *args, **kwargs):
            """Handle subprocess.run or _git calls."""
            # Determine if this is a _git call (first arg is Path) or subprocess.run (first arg is list)
            if args and isinstance(args[0], (str, Path)):
                cmd = list(args[1:])
            else:
                cmd = args[0] if args else []

            self.calls.append(cmd)

            # git ls-remote origin <branch>
            if cmd[:3] == ["ls-remote", "origin"] or cmd[:2] == ["ls-remote", "origin"]:
                branch = cmd[3] if len(cmd) > 3 else cmd[2] if len(cmd) > 2 else None
                if branch in self.ls_remote_responses and self.ls_remote_responses[branch]:
                    return Proc(0, f"abc123\trefs/heads/{branch}\n", "")
                return Proc(1, "", "")

            # gh pr view <branch/url> --json number,state,url,headRefName
            if cmd[:3] == ["gh", "pr", "view"] or (
                len(cmd) >= 3 and cmd[0] == "gh" and cmd[1] == "pr" and cmd[2] == "view"
            ):
                query = cmd[3] if len(cmd) > 3 else None
                responses = self.pr_view_responses.get(query)
                if not responses:
                    return Proc(1, "", "no pull requests found")
                resp = responses[0] if len(responses) == 1 else responses.popleft()
                return Proc(0, json.dumps(resp), "")

            # gh pr list --search
            if cmd[:3] == ["gh", "pr", "list"]:
                search_query = None
                for i, arg in enumerate(cmd):
                    if arg == "--search" and i + 1 < len(cmd):
                        search_query = cmd[i + 1]
                        break
                if search_query in self.pr_list_responses:
                    result = self.pr_list_responses[search_query]
                    return Proc(0, json.dumps(result), "")
                return Proc(0, "[]", "")  # empty list if no match

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

            # git diff --quiet <target> (empty-diff-vs-base guard): default to
            # "real changes exist" (returncode 1), matching every other
            # fixture's implicit assumption that a scripted merge delivered content.
            if cmd[:2] == ["diff", "--quiet"]:
                return Proc(1, "", "")

            # Default: success
            return Proc(0, "", "")

        def find_calls(self, *prefix):
            """Find all calls matching the given prefix."""
            return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


class IntegrateCompleteMarker(unittest.TestCase):
    """AC-019: integrate_complete marker written to journal."""

    def test_integrate_complete_marker_written(self):
        """Verify integrate_complete: true is written to journal after processing all groups."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "test-journal.json"

            # Initialize journal
            journal_path.write_text(json.dumps({"run_id": "full-123", "entries": []}) + "\n")

            pr_view = {
                "full-123/base": [
                    {
                        "number": 42,
                        "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/42",
                        "headRefName": "full-123/base",
                    }
                ]
            }
            ls_remote = {}

            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses=pr_view, ls_remote_responses=ls_remote
            )

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        group = mock_group("base", ["T001"])
                        mock_groups.return_value = [group]

                        prs, _, _ = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001")],
                            "origin",
                            "full-123",
                            "main",
                            cleanup=False,
                            journal_path=str(journal_path),
                        )

                        # Check journal was written with integrate_complete
                        journal = json.loads(journal_path.read_text())
                        self.assertTrue(
                            journal.get("integrate_complete"),
                            "integrate_complete marker should be True",
                        )

    def test_journal_not_written_when_path_none(self):
        """Verify backward compatibility: no journal writing when journal_path=None."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "test-journal.json"

            pr_view = {
                "full-456/base": [
                    {
                        "number": 50,
                        "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/50",
                        "headRefName": "full-456/base",
                    }
                ]
            }
            ls_remote = {}

            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses=pr_view, ls_remote_responses=ls_remote
            )

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        group = mock_group("base", ["T001"])
                        mock_groups.return_value = [group]

                        prs, _, _ = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001")],
                            "origin",
                            "full-456",
                            "main",
                            cleanup=False,
                            journal_path=None,  # No journal writing
                        )

                        # Journal file should not exist
                        self.assertFalse(
                            journal_path.exists(),
                            "Journal should not be created when journal_path=None",
                        )


class PerGroupIntegrateRecords(unittest.TestCase):
    """AC-021, AC-024: Per-group integrate records with pr_url, head_branch, state."""

    def test_per_group_record_written(self):
        """Verify per-group integrate record is written with all required fields."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "test-journal.json"
            journal_path.write_text(json.dumps({"run_id": "full-789", "entries": []}) + "\n")

            pr_view = {
                "full-789/base": [
                    {
                        "number": 30,
                        "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/30",
                        "headRefName": "full-789/base",
                    }
                ]
            }
            ls_remote = {}

            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses=pr_view, ls_remote_responses=ls_remote
            )

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        group = mock_group("base", ["T001"])
                        mock_groups.return_value = [group]

                        prs, _, _ = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001")],
                            "origin",
                            "full-789",
                            "main",
                            cleanup=False,
                            journal_path=str(journal_path),
                        )

                        # Check per-group record
                        journal = json.loads(journal_path.read_text())
                        self.assertIn("groups", journal)
                        self.assertIn("base", journal["groups"])

                        record = journal["groups"]["base"]
                        self.assertIn("pr_url", record)
                        self.assertIn("head_branch", record)
                        self.assertIn("state", record)
                        self.assertEqual(record["state"], "OPEN")

    def test_merged_group_record_written(self):
        """Verify MERGED group records are written with state: MERGED."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "test-journal.json"
            journal_path.write_text(json.dumps({"run_id": "full-999", "entries": []}) + "\n")

            pr_view = {
                "full-999/base": [
                    {
                        "number": 15,
                        "state": "MERGED",
                        "url": "https://github.com/owner/repo/pull/15",
                        "headRefName": "full-999/base",
                    }
                ]
            }
            ls_remote = {}

            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses=pr_view, ls_remote_responses=ls_remote
            )

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        group = mock_group("base", ["T001"])
                        mock_groups.return_value = [group]

                        prs, _, _ = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001")],
                            "origin",
                            "full-999",
                            "main",
                            cleanup=False,
                            journal_path=str(journal_path),
                        )

                        # Check merged group record
                        journal = json.loads(journal_path.read_text())
                        self.assertIn("groups", journal)
                        self.assertIn("base", journal["groups"])

                        record = journal["groups"]["base"]
                        self.assertEqual(record["state"], "MERGED")


class OperatorPRDiscovery(unittest.TestCase):
    """AC-022, AC-028: Operator PR discovery via gh pr list --search."""

    def test_operator_pr_discovered_via_search(self):
        """AC-028: Operator PR discovered when no conventional PR exists."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "test-journal.json"
            journal_path.write_text(json.dumps({"run_id": "full-111", "entries": []}) + "\n")

            pr_view = {}  # No PR on conventional branch
            pr_list = {
                "base spec-001": [  # Search query result
                    {
                        "number": 88,
                        "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/88",
                        "headRefName": "operator-custom-branch",
                        "baseRefName": "main",
                    }
                ]
            }
            ls_remote = {}

            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses=pr_view, pr_list_responses=pr_list, ls_remote_responses=ls_remote
            )

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        group = mock_group("base", ["T001"])
                        mock_groups.return_value = [group]

                        prs, _, _ = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001")],
                            "origin",
                            "full-111",
                            "main",
                            cleanup=False,
                            journal_path=str(journal_path),
                        )

                        # Verify operator PR was discovered and reused
                        self.assertEqual(len(prs), 1)
                        name, target, pr_url = prs[0]
                        self.assertEqual(name, "base")
                        self.assertEqual(pr_url, "https://github.com/owner/repo/pull/88")

                        # Verify gh pr create was NOT called
                        create_calls = run.find_calls("gh", "pr", "create")
                        self.assertEqual(
                            len(create_calls),
                            0,
                            "Should not call gh pr create for discovered operator PR",
                        )

                        # Verify journal has correct head_branch
                        journal = json.loads(journal_path.read_text())
                        record = journal["groups"]["base"]
                        self.assertEqual(record["head_branch"], "operator-custom-branch")

    def test_no_operator_pr_creates_normally(self):
        """Regression: When no operator PR found, create new PR normally."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "test-journal.json"
            journal_path.write_text(json.dumps({"run_id": "full-222", "entries": []}) + "\n")

            pr_view = {}  # No PR on conventional branch
            pr_list = {"base spec-001": []}  # Empty search result
            ls_remote = {}

            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses=pr_view, pr_list_responses=pr_list, ls_remote_responses=ls_remote
            )

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        group = mock_group("base", ["T001"])
                        mock_groups.return_value = [group]

                        prs, _, _ = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001")],
                            "origin",
                            "full-222",
                            "main",
                            cleanup=False,
                            journal_path=str(journal_path),
                        )

                        # Should create PR normally
                        create_calls = run.find_calls("gh", "pr", "create")
                        self.assertEqual(
                            len(create_calls),
                            1,
                            "Should call gh pr create when no operator PR found",
                        )

    def test_operator_pr_search_failure_falls_back(self):
        """Edge case: gh pr list fails → fall back to gh pr create."""
        pr_view = {}
        pr_list = {}  # No response = error case
        ls_remote = {}

        run = FakeRunWithOperator.FakeRunHelper(
            pr_view_responses=pr_view, pr_list_responses=pr_list, ls_remote_responses=ls_remote
        )

        with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    group = mock_group("base", ["T001"])
                    mock_groups.return_value = [group]

                    prs, _, _ = integrate_groups(
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T001")],
                        "origin",
                        "full-333",
                        "main",
                        cleanup=False,
                        journal_path=None,
                    )

                    # Should fall back to creating PR
                    create_calls = run.find_calls("gh", "pr", "create")
                    self.assertEqual(
                        len(create_calls), 1, "Should fallback to create when pr list fails"
                    )


class MultipleGroupsWithOperatorPR(unittest.TestCase):
    """Integration test: Multiple groups with mixed discovery scenarios."""

    def test_mixed_discovery_scenarios(self):
        """Verify mixed states: conventional PR, operator PR, new PR."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "test-journal.json"
            journal_path.write_text(json.dumps({"run_id": "full-mixed", "entries": []}) + "\n")

            pr_view = {
                "full-mixed/base": [
                    {
                        "number": 10,
                        "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/10",
                        "headRefName": "full-mixed/base",
                    }
                ],
                # feature-1 has no PR
            }
            pr_list = {
                "feature-1 spec-001": [
                    {
                        "number": 20,
                        "state": "OPEN",
                        "url": "https://github.com/owner/repo/pull/20",
                        "headRefName": "operator-feature-1",
                        "baseRefName": "full-mixed/base",
                    }
                ]
            }
            ls_remote = {}

            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses=pr_view, pr_list_responses=pr_list, ls_remote_responses=ls_remote
            )

            with patch("worktrail.orchestrator.integrate.coordinator.plan_groups") as mock_groups:
                with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                    with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                        base = mock_group("base", ["T001"])
                        feat1 = mock_group("feature-1", ["T002"], depends_on=["base"])
                        feat2 = mock_group("feature-2", ["T003"], depends_on=["base"])
                        mock_groups.return_value = [base, feat1, feat2]

                        prs, _, _ = integrate_groups(
                            Path("/repo"),
                            "spec-001",
                            [mock_task("T001"), mock_task("T002"), mock_task("T003")],
                            "origin",
                            "full-mixed",
                            "main",
                            cleanup=False,
                            journal_path=str(journal_path),
                        )

                        # base: conventional OPEN PR
                        # feature-1: operator PR discovered
                        # feature-2: new PR created
                        self.assertEqual(len(prs), 3)

                        pr_names = [p[0] for p in prs]
                        self.assertIn("base", pr_names)
                        self.assertIn("feature-1", pr_names)
                        self.assertIn("feature-2", pr_names)

                        # Verify journal has all three records
                        journal = json.loads(journal_path.read_text())
                        self.assertEqual(len(journal["groups"]), 3)
                        self.assertEqual(journal["groups"]["base"]["state"], "OPEN")
                        self.assertEqual(
                            journal["groups"]["feature-1"]["head_branch"], "operator-feature-1"
                        )


def _tail_task(task_id, kind="e2e", status="pending"):
    return {"id": task_id, "status": status, "kind": kind}


class PendingTailRecording(unittest.TestCase):
    """_mark_integrate_complete_if_terminal records the held-out e2e/cleanup tail."""

    def _journal_with_terminal_base(self, tmpdir):
        """A journal whose single 'base' group is in a terminal (OPEN) state."""
        jp = Path(tmpdir) / "journal.json"
        jp.write_text(
            json.dumps(
                {
                    "run_id": "full-tail",
                    "groups": {"base": {"pr_url": "u", "head_branch": "b", "state": "OPEN"}},
                }
            )
            + "\n"
        )
        return jp

    def test_pending_tail_tasks_and_reason_recorded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jp = self._journal_with_terminal_base(tmpdir)
            tasks = [
                mock_task("T001"),
                _tail_task("T022", "e2e"),
                _tail_task("T023", "cleanup"),
            ]
            complete = integrate._mark_integrate_complete_if_terminal(
                str(jp), [mock_group("base", ["T001"])], tasks
            )
            self.assertTrue(complete)
            journal = json.loads(jp.read_text())
            self.assertTrue(journal["integrate_complete"])
            self.assertEqual(journal["pending_tail_tasks"], ["T022", "T023"])
            self.assertEqual(journal["pending_tail_reason"], integrate.PENDING_TAIL_REASON)

    def test_no_tail_keys_when_no_tail_tasks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jp = self._journal_with_terminal_base(tmpdir)
            integrate._mark_integrate_complete_if_terminal(
                str(jp), [mock_group("base", ["T001"])], [mock_task("T001")]
            )
            journal = json.loads(jp.read_text())
            self.assertTrue(journal["integrate_complete"])
            self.assertNotIn("pending_tail_tasks", journal)
            self.assertNotIn("pending_tail_reason", journal)

    def test_tail_keys_cleared_when_tail_now_done(self):
        # A first pass records the tail; a later pass where the tail ran (status=done)
        # must clear the stale keys so the dashboard stops surfacing phantom work.
        with tempfile.TemporaryDirectory() as tmpdir:
            jp = self._journal_with_terminal_base(tmpdir)
            integrate._mark_integrate_complete_if_terminal(
                str(jp), [mock_group("base", ["T001"])], [mock_task("T001"), _tail_task("T022")]
            )
            self.assertIn("pending_tail_tasks", json.loads(jp.read_text()))
            integrate._mark_integrate_complete_if_terminal(
                str(jp),
                [mock_group("base", ["T001"])],
                [mock_task("T001"), _tail_task("T022", status="done")],
            )
            journal = json.loads(jp.read_text())
            self.assertNotIn("pending_tail_tasks", journal)
            self.assertNotIn("pending_tail_reason", journal)

    def test_no_recording_when_group_non_terminal(self):
        # A group still mid-flight (no terminal record) → not complete, no tail keys.
        with tempfile.TemporaryDirectory() as tmpdir:
            jp = Path(tmpdir) / "journal.json"
            jp.write_text(json.dumps({"run_id": "full-x", "groups": {}}) + "\n")
            complete = integrate._mark_integrate_complete_if_terminal(
                str(jp), [mock_group("base", ["T001"])], [mock_task("T001"), _tail_task("T022")]
            )
            self.assertFalse(complete)
            journal = json.loads(jp.read_text())
            self.assertNotIn("integrate_complete", journal)
            self.assertNotIn("pending_tail_tasks", journal)

    def test_pending_tail_task_ids_excludes_done(self):
        ids = integrate._pending_tail_task_ids(
            [
                mock_task("T001"),  # impl → excluded
                _tail_task("T030", "cleanup", status="done"),  # tail but done → excluded
                _tail_task("T022", "e2e"),  # tail pending → included
            ]
        )
        self.assertEqual(ids, ["T022"])

    def test_pending_tail_task_ids_includes_impl_blocked_only_by_tail(self):
        ids = integrate._pending_tail_task_ids(
            [
                {"id": "T001", "status": "done", "kind": "impl"},
                _tail_task("T022", "e2e"),
                {"id": "T023", "status": "pending", "kind": "impl", "deps": ["T022"]},
                {"id": "T024", "status": "pending", "kind": "impl", "deps": ["T001"]},
            ]
        )
        self.assertEqual(ids, ["T022", "T023"])

    def test_pending_tail_task_ids_none_safe(self):
        self.assertEqual(integrate._pending_tail_task_ids(None), [])


def _run_git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


class UnreconciledTailEvidence(unittest.TestCase):
    """detect_unreconciled_tail_evidence flags a terminal tail task whose own
    worktree branch never merged onto base -- the sibling bug class to
    stranded-tail (PR #235/#238): there the tail never dispatched at all;
    here it DID dispatch and reach DONE, but its own commit (evidence file,
    tasks.md checkbox flip) is stranded on a throwaway branch a later
    worktree-cleanup pass deletes with zero trace while the run reports full
    success (reproduced 2026-08-12, brief 20260812-152318)."""

    def _init_repo(self, tmpdir):
        repo = Path(tmpdir) / "myrepo"
        repo.mkdir()
        _run_git(repo, "init", "-q", "-b", "main")
        _run_git(repo, "config", "user.email", "t@example.com")
        _run_git(repo, "config", "user.name", "T")
        (repo / "README.md").write_text("hello\n")
        _run_git(repo, "add", "README.md")
        _run_git(repo, "commit", "-q", "-m", "init")
        # Local stand-in for the remote-tracking ref detect_unreconciled_tail_evidence
        # compares against -- no real remote needed for these tests.
        _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")
        wt_base = repo.parent / f"{repo.name}-worktrees"
        wt_base.mkdir()
        return repo, wt_base

    def _add_task_worktree(self, repo, wt_base, spec_id, task_id):
        wt = wt_base / f"{spec_id}-{task_id.lower()}"
        _run_git(repo, "worktree", "add", "-B", f"{spec_id}/{task_id.lower()}", str(wt), "main")
        return wt

    def test_flags_terminal_tail_task_with_unmerged_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            wt = self._add_task_worktree(repo, wt_base, "008-x", "T022")
            (wt / "evidence.md").write_text("dry-run evidence\n")
            _run_git(wt, "add", "evidence.md")
            _run_git(wt, "commit", "-q", "-m", "tail evidence")

            findings = integrate.detect_unreconciled_tail_evidence(
                repo, "origin", "main", "008-x", wt_base,
                [_tail_task("T022", "e2e", status="done")],
            )

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["task"], "T022")
            self.assertEqual(findings[0]["worktree"], str(wt))
            self.assertTrue(findings[0]["head_sha"])

    def test_no_finding_when_task_made_no_commit(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            self._add_task_worktree(repo, wt_base, "008-x", "T022")
            # No commit made in the worktree -- HEAD is still base itself.

            findings = integrate.detect_unreconciled_tail_evidence(
                repo, "origin", "main", "008-x", wt_base,
                [_tail_task("T022", "e2e", status="done")],
            )
            self.assertEqual(findings, [])

    def test_no_pr_opened_when_task_made_no_commit(self):
        """Pins the AC end to end: a terminal tail task whose worktree HEAD
        never advanced past its stacked base produces no detect finding, and
        feeding that (empty) finding list into reconcile_unreconciled_tail_evidence
        opens no reconciliation PR -- locks in existing behavior, no
        production code changes."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            self._add_task_worktree(repo, wt_base, "008-x", "T022")
            # No commit made in the worktree -- HEAD is still base itself.
            task = _tail_task("T022", "e2e", status="done")

            findings = integrate.detect_unreconciled_tail_evidence(
                repo, "origin", "main", "008-x", wt_base, [task],
            )
            self.assertEqual(findings, [])

            run = FakeRunWithOperator.FakeRunHelper()
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.reconcile_unreconciled_tail_evidence(
                        findings, repo, "008-x", [task], "origin", "run-1", "main", None,
                    )

            self.assertEqual(result, [])
            self.assertEqual(run.find_calls("gh", "pr", "create"), [])

    def test_no_finding_once_the_commit_is_reconciled_onto_base(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            wt = self._add_task_worktree(repo, wt_base, "008-x", "T022")
            (wt / "evidence.md").write_text("dry-run evidence\n")
            _run_git(wt, "add", "evidence.md")
            _run_git(wt, "commit", "-q", "-m", "tail evidence")
            # Simulate the fix landing: merge the tail branch onto base and
            # advance the remote-tracking stand-in the same way a push would.
            _run_git(repo, "merge", "-q", "--ff-only", "008-x/t022")
            _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")

            findings = integrate.detect_unreconciled_tail_evidence(
                repo, "origin", "main", "008-x", wt_base,
                [_tail_task("T022", "e2e", status="done")],
            )
            self.assertEqual(findings, [])

    def test_pending_tail_task_not_flagged(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            wt = self._add_task_worktree(repo, wt_base, "008-x", "T022")
            (wt / "evidence.md").write_text("dry-run evidence\n")
            _run_git(wt, "add", "evidence.md")
            _run_git(wt, "commit", "-q", "-m", "tail evidence")

            findings = integrate.detect_unreconciled_tail_evidence(
                repo, "origin", "main", "008-x", wt_base,
                [_tail_task("T022", "e2e", status="pending")],
            )
            self.assertEqual(findings, [])

    def test_no_worktree_on_disk_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            findings = integrate.detect_unreconciled_tail_evidence(
                repo, "origin", "main", "008-x", wt_base,
                [_tail_task("T022", "e2e", status="done")],
            )
            self.assertEqual(findings, [])

    def test_non_tail_kind_also_flagged(self):
        """INVERTED by brief 20260815-115257. This case previously asserted
        `findings == []` -- i.e. that a DONE impl task stranded off the base
        branch was deliberately ignored. That exemption was the defect: in run
        full-1786812908, impl task 1.3 was reviewed-PASSED and journal-done but
        its commits never reached PR #419 (merged as "base: 1.1, 1.2"), and this
        detector was the one component positioned to notice and did not, while
        catching tail task 3.3 in the same run.

        Kind is now irrelevant: the ancestry check is a delivery-ledger
        invariant over every task in `coordinator.DONE`. Full coverage lives in
        `tests/orchestrator/test_delivery_ledger_invariant.py`."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            wt = self._add_task_worktree(repo, wt_base, "008-x", "T001")
            (wt / "code.py").write_text("x = 1\n")
            _run_git(wt, "add", "code.py")
            _run_git(wt, "commit", "-q", "-m", "impl change")

            findings = integrate.detect_unreconciled_evidence(
                repo, "origin", "main", "008-x", wt_base,
                [mock_task("T001", status="done")],  # kind="impl"
            )
            self.assertEqual([f["task"] for f in findings], ["T001"])

    def test_missing_remote_or_base_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo, wt_base = self._init_repo(tmpdir)
            findings = integrate.detect_unreconciled_tail_evidence(
                repo, None, None, "008-x", wt_base,
                [_tail_task("T022", "e2e", status="done")],
            )
            self.assertEqual(findings, [])


class RecordUnreconciledTailEvidence(unittest.TestCase):
    """_record_unreconciled_tail_evidence persists/clears the journal field,
    mirroring the pending_tail_tasks recording pattern."""

    def test_writes_findings_to_journal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jp = Path(tmpdir) / "journal.json"
            jp.write_text(json.dumps({"run_id": "x"}) + "\n")
            findings = [{"task": "T022", "worktree": "/x", "head_sha": "abc123"}]

            integrate._record_unreconciled_tail_evidence(str(jp), findings)

            journal = json.loads(jp.read_text())
            self.assertEqual(journal["unreconciled_tail_evidence"], findings)

    def test_clears_field_when_no_findings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            jp = Path(tmpdir) / "journal.json"
            jp.write_text(
                json.dumps(
                    {"run_id": "x", "unreconciled_tail_evidence": [{"task": "T022"}]}
                ) + "\n"
            )

            integrate._record_unreconciled_tail_evidence(str(jp), [])

            journal = json.loads(jp.read_text())
            self.assertNotIn("unreconciled_tail_evidence", journal)

    def test_no_journal_path_is_noop(self):
        integrate._record_unreconciled_tail_evidence(None, [{"task": "T022"}])  # must not raise


class ReconcileUnreconciledTailEvidence(unittest.TestCase):
    """reconcile_unreconciled_tail_evidence turns each detect_unreconciled_tail_evidence
    finding into a synthetic single-task group fed through integrate_one -- the same
    merge/push/PR/quarantine seam every impl group already goes through."""

    class _GhWorldHelper(FakeRunWithOperator.FakeRunHelper):
        """Stateful git+gh fake standing in for a real remote across repeated
        reconcile_unreconciled_tail_evidence calls: `gh pr create` makes a
        branch's later `gh pr view`/`ls-remote` report OPEN + existing, the
        way a real GitHub PR and pushed branch would -- so a second call over
        the same finding naturally reuses it instead of needing per-call
        response scripting. `conflict_branches` names task branches (e.g.
        "spec-001/t022") whose `merge --no-edit` fails, simulating a real
        merge conflict on that tail branch.
        """

        def __init__(self, remote_url="https://github.com/owner/repo.git", conflict_branches=None):
            super().__init__(remote_url=remote_url)
            self._prs: dict = {}
            self._next_pr_num = 200
            self.conflict_branches = conflict_branches or set()

        def __call__(self, *args, **kwargs):
            cmd = (
                list(args[1:])
                if args and isinstance(args[0], (str, Path))
                else (args[0] if args else [])
            )
            self.calls.append(cmd)

            if cmd[:2] == ["ls-remote", "origin"]:
                branch = cmd[-1]
                if branch in self._prs:
                    return Proc(0, f"abc123\trefs/heads/{branch}\n", "")
                return Proc(1, "", "")

            if cmd[:3] == ["gh", "pr", "view"]:
                branch = cmd[3] if len(cmd) > 3 else None
                pr = self._prs.get(branch)
                if not pr:
                    return Proc(1, "", "no pull requests found")
                return Proc(0, json.dumps({
                    "number": pr["number"],
                    "state": pr["state"],
                    "url": pr["url"],
                    "headRefName": branch,
                    "isDraft": False,
                }), "")

            if cmd[:3] == ["gh", "pr", "list"]:
                return Proc(0, "[]", "")

            if cmd[:3] == ["gh", "pr", "create"]:
                branch = cmd[cmd.index("--head") + 1]
                self._next_pr_num += 1
                url = f"https://github.com/owner/repo/pull/{self._next_pr_num}"
                self._prs[branch] = {"number": self._next_pr_num, "state": "OPEN", "url": url}
                return Proc(0, url + "\n", "")

            if "remote" in cmd and "get-url" in cmd:
                return Proc(0, self.remote_url, "")

            if cmd[:2] == ["merge", "--no-edit"] and len(cmd) > 2 \
                    and cmd[2] in self.conflict_branches:
                return Proc(1, "", "CONFLICT (content): Merge conflict in foo.py")

            if "merge" in cmd or "push" in cmd or "checkout" in cmd:
                return Proc(0, "", "")

            # git diff --quiet <target> (empty-diff-vs-base guard): default to
            # "real changes exist" (returncode 1), matching every other
            # fixture's implicit assumption that a scripted merge delivered content.
            if cmd[:2] == ["diff", "--quiet"]:
                return Proc(1, "", "")

            return Proc(0, "", "")

    def test_builds_synthetic_group_and_opens_pr_via_integrate_one(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.json"
            journal_path.write_text(json.dumps({"run_id": "run-1"}) + "\n")

            run = FakeRunWithOperator.FakeRunHelper()
            findings = [{"task": "T022", "worktree": "/x", "head_sha": "abc123"}]
            task = _tail_task("T022", "e2e", status="done")
            task["reqs"] = ["REQ-009"]

            class NoOpVerifier:
                def verify_one(self, *args, **kwargs):
                    pass

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.reconcile_unreconciled_tail_evidence(
                        findings, Path("/repo"), "spec-001", [task], "origin",
                        "run-1", "main", str(journal_path),
                        make_verifier=lambda: NoOpVerifier(),
                    )

            # Enriched contract (post-1.2/1.3): a new list carrying each finding's
            # original fields plus reconcile_state/reconcile_pr_url; input untouched.
            self.assertEqual(len(result), 1)
            enriched = result[0]
            self.assertEqual(enriched["task"], "T022")
            self.assertEqual(enriched["reconcile_state"], "opened")
            self.assertEqual(enriched["reconcile_pr_url"], "https://github.com/owner/repo/pull/123")
            self.assertNotIn("reconcile_state", findings[0])  # input list not mutated
            create_calls = run.find_calls("gh", "pr", "create")
            self.assertEqual(len(create_calls), 1)
            self.assertIn("run-1/tail-t022", create_calls[0])
            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal["groups"]["tail-t022"]["state"], "OPEN")

    def test_empty_findings_is_noop(self):
        with patch("worktrail.orchestrator.integrate._git") as mock_git:
            with patch("worktrail.orchestrator.integrate.subprocess.run") as mock_run:
                result = integrate.reconcile_unreconciled_tail_evidence(
                    [], Path("/repo"), "spec-001", [], "origin", "run-1", "main", None,
                )
            self.assertEqual(result, [])
            mock_git.assert_not_called()
            mock_run.assert_not_called()

    def test_second_call_reuses_open_pr_as_already_open(self):
        """A repeated call over the same finding/journal must reuse the PR the
        first call opened rather than creating a second one (AC: reconciliation
        is safe to retry across resumed runs)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.json"
            journal_path.write_text(json.dumps({"run_id": "run-1"}) + "\n")

            run = self._GhWorldHelper()
            findings = [{"task": "T022", "worktree": "/x", "head_sha": "abc123"}]
            task = _tail_task("T022", "e2e", status="done")

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    first = integrate.reconcile_unreconciled_tail_evidence(
                        findings, Path("/repo"), "spec-001", [task], "origin",
                        "run-1", "main", str(journal_path),
                    )
                    second = integrate.reconcile_unreconciled_tail_evidence(
                        findings, Path("/repo"), "spec-001", [task], "origin",
                        "run-1", "main", str(journal_path),
                    )

            self.assertEqual(first[0]["reconcile_state"], "opened")
            self.assertEqual(second[0]["reconcile_state"], "already-open")
            self.assertEqual(second[0]["reconcile_pr_url"], first[0]["reconcile_pr_url"])
            create_calls = run.find_calls("gh", "pr", "create")
            self.assertEqual(len(create_calls), 1, "second call must not open a second PR")

    def test_pr_already_merged_returns_merged(self):
        """A finding whose PR has since merged (per gh pr view) is reported as
        'merged', not re-integrated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.json"
            journal_path.write_text(json.dumps({"run_id": "run-1"}) + "\n")

            gb = "run-1/tail-t022"
            merged_url = "https://github.com/owner/repo/pull/77"
            pr_view = {
                gb: [{
                    "number": 77,
                    "state": "MERGED",
                    "url": merged_url,
                    "headRefName": gb,
                    "isDraft": False,
                }]
            }
            run = FakeRunWithOperator.FakeRunHelper(pr_view_responses=pr_view)
            findings = [{"task": "T022", "worktree": "/x", "head_sha": "abc123"}]
            task = _tail_task("T022", "e2e", status="done")

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.reconcile_unreconciled_tail_evidence(
                        findings, Path("/repo"), "spec-001", [task], "origin",
                        "run-1", "main", str(journal_path),
                    )

            self.assertEqual(result[0]["reconcile_state"], "merged")
            self.assertEqual(result[0]["reconcile_pr_url"], merged_url)
            self.assertEqual(run.find_calls("gh", "pr", "create"), [])
            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal["groups"]["tail-t022"]["state"], "MERGED")

    def test_merge_conflict_quarantines_with_reason(self):
        """A finding whose tail branch conflicts with base on merge is
        quarantined with the same QUARANTINE_MERGE_CONFLICT reason used by
        every other group's merge-conflict path (reconciliation reuses
        existing conflict handling, it does not invent its own)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.json"
            journal_path.write_text(json.dumps({"run_id": "run-1"}) + "\n")

            run = self._GhWorldHelper(conflict_branches={"spec-001/t022"})
            findings = [{"task": "T022", "worktree": "/x", "head_sha": "abc123"}]
            task = _tail_task("T022", "e2e", status="done")

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.reconcile_unreconciled_tail_evidence(
                        findings, Path("/repo"), "spec-001", [task], "origin",
                        "run-1", "main", str(journal_path),
                    )

            self.assertEqual(result[0]["reconcile_state"], "quarantined")
            self.assertEqual(result[0]["reconcile_pr_url"], "")
            self.assertEqual(run.find_calls("gh", "pr", "create"), [])
            journal = json.loads(journal_path.read_text())
            record = journal["groups"]["tail-t022"]
            self.assertEqual(record["state"], "QUARANTINED")
            self.assertEqual(record["quarantine_reason"], integrate.QUARANTINE_MERGE_CONFLICT)

    def test_two_findings_reconciled_independently(self):
        """Two findings in the same call are reconciled independently: one
        quarantined (merge conflict), one opened cleanly -- both outcomes
        reflected correctly regardless of list order."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "journal.json"
            journal_path.write_text(json.dumps({"run_id": "run-1"}) + "\n")

            run = self._GhWorldHelper(conflict_branches={"spec-001/t022"})
            findings = [
                {"task": "T022", "worktree": "/x", "head_sha": "abc123"},
                {"task": "T023", "worktree": "/y", "head_sha": "def456"},
            ]
            tasks = [
                _tail_task("T022", "e2e", status="done"),
                _tail_task("T023", "cleanup", status="done"),
            ]

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.reconcile_unreconciled_tail_evidence(
                        findings, Path("/repo"), "spec-001", tasks, "origin",
                        "run-1", "main", str(journal_path),
                    )

            by_task = {r["task"]: r for r in result}
            self.assertEqual(by_task["T022"]["reconcile_state"], "quarantined")
            self.assertEqual(by_task["T022"]["reconcile_pr_url"], "")
            self.assertEqual(by_task["T023"]["reconcile_state"], "opened")
            self.assertTrue(
                by_task["T023"]["reconcile_pr_url"].startswith("https://github.com/owner/repo/pull/")
            )

            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal["groups"]["tail-t022"]["state"], "QUARANTINED")
            self.assertEqual(journal["groups"]["tail-t023"]["state"], "OPEN")


class QuarantinedTailGroupPickedUpByQuarantineSelfcheck(unittest.TestCase):
    """Regression test for design.md's "no dashboard change needed" claim: a
    synthetic `tail-<task-id>` group that reconcile_unreconciled_tail_evidence
    quarantines is just another QUARANTINED entry in `journal["groups"]` --
    quarantine_selfcheck.check_repo() needs no awareness of the tail-specific
    naming to surface it, because it keys off `state`, not group-name shape.
    """

    def test_quarantined_tail_group_surfaces_as_finding(self):
        from worktrail.router import quarantine_selfcheck

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "myrepo"
            (repo / ".git").mkdir(parents=True)
            worktrees_dir = repo.parent / "myrepo-worktrees"
            worktrees_dir.mkdir()
            journal_path = worktrees_dir / "run-spec-001.json"
            journal_path.write_text(json.dumps({"run_id": "run-1"}) + "\n")

            run = ReconcileUnreconciledTailEvidence._GhWorldHelper(
                conflict_branches={"spec-001/t022"}
            )
            findings = [{"task": "T022", "worktree": "/x", "head_sha": "abc123"}]
            task = _tail_task("T022", "e2e", status="done")

            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    integrate.reconcile_unreconciled_tail_evidence(
                        findings, repo, "spec-001", [task], "origin",
                        "run-1", "main", str(journal_path),
                    )

            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal["groups"]["tail-t022"]["state"], "QUARANTINED")

            result = quarantine_selfcheck.check_repo(repo)

            self.assertEqual(len(result["findings"]), 1)
            finding = result["findings"][0]
            self.assertEqual(finding["spec_id"], "spec-001")
            self.assertEqual(finding["group"], "tail-t022")
            self.assertEqual(finding["quarantine_reason"], integrate.QUARANTINE_MERGE_CONFLICT)
            self.assertEqual(result["reconciled"], [])
            self.assertEqual(result["resumable"], [])


class IntegrationSmokeTest(unittest.TestCase):
    """Option-3: policy integrate_smoke_cmd gate on a group's integration branch."""

    def test_run_smoke_passes_on_exit_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, detail = integrate._run_integration_smoke(Path(tmp), "base", "true")
            self.assertTrue(ok)
            self.assertEqual(detail, "ok")

    def test_run_smoke_fails_on_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, detail = integrate._run_integration_smoke(Path(tmp), "base", "echo boom >&2; exit 3")
            self.assertFalse(ok)
            self.assertIn("exit 3", detail)
            self.assertIn("boom", detail)

    def test_none_smoke_cmd_skips(self):
        # No smoke_cmd → integrate_one never invokes the smoke runner (default path).
        with patch("worktrail.orchestrator.integrate._run_integration_smoke") as runner:
            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses={
                    "full-s0/base": [
                        {"number": 1, "state": "OPEN", "url": "u", "headRefName": "full-s0/base"}
                    ]
                },
                ls_remote_responses={},
            )
            with patch("worktrail.orchestrator.integrate._git", side_effect=run), \
                 patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                integrate.integrate_one(
                    mock_group("base", ["T001"]), Path("/repo"), "spec-001",
                    [mock_task("T001")], "origin", "full-s0", "main",
                    None, {"T001": "done"}, {}, {},
                )
            runner.assert_not_called()

    def test_smoke_failure_quarantines_group(self):
        # A failing smoke run on a freshly-built branch quarantines the group and
        # never pushes or opens a PR.
        with tempfile.TemporaryDirectory() as iwtmp:
            run = FakeRunWithOperator.FakeRunHelper(
                pr_view_responses={},
                ls_remote_responses={},  # branch absent → build path → smoke runs
            )

            @contextlib.contextmanager
            def fake_iw(repo, branch, start_ref, git_lock=None):
                yield Path(iwtmp)

            quarantined: dict = {}
            with patch("worktrail.orchestrator.integrate._git", side_effect=run), \
                 patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run), \
                 patch("worktrail.orchestrator.integrate._integration_worktree", fake_iw), \
                 patch("worktrail.orchestrator.integrate._run_integration_smoke", return_value=(False, "exit 1: nope")):
                result = integrate.integrate_one(
                    mock_group("base", ["T001"]), Path("/repo"), "spec-001",
                    [mock_task("T001")], "origin", "full-s1", "main",
                    None, {"T001": "done"}, {}, quarantined,
                    smoke_cmd="pytest -q",
                )
            self.assertIsNone(result)
            self.assertIn("base", quarantined)
            self.assertIn("smoke test failed", quarantined["base"])
            # No push and no PR create after a smoke failure.
            self.assertEqual(run.find_calls("gh", "pr", "create"), [])
            self.assertEqual([c for c in run.calls if c[:1] == ["push"]], [])


class DepBranchGonePRBase(unittest.TestCase):
    """Regression: when a dependent group's dependency branch was squash-merged and
    deleted mid-run, the fallback rebuilds the group off the pre-squash commit (a valid
    worktree start-ref) — but the PR must still be opened against the real *base branch*,
    not that commit SHA. GitHub rejects a commit as a PR base ('Base ref must be a
    branch'), which previously quarantined the whole dependent group."""

    GONE_BRANCH = "full-X/base"
    FAKE_MERGE_BASE = "deadbeefcafef00ddeadbeefcafef00ddeadbeef"

    class _Helper(FakeRunWithOperator.FakeRunHelper):
        def __call__(self, *args, **kwargs):
            cmd = (
                list(args[1:])
                if args and isinstance(args[0], (str, Path))
                else (args[0] if args else [])
            )
            # dependency branch is gone → rev-parse --verify fails for it (triggers fallback)
            if cmd[:2] == ["rev-parse", "--verify"] and len(cmd) > 2 \
                    and cmd[2] == DepBranchGonePRBase.GONE_BRANCH:
                self.calls.append(cmd)
                return Proc(1, "", "fatal: Needed a single revision")
            # merge-base of first task and origin/base → the pre-squash commit SHA
            if cmd[:1] == ["merge-base"]:
                self.calls.append(cmd)
                return Proc(0, DepBranchGonePRBase.FAKE_MERGE_BASE + "\n", "")
            return super().__call__(*args, **kwargs)

    def test_dep_branch_gone_pr_base_is_branch_not_commit(self):
        run = self._Helper(pr_view_responses={}, pr_list_responses={}, ls_remote_responses={})
        # base group integrated earlier this run, then squash-merged + branch deleted.
        group_branch = {"base": self.GONE_BRANCH}
        g = mock_group("feature-1", ["T010"], depends_on=["base"])

        with patch("worktrail.orchestrator.integrate.coordinator.deliverable_subset", return_value=(["T010"], [])):
            with patch("worktrail.orchestrator.integrate._git", side_effect=run):
                with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run):
                    result = integrate.integrate_one(
                        g,
                        Path("/repo"),
                        "spec-001",
                        [mock_task("T010")],
                        "origin",
                        "full-X",
                        "main",
                        None,
                        {"T010": "done"},
                        group_branch,
                        {},
                    )

        # The fallback must have fired (dep branch ref verified-missing).
        self.assertTrue(
            run.find_calls("rev-parse", "--verify", self.GONE_BRANCH),
            "expected a rev-parse --verify on the gone dependency branch",
        )
        # Exactly one PR created, with --base = the real base branch (not the commit SHA).
        create_calls = run.find_calls("gh", "pr", "create")
        self.assertEqual(len(create_calls), 1, "should create the dependent group's PR")
        cmd = create_calls[0]
        base_arg = cmd[cmd.index("--base") + 1]
        self.assertEqual(
            base_arg, "main",
            "PR --base must be the base branch, not the pre-squash commit SHA",
        )
        self.assertNotEqual(base_arg, self.FAKE_MERGE_BASE)
        # Returned tuple's base element is likewise the branch, not the commit.
        self.assertIsNotNone(result)
        self.assertEqual(result[1], "main")


class AssemblyConflictResolvePath(unittest.TestCase):
    """Regression: on assembly-time merge conflict, a bounded resolve worker is
    dispatched before quarantine.

    Mirrors DepBranchGonePRBase: the conflict is scripted via a fake _git runner
    and the spawn callable is injected so no real claude -p call occurs.
    """

    @contextlib.contextmanager
    def _fake_iw(self, tmp_path):
        @contextlib.contextmanager
        def _ctx(repo, branch, start_ref, git_lock=None):
            yield tmp_path
        yield _ctx

    def _run_with_merge_conflict(self, conflict_on_no_edit=True):
        """Returns a _git fake that reports a conflict on --no-edit merges."""
        class _Helper(FakeRunWithOperator.FakeRunHelper):
            def __call__(self, *args, **kwargs):
                cmd = (
                    list(args[1:])
                    if args and isinstance(args[0], (str, Path))
                    else (args[0] if args else [])
                )
                if "merge" in cmd and "--no-edit" in cmd and conflict_on_no_edit:
                    self.calls.append(cmd)
                    return Proc(1, "", "CONFLICT (content): Merge conflict in foo.py")
                return super().__call__(*args, **kwargs)
        return _Helper(pr_view_responses={}, ls_remote_responses={})

    def test_resolve_worker_succeeds_group_gets_pr(self):
        """Worker returns success → conflict resolved, group receives a PR."""
        success_json = (
            '{"task": "base", "step": "assembly-resolve", "status": "success",'
            ' "head_sha": "abc123", "files_touched": ["foo.py"], "tests": "none",'
            ' "notes": "resolved"}'
        )

        def fake_spawn(prompt, wt):
            return success_json

        with tempfile.TemporaryDirectory() as iwtmp:
            run = self._run_with_merge_conflict(conflict_on_no_edit=True)
            quarantined: dict = {}

            with self._fake_iw(Path(iwtmp)) as fake_iw_ctx, \
                 patch("worktrail.orchestrator.integrate._git", side_effect=run), \
                 patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run), \
                 patch("worktrail.orchestrator.integrate._integration_worktree", fake_iw_ctx), \
                 patch("worktrail.orchestrator.integrate.coordinator.deliverable_subset",
                       return_value=(["T001"], [])):
                result = integrate.integrate_one(
                    mock_group("base", ["T001"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001")],
                    "origin",
                    "run-ar0",
                    "main",
                    None,
                    {"T001": "done"},
                    {},
                    quarantined,
                    assembly_resolve_spawn=fake_spawn,
                )

        self.assertNotIn("base", quarantined, "resolved group must not be quarantined")
        self.assertIsNotNone(result, "resolved group must receive a PR tuple")
        create_calls = run.find_calls("gh", "pr", "create")
        self.assertEqual(len(create_calls), 1, "exactly one PR must be opened")

    def test_resolve_worker_fails_quarantines_group(self):
        """Worker returns failed → group quarantined after strikes exhausted, no PR."""
        failed_json = (
            '{"task": "base", "step": "assembly-resolve", "status": "failed",'
            ' "head_sha": "", "files_touched": [], "tests": "none",'
            ' "notes": "could not resolve"}'
        )

        def fake_spawn(prompt, wt):
            return failed_json

        with tempfile.TemporaryDirectory() as iwtmp:
            run = self._run_with_merge_conflict(conflict_on_no_edit=True)
            quarantined: dict = {}

            with self._fake_iw(Path(iwtmp)) as fake_iw_ctx, \
                 patch("worktrail.orchestrator.integrate._git", side_effect=run), \
                 patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run), \
                 patch("worktrail.orchestrator.integrate._integration_worktree", fake_iw_ctx), \
                 patch("worktrail.orchestrator.integrate.coordinator.deliverable_subset",
                       return_value=(["T001"], [])):
                result = integrate.integrate_one(
                    mock_group("base", ["T001"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001")],
                    "origin",
                    "run-ar1",
                    "main",
                    None,
                    {"T001": "done"},
                    {},
                    quarantined,
                    assembly_resolve_spawn=fake_spawn,
                )

        self.assertIn("base", quarantined, "failed-worker group must be quarantined")
        self.assertIn("merge conflict", quarantined["base"])
        self.assertIsNone(result, "failed-worker group must not receive a PR")
        create_calls = run.find_calls("gh", "pr", "create")
        self.assertEqual(create_calls, [], "no PR must be opened for a quarantined group")

    def _run_with_conflict_and_salvage_state(self, merge_head_exists, dirty=False,
                                             conflicted="foo.py"):
        """Conflict fake that additionally scripts the git state the salvage
        check inspects: MERGE_HEAD presence, porcelain cleanliness, and the
        conflicted-file list."""
        class _Helper(FakeRunWithOperator.FakeRunHelper):
            def __call__(self, *args, **kwargs):
                cmd = (
                    list(args[1:])
                    if args and isinstance(args[0], (str, Path))
                    else (args[0] if args else [])
                )
                if "merge" in cmd and "--no-edit" in cmd:
                    self.calls.append(cmd)
                    return Proc(1, "", "CONFLICT (content): Merge conflict in foo.py")
                if cmd[:2] == ["diff", "--name-only"] and "--diff-filter=U" in cmd:
                    self.calls.append(cmd)
                    return Proc(0, conflicted + "\n" if conflicted else "", "")
                if "rev-parse" in cmd and "MERGE_HEAD" in cmd:
                    self.calls.append(cmd)
                    return Proc(0 if merge_head_exists else 1, "", "")
                if cmd[:2] == ["status", "--porcelain"]:
                    self.calls.append(cmd)
                    return Proc(0, " M foo.py\n" if dirty else "", "")
                return super().__call__(*args, **kwargs)
        return _Helper(pr_view_responses={}, ls_remote_responses={})

    def _integrate_with_spawn(self, run, iwtmp, fake_spawn, run_id):
        quarantined: dict = {}
        with self._fake_iw(Path(iwtmp)) as fake_iw_ctx, \
             patch("worktrail.orchestrator.integrate._git", side_effect=run), \
             patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run), \
             patch("worktrail.orchestrator.integrate._integration_worktree", fake_iw_ctx), \
             patch("worktrail.orchestrator.integrate.coordinator.deliverable_subset",
                   return_value=(["T001"], [])):
            result = integrate.integrate_one(
                mock_group("base", ["T001"]),
                Path("/repo"),
                "spec-001",
                [mock_task("T001")],
                "origin",
                run_id,
                "main",
                None,
                {"T001": "done"},
                {},
                quarantined,
                assembly_resolve_spawn=fake_spawn,
            )
        return result, quarantined

    def test_unparseable_report_salvaged_when_git_state_resolved(self):
        """Unparseable report-back + concluded merge, clean tree, no markers →
        salvaged as resolved (mirrors implement/fix git-commit salvage)."""
        def fake_spawn(prompt, wt):
            return "no report-back JSON block here"

        with tempfile.TemporaryDirectory() as iwtmp:
            (Path(iwtmp) / "foo.py").write_text("cleanly resolved\n")
            run = self._run_with_conflict_and_salvage_state(merge_head_exists=False)
            result, quarantined = self._integrate_with_spawn(
                run, iwtmp, fake_spawn, "run-ar3"
            )

        self.assertNotIn("base", quarantined, "salvaged group must not be quarantined")
        self.assertIsNotNone(result, "salvaged group must receive a PR tuple")
        self.assertEqual(len(run.find_calls("gh", "pr", "create")), 1)

    def test_unparseable_report_merge_still_open_quarantines(self):
        """Unparseable report-back with MERGE_HEAD still present → no salvage."""
        def fake_spawn(prompt, wt):
            return "no report-back JSON block here"

        with tempfile.TemporaryDirectory() as iwtmp:
            run = self._run_with_conflict_and_salvage_state(merge_head_exists=True)
            result, quarantined = self._integrate_with_spawn(
                run, iwtmp, fake_spawn, "run-ar4"
            )

        self.assertIn("base", quarantined)
        self.assertIsNone(result)

    def test_unparseable_report_markers_left_quarantines(self):
        """Unparseable report-back, merge concluded but conflict markers were
        committed into a previously-conflicted file → no salvage."""
        def fake_spawn(prompt, wt):
            return "no report-back JSON block here"

        with tempfile.TemporaryDirectory() as iwtmp:
            (Path(iwtmp) / "foo.py").write_text("<<<<<<< HEAD\nours\n=======\ntheirs\n")
            run = self._run_with_conflict_and_salvage_state(merge_head_exists=False)
            result, quarantined = self._integrate_with_spawn(
                run, iwtmp, fake_spawn, "run-ar5"
            )

        self.assertIn("base", quarantined)
        self.assertIsNone(result)

    def test_parsed_failure_report_is_trusted_over_git_state(self):
        """A worker that explicitly reports status:failed is trusted even when
        the git state looks resolved — salvage covers unparseable reports only."""
        failed_json = (
            '{"task": "base", "step": "assembly-resolve", "status": "failed",'
            ' "head_sha": "", "files_touched": [], "tests": "none",'
            ' "notes": "could not resolve"}'
        )

        def fake_spawn(prompt, wt):
            return failed_json

        with tempfile.TemporaryDirectory() as iwtmp:
            (Path(iwtmp) / "foo.py").write_text("looks resolved\n")
            run = self._run_with_conflict_and_salvage_state(merge_head_exists=False)
            result, quarantined = self._integrate_with_spawn(
                run, iwtmp, fake_spawn, "run-ar6"
            )

        self.assertIn("base", quarantined)
        self.assertIsNone(result)

    def test_no_spawn_quarantines_immediately(self):
        """Without assembly_resolve_spawn, conflict → immediate quarantine (current behavior)."""
        with tempfile.TemporaryDirectory() as iwtmp:
            run = self._run_with_merge_conflict(conflict_on_no_edit=True)
            quarantined: dict = {}

            with self._fake_iw(Path(iwtmp)) as fake_iw_ctx, \
                 patch("worktrail.orchestrator.integrate._git", side_effect=run), \
                 patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=run), \
                 patch("worktrail.orchestrator.integrate._integration_worktree", fake_iw_ctx), \
                 patch("worktrail.orchestrator.integrate.coordinator.deliverable_subset",
                       return_value=(["T001"], [])):
                result = integrate.integrate_one(
                    mock_group("base", ["T001"]),
                    Path("/repo"),
                    "spec-001",
                    [mock_task("T001")],
                    "origin",
                    "run-ar2",
                    "main",
                    None,
                    {"T001": "done"},
                    {},
                    quarantined,
                )  # no assembly_resolve_spawn

        self.assertIn("base", quarantined, "no-spawn conflict must quarantine immediately")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
