#!/usr/bin/env python3
"""Systematic resume-state eval matrix for integrate.finish_real().

Motivation (brief 20260808-114127): two silent-dispatch bugs were found in one
session resuming full-real against real repos -- PR #235 (tail tasks never
dispatched on resume) and PR #238 (integrate_one's two MERGED early-return
paths left group_branch unpopulated, so finish_real() returned an empty
group_branch and full-real's sequential scheduler bailed at "nothing to
assemble" before ever reaching tail dispatch). Both bugs only manifest on
RESUME against specific journal/task-state combinations. Existing coverage
(test_dep_group_integrate.py) unit-tests integrate_one() in isolation, one
regression test per discovered bug; test_pipeline_e2e.py's E2ETailDispatchTest
covers the --pipeline scheduler's resume+tail-dispatch end to end. Neither
exercises finish_real() itself -- the seam between integrate_one() and
full-real -- across realistic combinations of task status, remote/journal
reconcile state, and multi-group interaction (a spec where some groups were
already fully integrated in a PRIOR session while others are fresh in THIS
one, which is exactly the "still-live gap" scenario PR #238 fixed).

Matrix dimensions:
  - task status entering finish_real: fresh ("done"), already-integrated
    ("completed" -- coordinator.ALREADY_INTEGRATED), failed
  - remote/journal reconcile state integrate_one discovers per group: no
    branch/no PR, remote branch only, OPEN PR, MERGED PR (gh pr view)
  - deliverable-subset emptiness: non-empty (normal), empty-because-completed
    (PR #238's first fix path), empty-because-failed (quarantine, not MERGED)
  - group topology: single independent group vs. base + dependent groups,
    including a MIXED run where one group is already-integrated and a
    sibling is fresh in the same finish_real() call

Invariant asserted throughout: group_branch is populated for every group that
reached a terminal integrated state (MERGED or OPEN), regardless of whether
that state was reached in THIS call or a prior one -- this is what full-real's
"nothing to assemble" guard (`if not group_branch: ...`) depends on to avoid
short-circuiting before tail dispatch. group_branch is empty ONLY when every
group in the run was quarantined (genuinely nothing integrated).

Run: python3 test_finish_real_resume_matrix.py
"""
from __future__ import annotations

import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import coordinator
from worktrail.orchestrator import integrate

Proc = namedtuple("Proc", "returncode stdout stderr")


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _task(tid: str, status: str = "done", deps=None, files=None) -> dict:
    return {
        "id": tid,
        "status": status,
        "kind": "impl",
        "deps": deps or [],
        "files": files or [f"src/{tid.lower()}.txt"],
    }


def _single_group_tasks(status: str = "done") -> list:
    """One independent task -> plan_groups() emits a single feature group
    (no fan-out root, so it never qualifies as 'base')."""
    return [_task("T001", status=status)]


def _multi_group_tasks(t1_status="done", t2_status="done", t3_status="done") -> list:
    """T1 (2 dependents) -> base; T2/T3 (each depends on T1) -> feature-1/2.
    Mirrors the topology test_pipeline.py/_init_repo use for the pipeline
    scheduler's own e2e suite."""
    return [
        _task("T001", status=t1_status),
        _task("T002", status=t2_status, deps=["T001"]),
        _task("T003", status=t3_status, deps=["T001"]),
    ]


class ScriptedGit:
    """Full-command-aware git/gh dispatcher for finish_real() integration tests.

    A prefix-only dispatcher (as used for single-group integrate_one() unit
    tests) can't express a resume matrix: finish_real() drives MULTIPLE groups
    through the same `gh pr view <branch>` / `git ls-remote <remote> <branch>`
    call shapes with DIFFERENT branch names, and each group needs an
    independently scripted reconcile state within the same finish_real() call
    (e.g. base already MERGED while a sibling feature group is fresh).

    pr_view / ls_remote: {branch_name: Proc}; a branch with no entry gets the
    "not found" default (pr_view -> exit 1; ls_remote -> exit 1, empty stdout).
    """

    def __init__(self, pr_view=None, ls_remote=None):
        self.pr_view = pr_view or {}
        self.ls_remote = ls_remote or {}
        self.pr_create_calls: list = []  # [(head_branch, base_branch)]
        self.calls: list = []

    def git(self, *args, **_kw):
        cmd = list(args[1:]) if args and isinstance(args[0], Path) else list(args)
        self.calls.append(("git", cmd))
        if cmd[:2] == ["rev-parse", "--verify"]:
            return Proc(0, "deadbeefcafe1234", "")
        if cmd[:1] == ["ls-remote"]:
            branch = cmd[-1] if cmd else ""
            return self.ls_remote.get(branch, Proc(1, "", ""))
        if cmd[:2] == ["remote", "get-url"]:
            return Proc(0, "https://github.com/o/r.git", "")
        # worktree add/prune/remove, merge, push, fetch: all succeed
        return Proc(0, "", "")

    def subprocess_run(self, cmd, **_kw):
        self.calls.append(("sub", list(cmd)))
        if cmd[:3] == ["gh", "pr", "view"]:
            branch = cmd[3] if len(cmd) > 3 else ""
            return self.pr_view.get(branch, Proc(1, "", "no pr"))
        if cmd[:3] == ["gh", "pr", "list"]:
            return Proc(0, "[]", "")
        if cmd[:3] == ["gh", "pr", "create"]:
            head = cmd[cmd.index("--head") + 1] if "--head" in cmd else "?"
            base_ = cmd[cmd.index("--base") + 1] if "--base" in cmd else "?"
            self.pr_create_calls.append((head, base_))
            return Proc(0, f"https://github.com/o/r/pull/{len(self.pr_create_calls)}\n", "")
        return Proc(0, "", "")


