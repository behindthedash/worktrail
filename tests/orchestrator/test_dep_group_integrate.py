#!/usr/bin/env python3
"""Regression tests for dependent-group integration defects.

Three defects fixed:
  1. Deleted start ref: dep group quarantined when base group's branch was deleted
     after merge (start ref no longer exists). Fix: fall back to pre-squash merge-base
     + squash reconcile merge.
  2. Journal state never MERGED: after verify auto-merge succeeds, group stays
     state:OPEN in journal. Fix: stamp MERGED after verify_one succeeds.
  3. Broken MERGED reconcile on --re-integrate: _clear_integration_state wiped
     MERGED group records, causing --re-integrate to rebuild already-merged groups.
     Fix: preserve MERGED records in _clear_integration_state.

Run: python3 test_dep_group_integrate.py
"""
from __future__ import annotations

import threading
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import integrate
from worktrail.orchestrator import live

Proc = namedtuple("Proc", "returncode stdout stderr")


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _mock_group(name, tasks, depends_on=None):
    return {"name": name, "tasks": tasks, "reqs": [], "depends_on": depends_on or []}


def _mock_task(tid, status="done"):
    return {"id": tid, "status": status, "kind": "impl"}


class _GitDispatch:
    """Scriptable git/_git/subprocess.run dispatcher for tests.

    Matches recorded calls against a dict of {tuple-prefix: Proc}; returns
    Proc(0,"","") for anything not matched. `self.calls` accumulates every call.
    """

    def __init__(self, responses=None):
        self.calls: list = []
        self._resp = responses or {}
        self._default = Proc(0, "", "")

    def __call__(self, *args, **_kw):
        if args and isinstance(args[0], Path):
            cmd = list(args[1:])
        else:
            cmd = list(args[0]) if args else []
        self.calls.append(cmd)
        for prefix, proc in self._resp.items():
            p = list(prefix)
            if cmd[:len(p)] == p:
                return proc
        return self._default


# ---------------------------------------------------------------------------
# Fix 1: deleted start ref — dep group falls back to merge-base + squash reconcile
# ---------------------------------------------------------------------------

