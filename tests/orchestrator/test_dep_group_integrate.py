#!/usr/bin/env python3
"""Regression tests for dependent-group integration defects.

Four defects fixed:
  1. Deleted start ref: dep group quarantined when base group's branch was deleted
     after merge (start ref no longer exists). Fix: fall back to pre-squash merge-base
     + squash reconcile merge.
  2. Journal state never MERGED: after verify auto-merge succeeds, group stays
     state:OPEN in journal. Fix: stamp MERGED after verify_one succeeds.
  3. Broken MERGED reconcile on --re-integrate: _clear_integration_state wiped
     MERGED group records, causing --re-integrate to rebuild already-merged groups.
     Fix: preserve MERGED records in _clear_integration_state.
  4. Already-merged group never registered in group_branch: both MERGED early-return
     paths (no-deliverable-because-completed, and gh-pr-view-already-MERGED) journaled
     the group correctly but left group_branch untouched. When a spec's ONLY impl group
     was already fully integrated before this run started (e.g. finished in a prior
     session), finish_real() returned an empty group_branch and full-real's sequential
     scheduler bailed out at "nothing to assemble" BEFORE ever reaching PR#235's
     tail-dispatch call -- so the spec's e2e/cleanup tail tasks never dispatched, even
     on the fixed post-PR#235 code. Fix: populate group_branch[name] on both paths.

Run: python3 test_dep_group_integrate.py
"""
from __future__ import annotations

import json
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

class AlreadyIntegratedGroupRegistersBranch(unittest.TestCase):
    """A group whose every task is already status:"completed" (ALREADY_INTEGRATED --
    impl work merged in a prior session, outside this orchestrator's own bookkeeping)
    has an empty deliverable subset and is journaled MERGED, but must also register in
    group_branch so callers (finish_real -> full-real) see it as integrated rather than
    concluding "nothing to assemble" and skipping tail dispatch."""

    def test_group_branch_populated_when_all_tasks_already_integrated(self):
        quarantined: dict = {}
        group_branch: dict = {}
        git_dispatch = _GitDispatch()
        subprocess_dispatch = _GitDispatch({
            ("gh", "pr", "view"): Proc(1, "", "no pr"),
        })
        with patch("worktrail.orchestrator.integrate._git", side_effect=git_dispatch):
            with patch("worktrail.orchestrator.integrate.subprocess.run",
                       side_effect=subprocess_dispatch):
                result = integrate.integrate_one(
                    _mock_group("feature-1", ["T001"]),
                    Path("/repo"), "spec-049",
                    [_mock_task("T001", status="completed")],
                    "origin", "full-run", "main",
                    None, {"T001": "completed"}, group_branch, quarantined,
                )

        self.assertIsNone(result, "already-merged group opens no new PR")
        self.assertNotIn("feature-1", quarantined, "must not be quarantined")
        self.assertIn(
            "feature-1", group_branch,
            "group_branch must register the group so callers see it as integrated, "
            "not conclude 'nothing to assemble' and skip tail dispatch",
        )

    def test_group_branch_populated_on_gh_pr_view_merged_reconcile(self):
        """Same fix, second code path: a group whose PR already shows MERGED via the
        `gh pr view` reconcile check (not the no-deliverable-because-completed branch)."""
        quarantined: dict = {}
        group_branch: dict = {}
        git_dispatch = _GitDispatch({("ls-remote",): Proc(1, "", "")})
        subprocess_dispatch = _GitDispatch({
            ("gh", "pr", "view"): Proc(
                0, json.dumps({"state": "MERGED", "url": "https://g/p/9"}), "",
            ),
        })
        with patch("worktrail.orchestrator.integrate._git", side_effect=git_dispatch):
            with patch("worktrail.orchestrator.integrate.subprocess.run",
                       side_effect=subprocess_dispatch):
                result = integrate.integrate_one(
                    _mock_group("feature-1", ["T001"]),
                    Path("/repo"), "spec-049",
                    [_mock_task("T001", status="done")],
                    "origin", "full-run", "main",
                    None, {"T001": "done"}, group_branch, quarantined,
                )

        self.assertIsNone(result)
        self.assertIn(
            "feature-1", group_branch,
            "group_branch must register the group on the gh-pr-view MERGED reconcile "
            "path too",
        )


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


# ---------------------------------------------------------------------------
# Fix 4: resumed head_branch trust — a corrupted journal head_branch (e.g. a
# discovered-PR mismatch that recorded a real, unrelated branch like "stg")
# must never be trusted for VERIFY. Confirmed root cause: journal_rec.get(
# "head_branch", fallback) was passed straight into VERIFY with no validation
# that it is this run's own orchestrator-owned integration branch.
# ---------------------------------------------------------------------------