def _run_finish_real(tasks: list, scripted: ScriptedGit, run_id: str = "full-run"):
    """Drive the REAL finish_real() against a fully mocked git/gh layer."""
    with patch("worktrail.orchestrator.integrate._git", side_effect=scripted.git):
        with patch("worktrail.orchestrator.integrate.subprocess.run",
                    side_effect=scripted.subprocess_run):
            return integrate.finish_real(
                Path("/repo"), "spec-eval", tasks, "origin", run_id, "main",
                cleanup=False,
            )


# ---------------------------------------------------------------------------
# Single independent group: the four reconcile states x the two
# deliverable-emptiness paths
# ---------------------------------------------------------------------------

class SingleGroupReconcileMatrix(unittest.TestCase):
    """One group, four ways integrate_one can find it already reconciled on
    the remote, plus the two ways a group ends up with nothing left to ship."""

    def test_fresh_no_branch_no_pr_creates_pr(self):
        tasks = _single_group_tasks(status="done")
        scripted = ScriptedGit()
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(len(prs), 1, f"expected exactly 1 new PR; got {prs}")
        self.assertTrue(group_branch, "group_branch must be populated for a fresh integrate")
        self.assertEqual(len(scripted.pr_create_calls), 1)

    def test_remote_branch_exists_no_pr_reuses_branch_then_creates_pr(self):
        tasks = _single_group_tasks(status="done")
        name = coordinator.plan_groups(tasks)[0]["name"]
        gb = f"full-run/{name}"
        scripted = ScriptedGit(ls_remote={gb: Proc(0, gb, "")})
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(len(prs), 1)
        self.assertIn(name, group_branch)
        self.assertEqual(len(scripted.pr_create_calls), 1,
                          "a reused remote branch still needs a PR opened")
        # No worktree was built for a reused branch -- no merge/push calls.
        merge_calls = [c for k, c in scripted.calls if k == "git" and c[:1] == ["merge"]]
        self.assertEqual(merge_calls, [], "reused remote branch must skip the merge/build step")

    def test_open_pr_exists_is_reused_not_recreated(self):
        tasks = _single_group_tasks(status="done")
        name = coordinator.plan_groups(tasks)[0]["name"]
        gb = f"full-run/{name}"
        scripted = ScriptedGit(pr_view={
            gb: Proc(0, f'{{"state":"OPEN","url":"https://github.com/o/r/pull/42","headRefName":"{gb}"}}', "")
        })
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(len(prs), 1)
        self.assertEqual(prs[0][2], "https://github.com/o/r/pull/42")
        self.assertIn(name, group_branch)
        self.assertEqual(scripted.pr_create_calls, [], "an existing OPEN PR must not be recreated")

    def test_merged_pr_reconcile_populates_group_branch_opens_no_pr(self):
        """PR #238 regression, exercised through the REAL finish_real() rather
        than integrate_one() in isolation: a group whose PR already shows
        MERGED must still register in group_branch so finish_real()'s return
        value never reads as 'nothing to assemble'."""
        tasks = _single_group_tasks(status="done")
        name = coordinator.plan_groups(tasks)[0]["name"]
        gb = f"full-run/{name}"
        scripted = ScriptedGit(pr_view={
            gb: Proc(0, '{"state":"MERGED","url":"https://github.com/o/r/pull/7"}', "")
        })
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(prs, [], "an already-MERGED group opens no new PR")
        self.assertIn(name, group_branch,
                       "group_branch must register a MERGED-reconciled group so callers "
                       "don't conclude nothing was integrated")
        self.assertEqual(scripted.pr_create_calls, [])

    def test_all_tasks_already_integrated_populates_group_branch_opens_no_pr(self):
        """PR #238's OTHER fix path: every task status:'completed' (merged in a
        PRIOR session, no live PR to reconcile against) -> empty deliverable
        subset -> the 'all tasks already integrated' branch, not the gh-pr-view
        MERGED branch. Must ALSO register group_branch."""
        tasks = _single_group_tasks(status="completed")
        name = coordinator.plan_groups(tasks)[0]["name"]
        scripted = ScriptedGit()  # gh pr view -> not found; nothing to reconcile against
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(prs, [])
        self.assertIn(name, group_branch,
                       "group_branch must register an already-integrated group even with "
                       "no live PR to reconcile against")
        self.assertEqual(scripted.pr_create_calls, [])

    def test_all_tasks_failed_quarantines_group_branch_not_populated(self):
        """The negative case: deliverable is empty AND not because of prior
        integration -- this group legitimately has nothing to ship and must
        be quarantined, not silently treated as integrated."""
        tasks = _single_group_tasks(status="failed")
        scripted = ScriptedGit()
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(prs, [])
        self.assertEqual(group_branch, {},
                          "an all-failed group must NOT populate group_branch")
        self.assertEqual(len(quarantined), 1)