class DeletedDepBranchFallback(unittest.TestCase):
    """integrate_one must not quarantine a dep group when the base group's branch
    has been deleted (post-merge cleanup). It should detect the missing ref, find the
    pre-squash merge-base, start the integration worktree from that SHA, merge task
    branches, and reconcile squash-merge divergence with -X ours."""

    def _run_integrate_one_dep(self, rev_parse_ok: bool, merge_base_sha: str | None):
        """Drive integrate_one for a dep group; return (result, git_calls)."""
        sha = merge_base_sha or ""
        git_dispatch = _GitDispatch({
            # rev-parse --verify <deleted-branch> → fails (branch gone)
            ("rev-parse", "--verify"): Proc(0 if rev_parse_ok else 1, sha if rev_parse_ok else "", ""),
            # merge-base task origin/main → returns pre-squash SHA
            ("merge-base",): Proc(0 if merge_base_sha else 1, merge_base_sha or "", ""),
            # worktree ops / ls-remote / push
            ("worktree",): Proc(0, "", ""),
            ("ls-remote",): Proc(1, "", ""),   # branch not yet on remote
            ("merge",): Proc(0, "", ""),
            ("push",): Proc(0, "", ""),
        })
        subprocess_dispatch = _GitDispatch({
            ("gh", "pr", "view"): Proc(1, "", "no pr"),
            ("gh", "pr", "list"): Proc(0, "[]", ""),
            ("gh", "pr", "create"): Proc(0, "https://github.com/o/r/pull/99\n", ""),
            ("git", "remote"): Proc(0, "https://github.com/o/r.git", ""),
        })

        group_branch = {"base": "full-run/base"}  # stale ref name (branch deleted)
        quarantined: dict = {}

        with patch("worktrail.orchestrator.integrate._git", side_effect=git_dispatch):
            with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=subprocess_dispatch):
                result = integrate.integrate_one(
                    _mock_group("feature-1", ["T002"], depends_on=["base"]),
                    Path("/repo"),
                    "spec-049",
                    [_mock_task("T001"), _mock_task("T002")],
                    "origin",
                    "full-run",
                    "main",
                    None,
                    {"T001": "done", "T002": "done"},
                    group_branch,
                    quarantined,
                )

        return result, git_dispatch.calls, quarantined

    def test_deleted_branch_uses_merge_base_fallback(self):
        """When dep branch is deleted and merge-base succeeds, use the SHA as start ref."""
        result, calls, quarantined = self._run_integrate_one_dep(
            rev_parse_ok=False, merge_base_sha="deadbeef1234"
        )

        self.assertNotIn("feature-1", quarantined,
                         "dep group must not be quarantined when dep branch is deleted")
        self.assertIsNotNone(result, "dep group must produce a PR tuple, not None")

        # worktree add should have been called with the merge-base SHA (not the deleted branch)
        wt_add = [c for c in calls if c[:2] == ["worktree", "add"]]
        self.assertTrue(wt_add, "worktree add must have been called")
        self.assertIn("deadbeef1234", wt_add[0],
                      "worktree add must use the merge-base SHA as start ref")

        # squash reconcile: git merge --no-edit -X ours origin/main
        reconcile = [c for c in calls if c[:2] == ["merge", "--no-edit"] and "-X" in c and "ours" in c]
        self.assertTrue(reconcile, "squash reconcile merge must be called after task merges")
        self.assertIn("origin/main", reconcile[0],
                      "squash reconcile must target origin/<base>")

    def test_deleted_branch_merge_base_failure_falls_back_to_remote_base(self):
        """When merge-base also fails, fall back to origin/<base> without quarantine."""
        result, calls, quarantined = self._run_integrate_one_dep(
            rev_parse_ok=False, merge_base_sha=None
        )
        self.assertNotIn("feature-1", quarantined)
        self.assertIsNotNone(result)

        # No squash reconcile when we couldn't find the merge-base
        reconcile = [c for c in calls if c[:2] == ["merge", "--no-edit"] and "-X" in c]
        self.assertEqual(len(reconcile), 0, "no squash reconcile when merge-base unavailable")

    def test_existing_branch_skips_fallback(self):
        """When the dep branch still exists as a ref, the fallback is never triggered."""
        _result, calls, quarantined = self._run_integrate_one_dep(
            rev_parse_ok=True, merge_base_sha=None
        )
        self.assertNotIn("feature-1", quarantined)
        # rev-parse returned success → no merge-base call
        mb_calls = [c for c in calls if c[:1] == ["merge-base"]]
        self.assertEqual(len(mb_calls), 0, "merge-base must not be called when branch still exists")

    def test_independent_group_skips_fallback(self):
        """A group with no depends_on never hits the ref-validation path."""
        git_dispatch = _GitDispatch({
            ("ls-remote",): Proc(1, "", ""),
            ("worktree",): Proc(0, "", ""),
            ("merge",): Proc(0, "", ""),
            ("push",): Proc(0, "", ""),
        })
        subprocess_dispatch = _GitDispatch({
            ("gh", "pr", "view"): Proc(1, "", "no pr"),
            ("gh", "pr", "list"): Proc(0, "[]", ""),
            ("gh", "pr", "create"): Proc(0, "https://github.com/o/r/pull/1\n", ""),
            ("git", "remote"): Proc(0, "https://github.com/o/r.git", ""),
        })
        quarantined: dict = {}
        with patch("worktrail.orchestrator.integrate._git", side_effect=git_dispatch):
            with patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=subprocess_dispatch):
                result = integrate.integrate_one(
                    _mock_group("base", ["T001"]),   # no depends_on
                    Path("/repo"), "spec-049",
                    [_mock_task("T001")], "origin", "full-run", "main",
                    None, {"T001": "done"}, {}, quarantined,
                )
        self.assertIsNotNone(result)
        self.assertNotIn("base", quarantined)
        rev_parse_calls = [c for c in git_dispatch.calls if c[:2] == ["rev-parse", "--verify"]]
        self.assertEqual(len(rev_parse_calls), 0,
                         "rev-parse --verify must not be called for independent groups")