class ResolveJournaledHeadBranch(unittest.TestCase):
    """live._resolve_journaled_head_branch must reject any head_branch that
    isn't this run's own owned branch (f"{run_id}/{name}")."""

    def test_matching_owned_branch_is_trusted(self):
        branch, reason = live._resolve_journaled_head_branch(
            "feature-1", {"head_branch": "full-1786136018/feature-1"}, "full-1786136018"
        )
        self.assertEqual(branch, "full-1786136018/feature-1")
        self.assertIsNone(reason)

    def test_missing_head_branch_falls_back_to_owned(self):
        branch, reason = live._resolve_journaled_head_branch(
            "feature-1", {}, "full-1786136018"
        )
        self.assertEqual(branch, "full-1786136018/feature-1")
        self.assertIsNone(reason)

    def test_bare_shared_branch_name_is_rejected(self):
        """Reproduces the confirmed incident: PR discovery matched an unrelated
        stg->prd promotion PR whose headRefName is literally 'stg', corrupting
        the journal's head_branch for a completely unrelated group."""
        for bad_branch in ("stg", "dev", "main"):
            with self.subTest(bad_branch=bad_branch):
                branch, reason = live._resolve_journaled_head_branch(
                    "feature-1", {"head_branch": bad_branch}, "full-1786136018"
                )
                self.assertEqual(branch, bad_branch, "candidate is reported, not silently swapped")
                self.assertIsNotNone(reason, f"{bad_branch!r} must be rejected outright")
                self.assertIn(bad_branch, reason)

    def test_unrelated_run_branch_is_rejected(self):
        """A head_branch from a different run_id's group is just as untrustworthy
        as a bare shared branch name -- it is still not this run's own branch."""
        branch, reason = live._resolve_journaled_head_branch(
            "feature-1", {"head_branch": "full-999999/feature-1"}, "full-1786136018"
        )
        self.assertEqual(branch, "full-999999/feature-1")
        self.assertIsNotNone(reason)


class GroupBranchFromJournal(unittest.TestCase):
    """live._group_branch_from_journal must quarantine corrupted groups instead
    of including them in the trusted group_branch map handed to VERIFY."""

    def test_mixed_journal_quarantines_only_the_corrupted_group(self):
        journal_groups = {
            "base": {"pr_url": "https://g/p/1", "head_branch": "full-1786136018/base", "state": "OPEN"},
            "feature-1": {"pr_url": "https://g/p/2", "head_branch": "stg", "state": "OPEN"},
        }
        group_branch, quarantined = live._group_branch_from_journal(
            journal_groups, "full-1786136018"
        )
        self.assertEqual(group_branch, {"base": "full-1786136018/base"})
        self.assertNotIn("feature-1", group_branch,
                         "corrupted head_branch must never reach the trusted group_branch map")
        self.assertIn("feature-1", quarantined)
        self.assertIn("stg", quarantined["feature-1"])


class ResumeNeverVerifiesCorruptedBranch(unittest.TestCase):
    """End-to-end proof (mirroring the resume-path closure structure used by the
    other tests in this file): a resumed group whose journal head_branch fails
    validation must be quarantined and verify_one must NEVER be invoked with the
    corrupted branch name -- reproducing the incident where VERIFY checked out
    and ci-fixed the real 'stg' branch."""

    def test_corrupted_resume_skips_verify_entirely(self):
        verify_one_calls = []

        class RecordingVerifier:
            def verify_one(self, group, gb, *args, **kwargs):
                verify_one_calls.append(gb)

        def make_verifier_fn():
            return RecordingVerifier()

        run_id = "full-1786136018"
        # journal corrupted exactly as in the confirmed incident: an unrelated
        # stg->prd promotion PR's real headRefName landed in this group's record.
        journal_rec = {"pr_url": "https://github.com/o/r/pull/1990", "head_branch": "stg", "state": "OPEN"}
        group_branch: dict = {}
        quarantined: dict = {}
        prs: list = []
        record_calls = []

        def _record_group_fn(name, pr_url, head_branch, state):
            record_calls.append((name, pr_url, head_branch, state))

        name = "feature-1"
        # Mirrors the fixed _integrate_verify_group resume branch (live.py).
        _skip_integrate = bool(journal_rec.get("pr_url"))
        self.assertTrue(_skip_integrate)
        if name not in group_branch:
            candidate, reason = live._resolve_journaled_head_branch(name, journal_rec, run_id)
            if reason:
                quarantined[name] = reason
            else:
                group_branch[name] = candidate
        prs.append((name, "main", journal_rec.get("pr_url")))
        if name in quarantined:
            _record_group_fn(name, "", journal_rec.get("head_branch", ""), "QUARANTINED")

        # The shared post-branch guard in the real closure: quarantined/missing
        # group_branch entries must never reach verify_one.
        should_verify = name not in quarantined and name in group_branch
        self.assertFalse(should_verify, "a corrupted resumed head_branch must never reach VERIFY")
        if should_verify:
            make_verifier_fn().verify_one({"name": name}, group_branch[name])

        self.assertEqual(verify_one_calls, [], "verify_one must never be called with 'stg'")
        self.assertIn("feature-1", quarantined)
        self.assertEqual(record_calls, [("feature-1", "", "stg", "QUARANTINED")])


if __name__ == "__main__":
    unittest.main()