# ---------------------------------------------------------------------------
# Multi-group topology: base + two dependent feature groups, mixed resume
# states within a single finish_real() call
# ---------------------------------------------------------------------------

class MultiGroupResumeMatrix(unittest.TestCase):
    """base (T001) + feature-1 (T002) + feature-2 (T003), each independently
    in a different resume state -- the shape a real spec resumes in when part
    of it finished in a prior session and part is new this run."""

    def test_all_fresh_three_new_prs_features_target_base_branch(self):
        tasks = _multi_group_tasks()
        scripted = ScriptedGit()
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(len(prs), 3)
        self.assertEqual(set(group_branch), {"base", "feature-1", "feature-2"})
        feature_bases = {head: base_ for head, base_ in scripted.pr_create_calls
                          if head != "full-run/base"}
        self.assertTrue(
            all(b == "full-run/base" for b in feature_bases.values()),
            f"dependent groups must stack their PR on base's branch; got {feature_bases}",
        )

    def test_base_already_merged_dependents_stack_on_it_fresh(self):
        """base reached MERGED in a prior session (still has a live branch on
        remote); both feature groups are fresh this run and must stack their
        PR base on base's registered branch, not on 'main'."""
        tasks = _multi_group_tasks()
        scripted = ScriptedGit(pr_view={
            "full-run/base": Proc(0, '{"state":"MERGED","url":"https://github.com/o/r/pull/1"}', ""),
        })
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(len(prs), 2, "only the two feature groups open new PRs")
        self.assertEqual(set(group_branch), {"base", "feature-1", "feature-2"})
        self.assertTrue(
            all(b == "full-run/base" for _, b in scripted.pr_create_calls),
            f"feature PRs must target base's branch even though base opened no PR "
            f"this run; got {scripted.pr_create_calls}",
        )

    def test_mixed_base_and_one_feature_already_integrated_one_feature_fresh(self):
        """The exact 'still-live gap' shape from the brief: a spec resumed
        where base AND feature-1 were fully integrated in a PRIOR session
        (status:'completed', no deliverable, no live PR to reconcile against
        -- the harder of the two MERGED paths) while feature-2 is fresh in
        THIS run. Before PR #238 this returned group_branch missing base and
        feature-1, and if feature-2 had ALSO already been merged this would
        have produced group_branch == {} -- full-real's "nothing to assemble"
        guard firing even though the spec has three fully-integrated groups."""
        tasks = _multi_group_tasks(t1_status="completed", t2_status="completed", t3_status="done")
        scripted = ScriptedGit()
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(len(prs), 1, f"only feature-2 should open a new PR; got prs={prs}")
        self.assertEqual(
            set(group_branch), {"base", "feature-1", "feature-2"},
            "every already-integrated group must still register in group_branch "
            "alongside the one fresh group in the same finish_real() call",
        )
        self.assertEqual(scripted.pr_create_calls[0][1], "full-run/base",
                          "feature-2 must still stack on base's branch even though base "
                          "opened no PR this run")

    def test_all_three_already_integrated_group_branch_never_empty(self):
        """The scenario the brief's title names directly: EVERY group in the
        spec already merged in a prior session, nothing left to do this run.
        group_branch must be non-empty so full-real's sequential scheduler
        does not bail at 'nothing to assemble' before reaching tail dispatch."""
        tasks = _multi_group_tasks(t1_status="completed", t2_status="completed", t3_status="completed")
        scripted = ScriptedGit()
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertEqual(quarantined, {})
        self.assertEqual(prs, [], "nothing new to ship")
        self.assertEqual(
            set(group_branch), {"base", "feature-1", "feature-2"},
            "group_branch must be fully populated (never empty) when every group is "
            "already integrated, or full-real wrongly reports 'nothing to assemble'",
        )

    def test_one_feature_fails_others_integrate_group_branch_populated_for_survivors(self):
        """Partial-failure resume: feature-2's task failed (quarantined) while
        base and feature-1 succeed. group_branch must still be populated for
        the groups that DID integrate -- a partial failure must not be
        confused with 'nothing to assemble' either."""
        tasks = _multi_group_tasks(t3_status="failed")
        scripted = ScriptedGit()
        prs, group_branch, quarantined = _run_finish_real(tasks, scripted)

        self.assertIn("feature-2", quarantined)
        self.assertEqual(set(group_branch), {"base", "feature-1"},
                          "surviving groups must populate group_branch despite the "
                          "sibling failure")
        self.assertEqual(len(prs), 2)


if __name__ == "__main__":
    unittest.main()