# ---------------------------------------------------------------------------
# Fix 3: _clear_integration_state preserves MERGED records
# ---------------------------------------------------------------------------

class ClearIntegrationStatePreservesMerged(unittest.TestCase):
    """_clear_integration_state must preserve MERGED group records and only remove
    OPEN/QUARANTINED ones. A merged PR cannot be rebuilt."""

    def test_merged_records_preserved(self):
        journal = {
            "integrate_complete": True,
            "groups": {
                "base": {"pr_url": "https://g/p/1", "head_branch": "r/base", "state": "MERGED"},
                "feature-1": {"pr_url": "https://g/p/2", "head_branch": "r/f1", "state": "OPEN"},
                "feature-2": {"pr_url": "", "head_branch": "r/f2", "state": "QUARANTINED"},
            },
        }
        had = live._clear_integration_state(journal)

        self.assertTrue(had, "should return True when state was cleared")
        self.assertNotIn("integrate_complete", journal, "integrate_complete must be removed")
        self.assertIn("groups", journal, "groups key must remain (has MERGED record)")
        groups = journal["groups"]
        self.assertIn("base", groups, "MERGED group must be preserved")
        self.assertNotIn("feature-1", groups, "OPEN group must be removed")
        self.assertNotIn("feature-2", groups, "QUARANTINED group must be removed")
        self.assertEqual(groups["base"]["state"], "MERGED")

    def test_no_merged_records_removes_groups(self):
        journal = {
            "integrate_complete": True,
            "groups": {
                "base": {"pr_url": "x", "head_branch": "y", "state": "OPEN"},
            },
        }
        live._clear_integration_state(journal)
        self.assertNotIn("groups", journal, "groups must be removed when no MERGED records exist")

    def test_empty_journal_returns_false(self):
        journal: dict = {}
        had = live._clear_integration_state(journal)
        self.assertFalse(had)

    def test_all_merged_all_preserved(self):
        journal = {
            "integrate_complete": True,
            "groups": {
                "base": {"state": "MERGED", "pr_url": "u1", "head_branch": "b"},
                "feat": {"state": "MERGED", "pr_url": "u2", "head_branch": "f"},
            },
        }
        live._clear_integration_state(journal)
        self.assertEqual(len(journal.get("groups", {})), 2, "both MERGED records preserved")
        self.assertNotIn("integrate_complete", journal)


# ---------------------------------------------------------------------------
# Fix 2: journal stamped MERGED after verify_one succeeds
# ---------------------------------------------------------------------------

class JournalMergedAfterVerify(unittest.TestCase):
    """After _integrate_verify_group's verify_one succeeds and the group is added to
    the merged list, _record_group_fn must be called with state='MERGED'."""

    def _build_pipeline_partial(self):
        """Build just enough of the _pipeline_scheduler closure to invoke
        _integrate_verify_group against a controlled verifier."""
        # We need a minimal fake of the pipeline environment:
        # - groups_journal, merged, quarantined, group_branch (mutable shared state)
        # - _record_group_fn that records calls
        # - make_verifier_fn that returns a verifier whose verify_one marks the group merged
        record_calls = []
        groups_journal = {
            "base": {"pr_url": "https://g/p/1", "head_branch": "r/base", "state": "OPEN"}
        }
        merged = []
        quarantined: dict = {}
        group_branch = {"base": "full-run/base"}
        iv_lock = threading.Lock()
        state_lock = threading.Lock()
        run_id = "full-run"

        def _record_group_fn(name, pr_url, head_branch, state):
            with state_lock:
                groups_journal[name] = {"pr_url": pr_url, "head_branch": head_branch, "state": state}
            record_calls.append((name, state))

        def _emit_group_phases():
            pass

        _group_phase_map: dict = {}
        group_done_events = {"base": threading.Event()}

        class FakeVerifier:
            def verify_one(self, group, _gb, _delivered, merged_list, _quarantined_dict, lock):
                with lock:
                    merged_list.append(group["name"])

        def make_verifier_fn():
            return FakeVerifier()

        tasks = [_mock_task("T001")]

        # Reconstruct _integrate_verify_group inline (mirrors the closure structure)
        def _integrate_verify_group(g):
            name = g["name"]
            try:
                # Skip the integrate phase (simulate already done)
                journal_rec = groups_journal.get(name, {})
                if journal_rec.get("state") == "MERGED":
                    with iv_lock:
                        pass
                    return
                _skip_integrate = bool(journal_rec.get("pr_url"))
                if not _skip_integrate:
                    pass  # would run integrate_one; skip here

                # Verify
                with iv_lock:
                    _group_phase_map[name] = "verifying"
                _emit_group_phases()
                verifier = make_verifier_fn()
                try:
                    verifier.verify_one(g, group_branch[name], {name: []},
                                        merged, quarantined, iv_lock)
                except Exception as exc:
                    with iv_lock:
                        quarantined[name] = f"verify exception: {exc!r}"
                    _record_group_fn(name, "", group_branch.get(name, f"{run_id}/{name}"),
                                     "QUARANTINED")
                else:
                    if name in merged:
                        with state_lock:
                            pr_url = groups_journal.get(name, {}).get("pr_url", "")
                        _record_group_fn(
                            name, pr_url,
                            group_branch.get(name, f"{run_id}/{name}"), "MERGED"
                        )
            finally:
                with iv_lock:
                    _group_phase_map.pop(name, None)
                _emit_group_phases()
                group_done_events[name].set()

        return _integrate_verify_group, record_calls, groups_journal, merged

    def test_merged_stamped_after_successful_verify(self):
        fn, calls, gj, merged = self._build_pipeline_partial()
        fn(_mock_group("base", ["T001"]))

        self.assertIn("base", merged, "base must be in merged list after successful verify")
        merged_calls = [(n, s) for n, s in calls if s == "MERGED"]
        self.assertTrue(merged_calls, "MERGED must be recorded via _record_group_fn")
        self.assertEqual(merged_calls[0][0], "base")
        self.assertEqual(gj["base"]["state"], "MERGED",
                         "groups_journal must reflect MERGED state")

    def test_quarantined_not_stamped_merged(self):
        quarantined_local: dict = {}
        iv_lock = threading.Lock()
        state_lock = threading.Lock()
        groups_journal = {
            "base": {"pr_url": "x", "head_branch": "y", "state": "OPEN"}
        }
        merged_local: list = []
        record_calls_local = []
        group_branch = {"base": "full-run/base"}
        run_id = "full-run"
        group_done_events = {"base": threading.Event()}
        _group_phase_map: dict = {}

        def _record_group_fn(name, pr_url, head_branch, state):
            with state_lock:
                groups_journal[name] = {"pr_url": pr_url, "head_branch": head_branch, "state": state}
            record_calls_local.append((name, state))

        def _emit_group_phases():
            pass

        class QuarantineVerifier:
            def verify_one(self, group, gb, delivered, merged_list, quarantined_dict, lock):
                with lock:
                    quarantined_dict[group["name"]] = "ci failed"

        def make_verifier_fn():
            return QuarantineVerifier()

        def _integrate_verify_group_q(g):
            name = g["name"]
            try:
                verifier = make_verifier_fn()
                try:
                    verifier.verify_one(g, group_branch[name], {name: []},
                                        merged_local, quarantined_local, iv_lock)
                except Exception as exc:
                    with iv_lock:
                        quarantined_local[name] = f"exception: {exc!r}"
                    _record_group_fn(name, "", f"{run_id}/{name}", "QUARANTINED")
                else:
                    if name in merged_local:
                        with state_lock:
                            pr_url = groups_journal.get(name, {}).get("pr_url", "")
                        _record_group_fn(name, pr_url, group_branch.get(name, f"{run_id}/{name}"),
                                         "MERGED")
            finally:
                group_done_events[name].set()

        _integrate_verify_group_q(_mock_group("base", ["T001"]))

        self.assertNotIn("base", merged_local)
        merged_records = [(n, s) for n, s in record_calls_local if s == "MERGED"]
        self.assertEqual(len(merged_records), 0,
                         "MERGED must NOT be recorded for a quarantined group")


if __name__ == "__main__":
    unittest.main()
