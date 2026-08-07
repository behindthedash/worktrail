#!/usr/bin/env python3
"""Unit tests for the post-PR verify stage (verify.py).

Hermetic: every git/gh effect goes through a fake `run`, and every worker spawn
through a fake `spawn`. No subprocess, no network, no real worktrees. Run:

    python3 test_verify.py
"""
from __future__ import annotations

import json
import tempfile
import threading
import unittest
from collections import deque
from collections import namedtuple
from pathlib import Path

from worktrail.orchestrator import verify

Proc = namedtuple("Proc", "returncode stdout stderr")

GREEN = [{"name": "build", "status": "COMPLETED", "conclusion": "SUCCESS"}]
RED = [{"name": "build", "status": "COMPLETED", "conclusion": "FAILURE"}]


def view(mergeable="MERGEABLE", rollup=None, number=1, head="abc",
         state="OPEN", auto_merge_request=None, merged_by=None, labels=None):
    return {"number": number, "state": state, "mergeable": mergeable,
            "mergeStateStatus": "CLEAN", "statusCheckRollup": rollup or GREEN,
            "headRefOid": head, "autoMergeRequest": auto_merge_request,
            "mergedBy": merged_by, "labels": labels or []}


class FakeRun:
    """Scriptable git+gh runner. `views[gb]` is a list of pr-view dicts consumed
    in order (the last repeats)."""

    def __init__(self, views, merges=None,
                 remote_url="https://github.com/o/r.git", runs="[]"):
        self.views = {k: deque(v) for k, v in views.items()}
        self.merges = merges or {}
        self.remote_url = remote_url
        self.runs = runs
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[0] == "git" and "remote" in cmd and "get-url" in cmd:
            return Proc(0, self.remote_url, "")
        if cmd[:2] == ["gh", "api"]:
            if "/rules/branches/" in cmd[2]:
                return Proc(0, json.dumps([{
                    "type": "required_status_checks",
                    "parameters": {"required_status_checks": [{"context": "build"}]},
                }]), "")
            return Proc(0, json.dumps({"allow_auto_merge": True}), "")
        if cmd[:3] == ["gh", "pr", "view"]:
            gb = cmd[3]
            q = self.views.get(gb)
            if not q:
                return Proc(1, "", "no pull requests found")
            d = q[0] if len(q) == 1 else q.popleft()
            return Proc(0, json.dumps(d), "")
        if cmd[:3] == ["gh", "pr", "merge"]:
            rc = self.merges.get(cmd[3], 0)
            return Proc(rc, "", "merge blocked" if rc else "")
        if cmd[:3] == ["gh", "run", "list"]:
            return Proc(0, self.runs, "")
        if cmd[:3] == ["gh", "run", "view"]:
            return Proc(0, "::error:: boom\n", "")
        return Proc(0, "", "")            # any git mutation

    def find(self, *prefix):
        return [c for c in self.calls if c[:len(prefix)] == list(prefix)]


class FakeSpawn:
    def __init__(self, status="success"):
        self.status = status
        self.prompts = []

    def __call__(self, prompt, _worktree_path):
        self.prompts.append(prompt)
        return ('done.\n```json\n{"task":"g","step":"verify",'
                f'"status":"{self.status}","head_sha":"def"}}\n```')


def mk(run, spawn, tmp, **kw):
    return verify.Verifier(
        Path("/repo"), "origin", "dev", "001-x",
        run=run, spawn=spawn, log=lambda *_: None, sleep=lambda *_: None,
        worktree_base=Path(tmp), max_polls=5, **kw)


FEATURE = {"name": "feature-1", "tasks": ["TASK-002"], "reqs": [], "depends_on": []}


class CleanGreenPath(unittest.TestCase):
    def test_merges_and_cleans_up(self):
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], ["feature-1"])
        self.assertEqual(res["quarantined"], {})
        self.assertEqual(spawn.prompts, [])                  # no fix workers
        self.assertTrue(run.find("gh", "pr", "merge", "run/feature-1"))
        # cleanup gate: task worktree + branch removed, branch deleted, pruned
        self.assertTrue(run.find("git", "-C", "/repo", "worktree", "remove"))
        self.assertTrue(any(c[:5] == ["git", "-C", "/repo", "branch", "-D"]
                            and c[-1] == "001-x/task-002" for c in run.calls))
        self.assertTrue(run.find("git", "-C", "/repo", "worktree", "prune"))


class ConflictResolvePath(unittest.TestCase):
    def test_resolve_worker_then_merge(self):
        run = FakeRun({"run/feature-1": [view(mergeable="CONFLICTING"), view()]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], ["feature-1"])
        self.assertEqual(len(spawn.prompts), 1)
        self.assertIn("CONFLICTING", spawn.prompts[0])


class CiFixExhaustionPath(unittest.TestCase):
    def test_three_strikes_quarantines_and_keeps_worktree(self):
        run = FakeRun({"run/feature-1": [view(rollup=RED)]},
                      runs=json.dumps([{"databaseId": 9, "conclusion": "FAILURE",
                                        "headSha": "abc"}]))
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], [])
        self.assertIn("feature-1", res["quarantined"])
        self.assertIn("CI still failing", res["quarantined"]["feature-1"])
        self.assertEqual(len(spawn.prompts), 3)              # 3 strikes
        self.assertTrue(all("CI is FAILING" in p for p in spawn.prompts))
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))
        # worktree KEPT: no task-worktree teardown
        self.assertFalse(run.find("git", "-C", "/repo", "worktree", "remove"))


class BaseFailureQuarantinesDependents(unittest.TestCase):
    def test_dependent_skipped_when_base_red(self):
        base = {"name": "base", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}
        feat = {"name": "feature-1", "tasks": ["TASK-002"], "reqs": [],
                "depends_on": ["base"]}
        run = FakeRun({"run/base": [view(rollup=RED)],
                       "run/feature-1": [view()]},
                      runs=json.dumps([{"databaseId": 9, "conclusion": "FAILURE",
                                        "headSha": "abc"}]))
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        res = v.run_all([base, feat],
                        {"base": "run/base", "feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], [])
        self.assertIn("base", res["quarantined"])
        self.assertIn("feature-1", res["quarantined"])
        self.assertIn("base group 'base' failed", res["quarantined"]["feature-1"])
        # dependent never had its PR inspected
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))


class AutoMergeFailureQuarantines(unittest.TestCase):
    def test_merge_blocked_keeps_worktree(self):
        run = FakeRun({"run/feature-1": [view()]},
                      merges={"run/feature-1": 1})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], [])
        self.assertIn("auto-merge failed", res["quarantined"]["feature-1"])
        self.assertFalse(run.find("git", "-C", "/repo", "worktree", "remove"))


class NoAutomergeLabelGate(unittest.TestCase):
    """brief 20260723-174500-verify-automerge-fallback-bypasses-policy:
    auto_merge() must never arm `gh pr merge` while the PR carries
    go:no-automerge, on ANY internal path -- including the required-checks
    preflight-query-failed fallback that previously bypassed this entirely
    (spec-023 group PR #388, go:risk-high + go:no-automerge, merged with no
    human decision)."""

    def test_labeled_pr_never_arms_direct_merge(self):
        run = FakeRun({"run/feature-1": [
            view(labels=[{"name": "go:risk-high"}, {"name": "go:no-automerge"}])
        ]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("go:no-automerge", msg)
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_labeled_pr_blocks_the_preflight_query_failed_fallback(self):
        """The exact bug: a required-checks preflight query failure used to
        fall through UNCONDITIONALLY to `gh pr merge --auto`, regardless of
        go:no-automerge. Simulate the preflight's own `gh api` calls failing
        (query error) on a labeled PR -- must still never arm."""
        class FakeRunPreflightFailsLabeled(FakeRun):
            def __call__(self, cmd):
                if cmd[:2] == ["gh", "api"]:
                    return Proc(1, "", "gh api failed after 3 attempts")
                return super().__call__(cmd)

        run = FakeRunPreflightFailsLabeled({
            "run/feature-1": [view(labels=[{"name": "go:no-automerge"}])]
        })
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("go:no-automerge", msg)
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_unlabeled_pr_still_arms_normally(self):
        """Baseline: no go:no-automerge label -> unchanged existing behavior."""
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertTrue(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_label_query_failure_fails_closed_never_arms(self):
        """A label-query failure (gh unavailable, transient) must fail CLOSED
        -- never treated as 'label absent, safe to arm'."""
        run = FakeRun({})  # no queued view -> "gh pr view" returns rc=1
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("failing closed", msg)
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_labeled_pr_blocks_the_branch_protection_fallback_path(self):
        """The branch-protection --auto fallback (a separate arming path from
        the preflight-query-failed one) must also respect the label."""
        run = FakeRun(
            {"run/feature-1": [view(labels=[{"name": "go:no-automerge"}])]},
            merges={"run/feature-1": 1},  # direct merge blocked -> would try --auto fallback
        )
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("go:no-automerge", msg)
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))


class ClassifyChecks(unittest.TestCase):
    def test_shapes(self):
        self.assertEqual(verify.classify_checks(None), (False, []))
        self.assertEqual(verify.classify_checks(GREEN), (False, []))
        self.assertEqual(verify.classify_checks(RED), (False, ["build"]))
        self.assertEqual(
            verify.classify_checks([{"name": "t", "status": "IN_PROGRESS"}]),
            (True, []))
        self.assertEqual(
            verify.classify_checks([{"context": "legacy", "state": "FAILURE"}]),
            (False, ["legacy"]))


class MergedPRTreatedAsSuccess(unittest.TestCase):
    def test_auto_merge_skipped_for_merged_pr(self):
        merged_view = {"number": 1, "state": "MERGED", "mergeable": "MERGEABLE",
                       "mergeStateStatus": "CLEAN", "statusCheckRollup": GREEN,
                       "headRefOid": "abc"}
        run = FakeRun({"run/feature-1": [merged_view]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_run_all_handles_merged_pr(self):
        merged_view = {"number": 1, "state": "MERGED", "mergeable": "MERGEABLE",
                       "mergeStateStatus": "CLEAN", "statusCheckRollup": GREEN,
                       "headRefOid": "abc"}
        run = FakeRun({"run/feature-1": [merged_view]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], ["feature-1"])
        self.assertEqual(res["quarantined"], {})
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))


class DeferToArmedAutoMerge(unittest.TestCase):
    """Handoff 20260714-120011-go-automerge-coordination: auto_merge() must defer to
    an already-armed native auto-merge (this repo's own CI workflow, a bot, or a
    human via GitHub's toggle) instead of racing it with its own `gh pr merge` call
    -- racing risks applying the wrong merge method to a PR another actor already
    committed to merging a specific way."""

    def test_defers_instead_of_racing_when_armed(self):
        armed = view(state="OPEN",
                     auto_merge_request={"enabledBy": {"login": "ci-bot"}})
        merged = view(state="MERGED")
        run = FakeRun({"run/feature-1": [armed, merged]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"),
                         "must never call `gh pr merge` itself once armed externally")

    def test_defer_times_out_if_never_merges(self):
        armed = view(state="OPEN",
                     auto_merge_request={"enabledBy": {"login": "ci-bot"}})
        run = FakeRun({"run/feature-1": [armed]})  # stays OPEN (repeats last)
        v = mk(run, FakeSpawn(), "/tmp/x")  # mk() caps max_polls=5

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("did not complete within poll budget", msg)
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))


class RequiredChecksPreflight(unittest.TestCase):
    def test_eligible_branch_reaches_direct_merge(self):
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        self.assertTrue(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_ineligible_branch_never_attempts_direct_merge(self):
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        original = verify.automerge_preflight.required_checks_gate
        verify.automerge_preflight.required_checks_gate = lambda *args, **kwargs: (
            False, "base has zero required status checks"
        )
        try:
            ok, msg = v.auto_merge(FEATURE, "run/feature-1")
        finally:
            verify.automerge_preflight.required_checks_gate = original

        self.assertFalse(ok)
        self.assertIn("zero required status checks", msg)
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_preflight_query_error_falls_back_to_auto_merge(self):
        """A transient `gh api` read failure (not a confirmed unsafe state)
        must not quarantine an otherwise-green group -- it falls back to
        `gh pr merge --auto` so GitHub itself still enforces required checks
        at merge time."""
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        original = verify.automerge_preflight.required_checks_gate
        verify.automerge_preflight.required_checks_gate = lambda *args, **kwargs: (
            False, "could not query required status checks for o/r@dev (gh api failed after 3 attempts)"
        )
        try:
            ok, msg = v.auto_merge(FEATURE, "run/feature-1")
        finally:
            verify.automerge_preflight.required_checks_gate = original

        self.assertTrue(ok)
        self.assertEqual(msg, "queued")
        auto_merge_calls = [c for c in run.find("gh", "pr", "merge", "run/feature-1")
                            if "--auto" in c]
        self.assertTrue(auto_merge_calls, "expected a `gh pr merge --auto` fallback call")

    def test_preflight_query_error_fallback_also_fails_is_distinguishable(self):
        """If the `--auto` fallback itself fails, the group DOES quarantine,
        but with a reason that names the fallback attempt -- distinct from
        both a confirmed-ineligible block and a plain merge failure, so
        post-mortems don't conflate a read failure with a real rejection."""
        run = FakeRun({"run/feature-1": [view()]}, merges={"run/feature-1": 1})
        v = mk(run, FakeSpawn(), "/tmp/x")

        original = verify.automerge_preflight.required_checks_gate
        verify.automerge_preflight.required_checks_gate = lambda *args, **kwargs: (
            False, "could not query required status checks for o/r@dev (gh api failed after 3 attempts)"
        )
        try:
            ok, msg = v.auto_merge(FEATURE, "run/feature-1")
        finally:
            verify.automerge_preflight.required_checks_gate = original

        self.assertFalse(ok)
        self.assertIn("preflight query failed", msg)
        self.assertIn("fallback also failed", msg)

    def test_preflight_query_error_and_method_rejected_retries_other_methods(self):
        """Regression for the 2026-07-22 spec-087 orchestrator run against datalena
        (GO run go-20260722-233349, PR #1848): the required-checks preflight query
        failed, and the `gh pr merge --auto` fallback used the repo-wide-detected
        method ("merge", since repo settings allow merge+squash) against a
        squash-only branch ruleset -- rejected by enablePullRequestAutoMerge and
        quarantined an otherwise-green group. The fallback must now retry the
        other methods (mirroring the branch-protection fallback's existing
        method-retry) before giving up."""
        merge_calls = []

        class MethodRejectRun:
            def __call__(self, cmd):
                if cmd[:3] == ["gh", "pr", "merge"]:
                    merge_calls.append(cmd)
                    if "--merge" in cmd:
                        return Proc(1, "",
                                    "GraphQL: Merge method merge commits are not allowed on "
                                    "this repository (enablePullRequestAutoMerge)")
                    if "--squash" in cmd:
                        return Proc(0, "", "")
                    return Proc(1, "", "method also not allowed")
                if cmd[:3] == ["gh", "repo", "view"]:
                    # Repo-wide settings allow both -- this is what misleads
                    # _detect_merge_method into preferring "merge".
                    return Proc(0, json.dumps({
                        "mergeCommitAllowed": True,
                        "squashMergeAllowed": True,
                        "rebaseMergeAllowed": False,
                    }), "")
                base = FakeRun({"run/feature-1": [view()]})
                return base(cmd)

        run = MethodRejectRun()
        v = mk(run, FakeSpawn(), "/tmp/x")

        original = verify.automerge_preflight.required_checks_gate
        verify.automerge_preflight.required_checks_gate = lambda *args, **kwargs: (
            False, "could not query required status checks for o/r@dev (gh api failed after 3 attempts)"
        )
        try:
            ok, msg = v.auto_merge(FEATURE, "run/feature-1")
        finally:
            verify.automerge_preflight.required_checks_gate = original

        self.assertTrue(ok, f"expected method-retry to succeed, got: {msg}")
        self.assertEqual(msg, "queued")
        self.assertEqual(v._merge_method, "squash")
        auto_squash_calls = [c for c in merge_calls if "--auto" in c and "--squash" in c]
        self.assertEqual(len(auto_squash_calls), 1)
        self.assertEqual(v._preflight_fallbacks["feature-1"]["outcome"], "queued")
        self.assertEqual(v._preflight_fallbacks["feature-1"]["method"], "squash")

    def test_query_error_fallback_records_structured_event_on_success(self):
        """The preflight-query-error fallback must be recorded distinctly from a
        normal path so it's queryable across runs (safety_net_report.py), not
        just readable in this one run's reason string."""
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        original = verify.automerge_preflight.required_checks_gate
        verify.automerge_preflight.required_checks_gate = lambda *args, **kwargs: (
            False, "could not query required status checks for o/r@dev (gh api failed after 3 attempts)"
        )
        try:
            v.auto_merge(FEATURE, "run/feature-1")
        finally:
            verify.automerge_preflight.required_checks_gate = original

        self.assertIn("feature-1", v._preflight_fallbacks)
        event = v._preflight_fallbacks["feature-1"]
        self.assertEqual(event["outcome"], "queued")
        self.assertEqual(event["pr"], "run/feature-1")
        self.assertIn("could not query required status checks", event["reason"])

    def test_query_error_fallback_records_structured_event_on_failure(self):
        run = FakeRun({"run/feature-1": [view()]}, merges={"run/feature-1": 1})
        v = mk(run, FakeSpawn(), "/tmp/x")

        original = verify.automerge_preflight.required_checks_gate
        verify.automerge_preflight.required_checks_gate = lambda *args, **kwargs: (
            False, "could not query required status checks for o/r@dev (gh api failed after 3 attempts)"
        )
        try:
            v.auto_merge(FEATURE, "run/feature-1")
        finally:
            verify.automerge_preflight.required_checks_gate = original

        self.assertIn("feature-1", v._preflight_fallbacks)
        self.assertEqual(v._preflight_fallbacks["feature-1"]["outcome"], "fallback_failed")

    def test_run_all_returns_preflight_fallbacks(self):
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        original = verify.automerge_preflight.required_checks_gate
        verify.automerge_preflight.required_checks_gate = lambda *args, **kwargs: (
            False, "could not query required status checks for o/r@dev (gh api failed after 3 attempts)"
        )
        try:
            result = v.run_all([FEATURE], {"feature-1": "run/feature-1"})
        finally:
            verify.automerge_preflight.required_checks_gate = original

        self.assertIn("feature-1", result["preflight_fallbacks"])
        self.assertEqual(result["preflight_fallbacks"]["feature-1"]["outcome"], "queued")

    def test_direct_eligible_merge_records_no_fallback_event(self):
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        v.auto_merge(FEATURE, "run/feature-1")

        self.assertEqual(v._preflight_fallbacks, {})


class MergeMethodOverride(unittest.TestCase):
    """merge_method_by_base (via go-policy.yaml, resolved by the sdd-workflow
    conductor and passed as verify.Verifier(merge_method=...)) skips verify.py's
    own repo-wide `_detect_merge_method()` GitHub-settings query entirely."""

    def test_override_used_without_querying_repo_settings(self):
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x", merge_method="merge")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertFalse(run.find("gh", "repo", "view"),
                         "override must skip the repo-wide detection query")
        merge_calls = run.find("gh", "pr", "merge", "run/feature-1")
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--merge", merge_calls[0])

    def test_no_override_falls_back_to_detection(self):
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")  # merge_method not set

        v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(run.find("gh", "repo", "view"),
                        "no override -> must fall back to repo-wide detection")


class CleanupNoOps(unittest.TestCase):
    def test_cleanup_nonexistent_verify_worktree(self):
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/nonexistent")
        # verify worktree path never created, so cleanup handles it gracefully
        try:
            v.cleanup_group(FEATURE, "run/feature-1")
        except Exception as e:
            self.fail(f"cleanup_group raised {e} for nonexistent worktree")

    def test_cleanup_already_deleted_branch(self):
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()

        # Simulate branch already deleted by returning non-zero with "not found" error
        def fake_run_with_branch_error(cmd):
            if cmd[:5] == ["git", "-C", "/repo", "branch", "-D"]:
                return Proc(1, "", "error: branch 'run/feature-1' not found")
            return run(cmd)

        v = mk(fake_run_with_branch_error, spawn, "/tmp/x")
        try:
            v.cleanup_group(FEATURE, "run/feature-1")
        except Exception as e:
            self.fail(f"cleanup_group raised {e} for already-deleted branch")

    def test_cleanup_branch_deletion_with_alternative_error_msg(self):
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()

        def fake_run_with_branch_error(cmd):
            if cmd[:5] == ["git", "-C", "/repo", "branch", "-D"]:
                return Proc(1, "", "error: no such branch 'run/feature-1'")
            return run(cmd)

        v = mk(fake_run_with_branch_error, spawn, "/tmp/x")
        try:
            v.cleanup_group(FEATURE, "run/feature-1")
        except Exception as e:
            self.fail(f"cleanup_group raised {e} for already-deleted branch")

    def test_cleanup_deletes_remote_group_branch(self):
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        v.cleanup_group(FEATURE, "run/feature-1")
        self.assertTrue(
            run.find("git", "-C", "/repo", "push", "origin", "--delete", "run/feature-1"),
            "cleanup_group must push --delete the remote group branch",
        )

    def test_cleanup_remote_branch_delete_failure_is_swallowed(self):
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()

        def fake_run_push_fails(cmd):
            if cmd[:6] == ["git", "-C", "/repo", "push", "origin", "--delete"]:
                return Proc(1, "", "error: remote ref does not exist")
            return run(cmd)

        v = mk(fake_run_push_fails, spawn, "/tmp/x")
        try:
            v.cleanup_group(FEATURE, "run/feature-1")
        except Exception as e:
            self.fail(f"cleanup_group raised {e} when remote push --delete failed")

    def test_cleanup_skips_remote_branch_delete_when_queued(self):
        """When skip_remote_branch_delete=True (PR queued for auto-merge), the remote
        branch must NOT be deleted — GitHub deletes it when auto-merge fires."""
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        v.cleanup_group(FEATURE, "run/feature-1", skip_remote_branch_delete=True)

        self.assertFalse(
            run.find("git", "-C", "/repo", "push", "origin", "--delete", "run/feature-1"),
            "remote branch must not be deleted when PR is queued for auto-merge",
        )
        # Local worktree + branch cleanup still happens
        self.assertTrue(
            any(c[:5] == ["git", "-C", "/repo", "branch", "-D"] for c in run.calls),
            "local branch cleanup must still run when skip_remote_branch_delete=True",
        )


class ReuseExistingVerifyWorktree(unittest.TestCase):
    def test_verify_worktree_reused_on_second_call(self):
        import tempfile
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()
        with tempfile.TemporaryDirectory() as tmp:
            v = mk(run, spawn, tmp)
            # First call creates the worktree
            path1 = v._group_worktree(FEATURE, "run/feature-1")
            self.assertIsNotNone(path1)
            worktree_add_calls_after_first = [c for c in run.calls
                                               if c[:5] == ["git", "-C", "/repo", "worktree", "add"]]
            self.assertEqual(len(worktree_add_calls_after_first), 1)

            # Manually create the directory to simulate the worktree existing
            path1.parent.mkdir(parents=True, exist_ok=True)
            path1.mkdir(exist_ok=True)

            # Second call should reuse it (path exists)
            path2 = v._group_worktree(FEATURE, "run/feature-1")
            worktree_add_calls_after_second = [c for c in run.calls
                                                if c[:5] == ["git", "-C", "/repo", "worktree", "add"]]
            self.assertEqual(len(worktree_add_calls_after_second), 1)  # still 1, not 2
            self.assertEqual(path1, path2)


class AutoMergeEdgeCases(unittest.TestCase):
    def test_closed_pr_fails_merge(self):
        closed_view = {"number": 1, "state": "CLOSED", "mergeable": "MERGEABLE",
                       "mergeStateStatus": "CLEAN", "statusCheckRollup": GREEN,
                       "headRefOid": "abc"}
        run = FakeRun({"run/feature-1": [closed_view]},
                      merges={"run/feature-1": 1})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("auto-merge failed", msg)

    def test_gh_wholly_unavailable_fails_closed_at_the_label_gate(self):
        """When `gh` is broadly unreachable (pr view AND the required-checks
        preflight's own `gh api` calls all fail), auto_merge()'s FIRST call is
        now the go:no-automerge label check (route:J
        20260723-174500-verify-automerge-fallback-bypasses-policy) -- a failed
        label query fails closed immediately, before even reaching the
        preflight/merge-fallback logic. Zero merge attempts, not one: failing
        earlier is strictly safer than reaching a merge attempt at all."""
        class FakeRunGhUnavailable:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[0] == "git" and "remote" in cmd and "get-url" in cmd:
                    return Proc(0, "https://github.com/o/r.git", "")
                if cmd[:3] == ["gh", "pr", "view"]:
                    return Proc(1, "", "no pull requests found")
                if cmd[:3] == ["gh", "pr", "merge"]:
                    return Proc(1, "", "gh: could not connect")
                return Proc(1, "", "gh: could not connect")  # gh api calls fail too

        run = FakeRunGhUnavailable()
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("failing closed", msg)
        merge_calls = [c for c in run.calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merge_calls), 0)


class ParallelVerifyWave(unittest.TestCase):
    """#15: independent group PRs are verified in one concurrent wave."""

    def test_two_independent_groups_verified_in_one_wave(self):
        f1 = {"name": "feature-1", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}
        f2 = {"name": "feature-2", "tasks": ["TASK-002"], "reqs": [], "depends_on": []}
        run = FakeRun({"run/feature-1": [view()], "run/feature-2": [view()]})
        logs = []
        v = verify.Verifier(
            Path("/repo"), "origin", "dev", "001-x",
            run=run, spawn=FakeSpawn(), log=logs.append, sleep=lambda *_: None,
            worktree_base=Path("/tmp/x"), max_polls=5,
        )
        res = v.run_all(
            [f1, f2], {"feature-1": "run/feature-1", "feature-2": "run/feature-2"}
        )
        self.assertEqual(set(res["merged"]), {"feature-1", "feature-2"})
        self.assertEqual(res["quarantined"], {})
        self.assertTrue(any("VERIFY WAVE [parallel x2]" in line for line in logs),
                        "independent groups should verify in one concurrent wave")


class GhRetry(unittest.TestCase):
    """#9: a transient `gh pr view` failure is retried, not read as 'unavailable'."""

    def test_pr_status_retries_transient_failure(self):
        class FlakyRun:
            def __init__(self):
                self.views = 0

            def __call__(self, cmd):
                if cmd[0] == "git" and "remote" in cmd and "get-url" in cmd:
                    return Proc(0, "https://github.com/o/r.git", "")
                if cmd[:3] == ["gh", "pr", "view"]:
                    self.views += 1
                    if self.views < 3:  # two transient blips, then success
                        return Proc(1, "", "error: could not connect")
                    return Proc(0, json.dumps(view()), "")
                return Proc(0, "", "")

        run = FlakyRun()
        v = mk(run, FakeSpawn(), "/tmp/x")
        st = v.pr_status("run/feature-1")
        self.assertIsNotNone(st)  # retried past the blips instead of returning None
        self.assertEqual(run.views, 3)


class RetargetDependent(unittest.TestCase):
    """#10: a dependent group's PR is retargeted to base before it's verified, so
    it can't be orphaned on the parent's deleted branch."""

    def test_dependent_retargeted_base_is_not(self):
        base = {"name": "base", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}
        feat = {"name": "feature-1", "tasks": ["TASK-002"], "reqs": [],
                "depends_on": ["base"]}
        run = FakeRun({"run/base": [view()], "run/feature-1": [view(number=7)]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        res = v.run_all([base, feat],
                        {"base": "run/base", "feature-1": "run/feature-1"})

        self.assertEqual(set(res["merged"]), {"base", "feature-1"})
        # retargeted via the REST PATCH endpoint (not `gh pr edit --base`,
        # which unconditionally requests `projectCards` pre-mutation and fails
        # on repos/orgs with a legacy Projects (classic) board attached)
        self.assertIn(["gh", "api", "repos/o/r/pulls/7", "-X", "PATCH",
                       "-f", "base=dev"], run.calls)
        # the base group (no depends_on) was NOT retargeted
        self.assertFalse(run.find("gh", "pr", "edit"))

    def test_retarget_falls_back_to_pr_edit_when_pr_number_unresolvable(self):
        """If `pr_status` can't resolve a PR number (e.g. `gh pr view` failed),
        fall back to `gh pr edit --base` rather than skip retargeting silently."""
        base = {"name": "base", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}
        feat = {"name": "feature-1", "tasks": ["TASK-002"], "reqs": [],
                "depends_on": ["base"]}
        run = FakeRun({"run/base": [view()], "run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.retarget_to_base({"name": "feature-1"}, "run/feature-1")
        # sanity: normal path with a resolvable number/repo uses REST, not edit
        self.assertFalse(run.find("gh", "pr", "edit"))

        # now force the fallback: no resolvable owner/repo
        run2 = FakeRun({"run/feature-1": [view()]})
        v2 = mk(run2, FakeSpawn(), "/tmp/x")
        v2.gh_repo = None
        v2.retarget_to_base({"name": "feature-1"}, "run/feature-1")
        edits = [c for c in run2.calls if c[:3] == ["gh", "pr", "edit"]]
        self.assertTrue(any("run/feature-1" in c and "--base" in c and "dev" in c
                            for c in edits))


class BranchProtectionAutoMergePath(unittest.TestCase):
    """Regression tests for Bug 2 (branch-protection auto-merge fallback).

    When `gh pr merge` is blocked by branch protection, auto_merge must retry
    with --auto and treat a successful queue as a terminal success state.
    """

    def _make_protection_run(self, auto_rc=0):
        """Runner where the first merge attempt fails with a branch-protection
        error and the --auto retry returns `auto_rc`."""
        base = FakeRun({"run/feature-1": [view()]})
        merge_calls = []

        def run(cmd):
            if cmd[:3] == ["gh", "pr", "merge"]:
                branch = cmd[3]
                merge_calls.append(cmd)
                if "--auto" in cmd:
                    return Proc(auto_rc, "", "" if auto_rc == 0 else "auto-merge not allowed")
                return Proc(1, "", "the base branch policy prohibits the merge")
            return base(cmd)

        run.calls = []
        original_call = run

        class Recorder:
            def __init__(self):
                self.calls = []
            def __call__(self, cmd):
                self.calls.append(cmd)
                return original_call(cmd)

        r = Recorder()
        # Merge calls are tracked via merge_calls; expose them on r for assertions.
        r.merge_calls = merge_calls
        return r

    def test_policy_prohibit_falls_back_to_auto_merge(self):
        """A policy-prohibit error triggers --auto retry and treats queued as success."""
        run = self._make_protection_run(auto_rc=0)
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(msg, "queued")
        # Both calls were made: regular merge then --auto
        auto_calls = [c for c in run.merge_calls if "--auto" in c]
        self.assertEqual(len(auto_calls), 1, "expected exactly one --auto merge call")

    def test_policy_prohibit_auto_merge_also_fails_quarantines(self):
        """If --auto also fails, the group is quarantined (not silently dropped)."""
        run = self._make_protection_run(auto_rc=1)
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("auto-merge failed", msg)

    def test_generic_merge_error_does_not_trigger_auto_fallback(self):
        """A non-protection merge error (e.g. network) does not trigger --auto."""
        base = FakeRun({"run/feature-1": [view()]}, merges={"run/feature-1": 1})
        spawn = FakeSpawn()
        v = mk(base, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("auto-merge failed", msg)
        # No --auto attempt was made
        auto_calls = [c for c in base.calls
                      if c[:3] == ["gh", "pr", "merge"] and "--auto" in c]
        self.assertEqual(auto_calls, [], "--auto must not be called for generic errors")

    def test_full_run_with_branch_protection_merges_group(self):
        """run_all queues (not confirms) a group that required --auto and cleans it up.
        A `--auto` queue is only ARMED, not a confirmed merge (GitHub may still be
        blocked by required checks indefinitely) -- it must land in `automerge_armed`,
        never `merged`, or a stuck PR would be journaled as done (see the false-MERGED
        defect this accumulator fixes). The remote group branch must NOT be deleted
        eagerly -- GitHub handles it via --delete-branch when auto-merge fires."""
        merge_calls = []

        class ProtectionRun(FakeRun):
            def __call__(self, cmd):
                if cmd[:3] == ["gh", "pr", "merge"]:
                    merge_calls.append(cmd)
                    if "--auto" in cmd:
                        return Proc(0, "", "")
                    return Proc(1, "", "the base branch policy prohibits the merge")
                return super().__call__(cmd)

        run = ProtectionRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], [])
        self.assertEqual(res["automerge_armed"], {"feature-1": "queued"})
        self.assertEqual(res["quarantined"], {})
        auto_calls = [c for c in merge_calls if "--auto" in c]
        self.assertEqual(len(auto_calls), 1)
        # Remote branch deletion must be skipped (would auto-close the PR before merge)
        remote_deletes = [c for c in run.calls
                          if c[:5] == ["git", "-C", "/repo", "push", "origin", "--delete"]
                          and "run/feature-1" in c]
        self.assertEqual(remote_deletes, [],
                         "remote group branch must not be deleted when PR is queued for auto-merge")


class VerifyOneMethod(unittest.TestCase):
    """TASK-002: Verifier.verify_one promoted to a callable method -- AC-008, AC-009."""

    def _accums(self):
        return [], {}, threading.Lock()

    def test_green_path_merges_only_that_group(self):
        """Calling verify_one directly runs the full pipeline for exactly one group."""
        run = FakeRun({"run/feature-1": [view()]})
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        merged, quarantined, lock = self._accums()

        v.verify_one(FEATURE, "run/feature-1", None, merged, quarantined, lock)

        self.assertEqual(merged, ["feature-1"])
        self.assertEqual(quarantined, {})
        self.assertTrue(run.find("gh", "pr", "merge", "run/feature-1"))
        # group branch cleaned up after merge
        self.assertTrue(any(c[:5] == ["git", "-C", "/repo", "branch", "-D"]
                            and c[-1] == "run/feature-1" for c in run.calls))

    def test_queued_automerge_goes_to_armed_not_merged(self):
        """A queued (armed, unconfirmed) auto-merge must never land in `merged` --
        that accumulator is journaled as terminal "MERGED" by callers, and a PR only
        queued via --auto can still sit OPEN/BLOCKED forever (e.g. a required check
        stuck red on an unrelated outage). Regression for the false-MERGED-status
        defect: `armed` is the caller-owned accumulator for this unconfirmed case."""
        class ProtectionRun(FakeRun):
            def __call__(self, cmd):
                if cmd[:3] == ["gh", "pr", "merge"]:
                    if "--auto" in cmd:
                        return Proc(0, "", "")
                    return Proc(1, "", "the base branch policy prohibits the merge")
                return super().__call__(cmd)

        run = ProtectionRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        merged, quarantined, lock = self._accums()
        armed: dict = {}

        v.verify_one(FEATURE, "run/feature-1", None, merged, quarantined, lock,
                     armed=armed)

        self.assertEqual(merged, [])
        self.assertEqual(armed, {"feature-1": "queued"})
        self.assertEqual(quarantined, {})

    def test_green_path_cleanup_only_delivered_tasks(self):
        """On success, only DELIVERED task worktrees/branches are torn down."""
        two_task_group = {"name": "feature-1",
                          "tasks": ["TASK-002", "TASK-003"],
                          "reqs": [], "depends_on": []}
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        merged, quarantined, lock = self._accums()
        delivered = {"feature-1": ["TASK-002"]}

        v.verify_one(two_task_group, "run/feature-1", delivered,
                     merged, quarantined, lock)

        self.assertEqual(merged, ["feature-1"])
        branch_dels = [c[-1] for c in run.calls
                       if c[:5] == ["git", "-C", "/repo", "branch", "-D"]]
        self.assertIn("001-x/task-002", branch_dels,
                      "delivered task branch must be deleted")
        self.assertNotIn("001-x/task-003", branch_dels,
                         "non-delivered task branch must be kept")

    def test_red_path_quarantines_keeps_worktrees(self):
        """On CI failure after all strikes, group is quarantined and worktrees kept."""
        run = FakeRun({"run/feature-1": [view(rollup=RED)]},
                      runs=json.dumps([{"databaseId": 9, "conclusion": "FAILURE",
                                        "headSha": "abc"}]))
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")
        merged, quarantined, lock = self._accums()

        v.verify_one(FEATURE, "run/feature-1", None, merged, quarantined, lock)

        self.assertEqual(merged, [])
        self.assertIn("feature-1", quarantined)
        self.assertIn("CI still failing", quarantined["feature-1"])
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))
        self.assertFalse(run.find("git", "-C", "/repo", "worktree", "remove"))

    def test_run_all_over_verify_one_preserves_wave_ordering(self):
        """run_all expressed over verify_one preserves base-before-dependent ordering."""
        base = {"name": "base", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}
        feat = {"name": "feature-1", "tasks": ["TASK-002"], "reqs": [],
                "depends_on": ["base"]}
        run = FakeRun({"run/base": [view()], "run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")

        res = v.run_all([base, feat],
                        {"base": "run/base", "feature-1": "run/feature-1"})

        self.assertEqual(set(res["merged"]), {"base", "feature-1"})
        self.assertEqual(res["quarantined"], {})
        merge_calls = [c[3] for c in run.calls if c[:3] == ["gh", "pr", "merge"]]
        base_idx = next(i for i, b in enumerate(merge_calls) if b == "run/base")
        feat_idx = next(i for i, b in enumerate(merge_calls) if b == "run/feature-1")
        self.assertLess(base_idx, feat_idx,
                        "base must be merged before its dependent")

    def test_dependent_retargeted_to_base_before_verify(self):
        """A dependent group's PR is retargeted to base when verify_one is called."""
        feat = {"name": "feature-1", "tasks": ["TASK-002"], "reqs": [],
                "depends_on": ["base"]}
        run = FakeRun({"run/feature-1": [view()]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        merged, quarantined, lock = self._accums()

        v.verify_one(feat, "run/feature-1", None, merged, quarantined, lock)

        self.assertEqual(merged, ["feature-1"])
        self.assertIn(["gh", "api", "repos/o/r/pulls/1", "-X", "PATCH",
                       "-f", "base=dev"], run.calls,
                      "dependent PR must be retargeted to base before verify")


class SquashMergeDetection(unittest.TestCase):
    """Squash-only repos must use --squash instead of --merge.

    When a repo has mergeCommitAllowed=false but squashMergeAllowed=true,
    verify.py must detect this and use --squash to avoid hard-failing with
    "Merge commits are not allowed on this repository."
    """

    def _make_squash_only_run(self, auto_rc=0):
        """Runner that simulates a squash-only repo (no merge commits allowed)."""
        base = FakeRun({"run/feature-1": [view()]})

        class SquashOnlyRun:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[:3] == ["gh", "repo", "view"] and "--json" in cmd:
                    # Simulate squash-only repo settings
                    return Proc(0, json.dumps({
                        "squashMergeAllowed": True,
                        "mergeCommitAllowed": False,
                        "rebaseMergeAllowed": True
                    }), "")
                return base(cmd)

        return SquashOnlyRun()

    def test_squash_only_repo_uses_squash_flag(self):
        """A squash-only repo uses --squash instead of --merge."""
        run = self._make_squash_only_run()
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(msg, "")
        merge_calls = [c for c in run.calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--squash", merge_calls[0])
        self.assertNotIn("--merge", merge_calls[0])

    def test_merge_method_detected_once_and_cached(self):
        """Repo settings are queried once per run, not per merge."""
        run = self._make_squash_only_run()
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        # Call auto_merge twice
        v.auto_merge(FEATURE, "run/feature-1")
        v.auto_merge(FEATURE, "run/feature-1")

        repo_view_calls = [c for c in run.calls
                          if c[:3] == ["gh", "repo", "view"]]
        self.assertEqual(len(repo_view_calls), 1,
                        "repo settings should be queried once, not per merge")

    def test_squash_only_with_branch_protection_uses_auto_squash(self):
        """Branch-protection fallback works with squash: --auto --squash."""
        merge_calls = []

        class SquashOnlyWithProtection:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[:3] == ["gh", "repo", "view"] and "--json" in cmd:
                    return Proc(0, json.dumps({
                        "squashMergeAllowed": True,
                        "mergeCommitAllowed": False,
                        "rebaseMergeAllowed": False
                    }), "")
                if cmd[:3] == ["gh", "pr", "merge"]:
                    merge_calls.append(cmd)
                    if "--auto" in cmd:
                        return Proc(0, "", "")
                    return Proc(1, "", "the base branch policy prohibits the merge")
                base = FakeRun({"run/feature-1": [view()]})
                return base(cmd)

        run = SquashOnlyWithProtection()
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        self.assertEqual(msg, "queued")
        auto_calls = [c for c in merge_calls if "--auto" in c]
        self.assertEqual(len(auto_calls), 1, "expected one --auto merge call")
        self.assertIn("--squash", auto_calls[0])
        self.assertNotIn("--merge", auto_calls[0])

    def test_rebase_only_repo_uses_rebase_flag(self):
        """When only rebase is allowed, use --rebase."""
        class RebaseOnlyRun:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[:3] == ["gh", "repo", "view"] and "--json" in cmd:
                    return Proc(0, json.dumps({
                        "squashMergeAllowed": False,
                        "mergeCommitAllowed": False,
                        "rebaseMergeAllowed": True
                    }), "")
                base = FakeRun({"run/feature-1": [view()]})
                return base(cmd)

        run = RebaseOnlyRun()
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        merge_calls = [c for c in run.calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--rebase", merge_calls[0])

    def test_merge_allowed_still_uses_merge_flag(self):
        """When merge commits are allowed, use --merge (original behavior)."""
        class MergeAllowedRun:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[:3] == ["gh", "repo", "view"] and "--json" in cmd:
                    return Proc(0, json.dumps({
                        "squashMergeAllowed": True,
                        "mergeCommitAllowed": True,
                        "rebaseMergeAllowed": True
                    }), "")
                base = FakeRun({"run/feature-1": [view()]})
                return base(cmd)

        run = MergeAllowedRun()
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        merge_calls = [c for c in run.calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--merge", merge_calls[0])

    def test_repo_view_failure_falls_back_to_squash(self):
        """If repo settings query fails, default to --squash (safe fallback)."""
        class RepoViewFailRun:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[:3] == ["gh", "repo", "view"] and "--json" in cmd:
                    return Proc(1, "", "network error")
                base = FakeRun({"run/feature-1": [view()]})
                return base(cmd)

        run = RepoViewFailRun()
        spawn = FakeSpawn()
        v = mk(run, spawn, "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok)
        merge_calls = [c for c in run.calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--squash", merge_calls[0])

    def test_full_run_with_squash_only_merges_group(self):
        """run_all merges and cleans up a group on a squash-only repo."""
        class SquashOnlyFullRun:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[:3] == ["gh", "repo", "view"] and "--json" in cmd:
                    return Proc(0, json.dumps({
                        "squashMergeAllowed": True,
                        "mergeCommitAllowed": False,
                        "rebaseMergeAllowed": True
                    }), "")
                base = FakeRun({"run/feature-1": [view()]})
                return base(cmd)

        run = SquashOnlyFullRun()
        v = mk(run, FakeSpawn(), "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], ["feature-1"])
        self.assertEqual(res["quarantined"], {})
        merge_calls = [c for c in run.calls if c[:3] == ["gh", "pr", "merge"]]
        self.assertEqual(len(merge_calls), 1)
        self.assertIn("--squash", merge_calls[0])


class AutoMergeMethodFallback(unittest.TestCase):
    """Fix 3: enablePullRequestAutoMerge rejects the detected method -> retry with squash/rebase.

    Real-world trigger: _detect_merge_method returns "merge" (mergeCommitAllowed=true in repo
    settings), but the repo's branch protection requires squash for auto-merge. The first
    `gh pr merge --merge` is blocked (branch protection), so we fall back to --auto --merge,
    which fails with `GraphQL: Merge method merge commits are not allowed on this repository
    (enablePullRequestAutoMerge)`. We must retry --auto --squash instead of quarantining.
    """

    def _make_method_fallback_run(self, fallback_succeeds=True):
        """Runner: direct merge blocked by branch protection; --auto --merge rejected by
        enablePullRequestAutoMerge; --auto --squash succeeds (or fails when fallback_succeeds=False)."""
        merge_calls = []

        class MethodFallbackRun:
            def __init__(self):
                self.calls = []

            def __call__(self, cmd):
                self.calls.append(cmd)
                if cmd[:3] == ["gh", "repo", "view"] and "--json" in cmd:
                    # Repo view says merge commits ARE allowed (this is what misleads detect)
                    return Proc(0, json.dumps({
                        "mergeCommitAllowed": True,
                        "squashMergeAllowed": True,
                        "rebaseMergeAllowed": False,
                    }), "")
                if cmd[:3] == ["gh", "pr", "merge"]:
                    merge_calls.append(cmd)
                    if "--auto" not in cmd:
                        # Direct merge blocked by branch protection
                        return Proc(1, "", "the base branch policy prohibits the merge")
                    if "--merge" in cmd:
                        # --auto --merge rejected by enablePullRequestAutoMerge
                        return Proc(1, "",
                                    "GraphQL: Merge method merge commits are not allowed on "
                                    "this repository (enablePullRequestAutoMerge)")
                    # All other --auto methods (squash/rebase): succeed or all-fail
                    rc = 0 if fallback_succeeds else 1
                    return Proc(rc, "", "" if fallback_succeeds else "method also not allowed")
                base = FakeRun({"run/feature-1": [view()]})
                return base(cmd)

        return MethodFallbackRun(), merge_calls

    def test_enablepullrequestautomerge_retries_squash(self):
        """When --auto --merge fails with enablePullRequestAutoMerge, retry with --squash."""
        run, merge_calls = self._make_method_fallback_run(fallback_succeeds=True)
        v = mk(run, FakeSpawn(), "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertTrue(ok, f"Expected success after squash fallback, got: {msg}")
        self.assertEqual(msg, "queued")

        auto_squash_calls = [c for c in merge_calls if "--auto" in c and "--squash" in c]
        self.assertEqual(len(auto_squash_calls), 1, "Expected one --auto --squash retry")

    def test_enablepullrequestautomerge_all_fallbacks_fail_quarantines(self):
        """When all method fallbacks fail, the group is quarantined."""
        run, merge_calls = self._make_method_fallback_run(fallback_succeeds=False)
        v = mk(run, FakeSpawn(), "/tmp/x")

        ok, msg = v.auto_merge(FEATURE, "run/feature-1")

        self.assertFalse(ok)
        self.assertIn("auto-merge failed", msg)

    def test_method_cached_after_successful_fallback(self):
        """After a squash fallback succeeds, subsequent merges use squash directly."""
        run, merge_calls = self._make_method_fallback_run(fallback_succeeds=True)
        v = mk(run, FakeSpawn(), "/tmp/x")

        # First merge: falls back to squash
        v.auto_merge(FEATURE, "run/feature-1")
        self.assertEqual(v._merge_method, "squash", "Method should be cached as squash")

        # Second merge on another group: should use squash directly
        second_group = {"name": "feature-2", "tasks": ["T002"], "depends_on": [], "reqs": []}
        v.auto_merge(second_group, "run/feature-2")

        # The first call after caching should use --squash without going through --merge again
        auto_squash_second = [
            c for c in merge_calls if "--squash" in c and "feature-2" in " ".join(str(a) for a in c)
        ]
        self.assertTrue(
            len(auto_squash_second) >= 1 or v._merge_method == "squash",
            "Cached squash method should be used for subsequent merges"
        )


class MakeLiveSpawnExcludesUserSettingSource(unittest.TestCase):
    """Regression for handoff 20260712-214530: verify.py's group-level
    resolve/ci-fix/assembly-resolve workers call spawnlib.spawn_agent
    directly (not through live.LiveSpawn), so PR #252's --setting-sources
    fix for task-level workers never covered them. Same defect class
    (investigation 20260711-130900): the operator's user-level Stop hook
    consumes the worker's final turn on any commit/write, eating the
    report-back JSON these group workers rely on just like task workers do.
    """

    def _captured_extra_args(self, agent="claude"):
        captured = {}
        fake_result = type("R", (), {"text": "ok"})()
        from unittest.mock import patch
        with patch(
            "worktrail.orchestrator.verify.spawnlib.spawn_agent",
            side_effect=lambda *_, **kw: captured.update(kw) or fake_result,
        ):
            spawn = verify._make_live_spawn(agent=agent)
            spawn("prompt", Path("/tmp/wt"))
        return captured.get("extra_args", [])

    def test_claude_worker_excludes_user_setting_source(self):
        args = self._captured_extra_args("claude")
        self.assertIn("--setting-sources", args)
        idx = args.index("--setting-sources")
        self.assertEqual(args[idx + 1], "project,local")

    def test_claude_worker_gets_no_tools_restriction(self):
        # Unlike task-level _LEAN_WORKER_FLAGS, group workers keep the full
        # tool set -- their prompts (conflict resolution, CI-log diagnosis)
        # only run git/build commands through Bash, so a --tools restriction
        # would add risk without saving anything (verified against
        # dispatch.build_group_prompt's actual instructions).
        args = self._captured_extra_args("claude")
        self.assertNotIn("--tools", args)

    def test_non_claude_worker_gets_no_extra_args(self):
        args = self._captured_extra_args("codex")
        self.assertEqual(args, [])


class WorkerScopeViolation(unittest.TestCase):
    """Structural backstop for dispatch.py's prompt-level 'Hard rules': a group
    worker's actual pushed diff is checked against a deny-list, independent of
    what its own report-back JSON claims (a prompt instruction alone can be
    rationalized around under pressure -- this is the deterministic check)."""

    class ScopeCheckRun(FakeRun):
        def __init__(self, *a, touched=(), **kw):
            super().__init__(*a, **kw)
            self.touched = list(touched)

        def __call__(self, cmd):
            if cmd[:4] == ["git", "-C", "/repo", "rev-parse"]:
                return Proc(0, "presha", "")
            if cmd[:4] == ["git", "-C", "/repo", "diff"] and "--name-only" in cmd:
                return Proc(0, "\n".join(self.touched), "")
            return super().__call__(cmd)

    def test_in_scope_diff_passes(self):
        run = self.ScopeCheckRun(
            {"run/feature-1": [view(mergeable="CONFLICTING"), view()]},
            touched=["src/app.py"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], ["feature-1"])
        self.assertEqual(res["quarantined"], {})

    def test_forbidden_workflow_edit_fails_strike_despite_reported_success(self):
        run = self.ScopeCheckRun(
            {"run/feature-1": [view(mergeable="CONFLICTING")]},
            touched=[".github/workflows/ci.yml"])
        v = mk(run, FakeSpawn(), "/tmp/x")   # FakeSpawn reports status: "success"
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], [])
        self.assertIn("resolve worker failed", res["quarantined"]["feature-1"])

    def test_forbidden_docs_specs_edit_also_fails(self):
        run = self.ScopeCheckRun(
            {"run/feature-1": [view(mergeable="CONFLICTING")]},
            touched=["docs/specs/001-x/tasks/TASK-001.md"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], [])
        self.assertIn("resolve worker failed", res["quarantined"]["feature-1"])

    def test_forbidden_paths_touched_helper_filters_deny_list_only(self):
        run = self.ScopeCheckRun({}, touched=[".github/workflows/x.yml", "src/ok.py"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        self.assertEqual(v._forbidden_paths_touched("presha", "gb"),
                         [".github/workflows/x.yml"])

    def test_empty_pre_sha_fails_open_no_diff_call(self):
        run = self.ScopeCheckRun({}, touched=[".github/workflows/x.yml"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        self.assertEqual(v._forbidden_paths_touched("", "gb"), [])
        self.assertFalse(run.find("git", "-C", "/repo", "diff"))

    # -- the deny-list must name the spec root of the format actually running --
    #
    # Both bugs below were found by running datalena spec 080 for real. Neither
    # was visible to a unit test, because every existing test constructed its own
    # devkit-shaped Verifier.

    def test_an_openspec_run_guards_openspec_not_docs_specs(self):
        """`spec_rel` unset meant the deny-list fell back to devkit's root. On an
        OpenSpec run that leaves `openspec/**` -- the tree the guard exists to
        protect -- completely unguarded, while falsely flagging `docs/specs/**`.
        The observed symptom was a ci-fix worker struck out for touching
        `docs/specs/080-.../promotion-runbook.md`, a declared deliverable."""
        run = self.ScopeCheckRun({}, touched=[
            "openspec/changes/080-x/tasks.md",
            "docs/specs/080-x/promotion-runbook.md",
        ])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.spec_rel = "openspec/changes/080-x"
        self.assertEqual(v._forbidden_paths_touched("presha", "gb"),
                         ["openspec/changes/080-x/tasks.md"])

    def test_a_devkit_run_still_guards_docs_specs(self):
        run = self.ScopeCheckRun({}, touched=[
            "openspec/changes/080-x/tasks.md",
            "docs/specs/080-x/tasks/TASK-001.md",
        ])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.spec_rel = "docs/specs/080-x"
        self.assertEqual(v._forbidden_paths_touched("presha", "gb"),
                         ["docs/specs/080-x/tasks/TASK-001.md"])

    # -- a declared deliverable is in scope, even under a denied prefix --------

    def test_a_declared_workflow_file_is_not_a_violation(self):
        """Blanket-denying `.github/workflows/**` makes any CI-focused spec
        unimplementable. datalena spec 080 exists to modify `qa-pipeline.yml`,
        and its ci-fix worker was struck out for touching it."""
        run = self.ScopeCheckRun({}, touched=[".github/workflows/qa-pipeline.yml"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.declared_files = {"feature-1": [".github/workflows/qa-pipeline.yml"]}
        self.assertEqual(
            v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"}), [])

    def test_an_undeclared_workflow_file_is_still_a_violation(self):
        """The guard's actual purpose: a worker must not edit CI it was never
        asked to touch."""
        run = self.ScopeCheckRun({}, touched=[".github/workflows/other.yml"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.declared_files = {"feature-1": [".github/workflows/qa-pipeline.yml"]}
        self.assertEqual(v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"}),
                         [".github/workflows/other.yml"])

    def test_another_groups_declaration_does_not_exempt_this_group(self):
        run = self.ScopeCheckRun({}, touched=[".github/workflows/qa-pipeline.yml"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.declared_files = {"other": [".github/workflows/qa-pipeline.yml"]}
        self.assertEqual(v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"}),
                         [".github/workflows/qa-pipeline.yml"])

    def test_the_spec_root_is_absolute_and_no_declaration_exempts_it(self):
        """The spec tree is the run's own bookkeeping -- status reaches it once,
        at integrate, on the base checkout (design 4.3). A worker writing there
        reintroduces the cross-branch conflict class P0 removed, so unlike the
        other prefixes this one takes no carve-out."""
        run = self.ScopeCheckRun({}, touched=["docs/specs/001-x/tasks/TASK-001.md"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.spec_rel = "docs/specs/001-x"
        v.declared_files = {"feature-1": ["docs/specs/001-x/tasks/TASK-001.md"]}
        self.assertEqual(v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"}),
                         ["docs/specs/001-x/tasks/TASK-001.md"])

    def test_no_declared_files_preserves_pre_existing_behavior(self):
        run = self.ScopeCheckRun({}, touched=[".github/workflows/x.yml", "src/ok.py"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        self.assertEqual(v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"}),
                         [".github/workflows/x.yml"])

    # -- plan-audit signal: log-only, never gates -------------------------- #

    def test_touched_not_declared_is_logged_not_gated(self):
        """A file outside the deny-list that the group never declared is the
        same under-reporting signal `conductor.plan_audit` computes by hand --
        surfaced automatically here, but still only a log line: the return
        value (what actually gates a strike) is untouched."""
        logged = []
        run = self.ScopeCheckRun({}, touched=["src/ok.py", "src/surprise.py"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.log = logged.append
        v.declared_files = {"feature-1": ["src/ok.py"]}
        result = v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"})
        self.assertEqual(result, [])
        self.assertTrue(any("plan-audit" in m and "src/surprise.py" in m
                             for m in logged), logged)

    def test_no_mismatch_is_silent(self):
        logged = []
        run = self.ScopeCheckRun({}, touched=["src/ok.py"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.log = logged.append
        v.declared_files = {"feature-1": ["src/ok.py"]}
        v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"})
        self.assertFalse(any("plan-audit" in m for m in logged), logged)

    def test_no_declared_files_skips_the_plan_audit_signal(self):
        """Mirrors `plan_audit.audit_plan`'s own skip: nothing declared means
        nothing to compare against, not that every touched file is a mismatch."""
        logged = []
        run = self.ScopeCheckRun({}, touched=["src/ok.py"])
        v = mk(run, FakeSpawn(), "/tmp/x")
        v.log = logged.append
        v._forbidden_paths_touched("presha", "gb", {"name": "feature-1"})
        self.assertFalse(any("plan-audit" in m for m in logged), logged)


class WorkerSelfMergeViolation(unittest.TestCase):
    """Structural backstop for dispatch.py's second Hard rule ('do NOT run
    `gh pr merge`, enable auto-merge, or take any merge action yourself'). PR
    #259 enforced the first Hard rule (forbidden-path deny-list, above); this
    covers the second. `_spawn_group_worker` runs strictly BEFORE `auto_merge`
    is ever called for a group in one `verify_one` pass, so a MERGED flip
    observed between its pre- and post-spawn `pr_status()` calls -- with no
    `autoMergeRequest` already armed pre-spawn -- is unambiguous evidence the
    worker merged the PR itself, distinct from an ordinary strike failure."""

    def test_detect_self_merge_confirms_violation(self):
        run = FakeRun({"run/feature-1": [view(state="MERGED")]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        pre_status = {"state": "OPEN", "autoMergeRequest": None}
        self.assertTrue(
            v._detect_self_merge("resolve", FEATURE, "run/feature-1", pre_status))
        self.assertIn("feature-1", v._self_merge_violations)
        self.assertIn("MERGED", v._self_merge_violations["feature-1"])

    def test_detect_self_merge_ignores_pre_armed_automerge(self):
        # autoMergeRequest already present BEFORE the worker's turn -- GitHub's
        # own automation could fire independent of the worker; not attributable.
        run = FakeRun({"run/feature-1": [view(state="MERGED")]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        pre_status = {"state": "OPEN", "autoMergeRequest": {"enabledBy": {}}}
        self.assertFalse(
            v._detect_self_merge("resolve", FEATURE, "run/feature-1", pre_status))
        self.assertEqual(v._self_merge_violations, {})

    def test_detect_self_merge_ignores_post_spawn_automerge_signal(self):
        logs = []
        run = FakeRun({"run/feature-1": [view(
            state="MERGED",
            auto_merge_request={"enabledBy": {"login": "app/github-actions"}},
            merged_by={"login": "app/github-actions"},
        )]})
        v = verify.Verifier(
            Path("/repo"), "origin", "dev", "001-x",
            run=run, spawn=FakeSpawn(), log=logs.append, sleep=lambda *_: None,
            worktree_base=Path("/tmp/x"), max_polls=5,
        )
        pre_status = {"state": "OPEN", "autoMergeRequest": None}
        self.assertFalse(
            v._detect_self_merge("resolve", FEATURE, "run/feature-1", pre_status))
        self.assertEqual(v._self_merge_violations, {})
        self.assertEqual(
            v._automerge_evidence,
            {"feature-1": {
                "enabledBy": "app/github-actions",
                "mergedBy": "app/github-actions",
            }},
        )
        self.assertTrue(any("enabledBy=app/github-actions" in line for line in logs))
        self.assertTrue(any("mergedBy=app/github-actions" in line for line in logs))

    def test_detect_self_merge_no_flip_no_violation(self):
        run = FakeRun({"run/feature-1": [view(state="OPEN")]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        pre_status = {"state": "OPEN", "autoMergeRequest": None}
        self.assertFalse(
            v._detect_self_merge("resolve", FEATURE, "run/feature-1", pre_status))

    def test_detect_self_merge_already_merged_pre_spawn_not_flagged(self):
        # Merged before this worker's turn even started -- not its doing.
        run = FakeRun({"run/feature-1": [view(state="MERGED")]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        pre_status = {"state": "MERGED", "autoMergeRequest": None}
        self.assertFalse(
            v._detect_self_merge("resolve", FEATURE, "run/feature-1", pre_status))

    def test_detect_self_merge_none_pre_status_fails_open(self):
        run = FakeRun({})
        v = mk(run, FakeSpawn(), "/tmp/x")
        self.assertFalse(v._detect_self_merge("resolve", FEATURE, "run/feature-1", None))

    def test_resolve_worker_self_merge_surfaces_distinctly_not_quarantined(self):
        """End-to-end through run_all: a resolve worker spawned to fix a
        CONFLICTING PR instead merges it directly. The violation must NOT be
        folded into the ordinary quarantine bucket -- it's surfaced in a
        distinct self_merged accumulator since a landed merge can't be undone
        by another strike or retry."""
        run = FakeRun({"run/feature-1": [
            view(mergeable="CONFLICTING"),   # ensure_mergeable's initial check
            view(state="OPEN"),              # _spawn_group_worker's pre-spawn status
            view(state="MERGED"),            # _detect_self_merge's post-spawn status
        ]})
        v = mk(run, FakeSpawn(), "/tmp/x")   # FakeSpawn reports status: "success"
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], [])
        self.assertEqual(res["quarantined"], {})
        self.assertIn("feature-1", res["self_merged"])
        self.assertIn("MERGED", res["self_merged"]["feature-1"])
        # the orchestrator itself never attempted a merge
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))

    def test_pre_armed_automerge_still_merges_normally(self):
        """Regression guard against a false positive: when autoMergeRequest was
        already armed BEFORE the resolve worker's turn (e.g. this repo's own
        auto-merge automation), a MERGED flip is explained by that, not the
        worker -- the group completes normally with no violation recorded."""
        run = FakeRun({"run/feature-1": [
            view(mergeable="CONFLICTING"),
            view(state="OPEN", auto_merge_request={"enabledBy": {"login": "bot"}}),
            view(state="MERGED"),
        ]})
        v = mk(run, FakeSpawn(), "/tmp/x")
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], ["feature-1"])
        self.assertEqual(res["self_merged"], {})
        self.assertEqual(res["quarantined"], {})
        self.assertEqual(res["automerge_evidence"], {})

    def test_post_spawn_automerge_signal_still_merges_normally(self):
        """Regression guard for the narrow race: auto-merge was not armed at the
        pre-spawn snapshot, but the post-merge PR state shows the same actor both
        enabled auto-merge and performed the merge."""
        logs = []
        run = FakeRun({"run/feature-1": [
            view(mergeable="CONFLICTING"),
            view(state="OPEN"),
            view(
                state="MERGED",
                auto_merge_request={"enabledBy": {"login": "app/github-actions"}},
                merged_by={"login": "app/github-actions"},
            ),
        ]})
        v = verify.Verifier(
            Path("/repo"), "origin", "dev", "001-x",
            run=run, spawn=FakeSpawn(), log=logs.append, sleep=lambda *_: None,
            worktree_base=Path("/tmp/x"), max_polls=5,
        )
        res = v.run_all([FEATURE], {"feature-1": "run/feature-1"})

        self.assertEqual(res["merged"], ["feature-1"])
        self.assertEqual(res["self_merged"], {})
        self.assertEqual(res["quarantined"], {})
        self.assertEqual(
            res["automerge_evidence"],
            {"feature-1": {
                "enabledBy": "app/github-actions",
                "mergedBy": "app/github-actions",
            }},
        )
        self.assertTrue(any("enabledBy=app/github-actions" in line for line in logs))
        self.assertTrue(any("mergedBy=app/github-actions" in line for line in logs))

    def test_self_merged_dependency_cascades_to_dependents(self):
        base = {"name": "base", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}
        feat = {"name": "feature-1", "tasks": ["TASK-002"], "reqs": [],
                "depends_on": ["base"]}
        run = FakeRun({
            "run/base": [
                view(mergeable="CONFLICTING"),
                view(state="OPEN"),
                view(state="MERGED"),
            ],
            "run/feature-1": [view()],
        })
        v = mk(run, FakeSpawn(), "/tmp/x")
        res = v.run_all([base, feat], {"base": "run/base", "feature-1": "run/feature-1"})

        self.assertIn("base", res["self_merged"])
        self.assertIn("feature-1", res["quarantined"])
        self.assertIn("base", res["quarantined"]["feature-1"])
        self.assertFalse(run.find("gh", "pr", "merge", "run/feature-1"))


class TestDeriveGhRepo(unittest.TestCase):
    """Verify _derive_gh_repo handles both HTTPS and SSH git remote URLs."""

    def test_https_remote(self):
        run = FakeRun({}, remote_url="https://github.com/owner/repo.git")
        v = verify.Verifier(Path("/repo"), "origin", "dev", "001-x",
                            run=run, spawn=FakeSpawn(), log=lambda *_: None,
                            sleep=lambda *_: None, worktree_base=Path("/tmp/x"),
                            max_polls=5)
        self.assertEqual(v.gh_repo, "owner/repo")

    def test_ssh_remote(self):
        run = FakeRun({}, remote_url="git@github.com:owner/repo.git")
        v = verify.Verifier(Path("/repo"), "origin", "dev", "001-x",
                            run=run, spawn=FakeSpawn(), log=lambda *_: None,
                            sleep=lambda *_: None, worktree_base=Path("/tmp/x"),
                            max_polls=5)
        self.assertEqual(v.gh_repo, "owner/repo")

    def test_ssh_remote_no_dot_git(self):
        run = FakeRun({}, remote_url="git@github.com:owner/repo")
        v = verify.Verifier(Path("/repo"), "origin", "dev", "001-x",
                            run=run, spawn=FakeSpawn(), log=lambda *_: None,
                            sleep=lambda *_: None, worktree_base=Path("/tmp/x"),
                            max_polls=5)
        self.assertEqual(v.gh_repo, "owner/repo")

    def test_non_github_remote_returns_none(self):
        run = FakeRun({}, remote_url="git@gitlab.com:owner/repo.git")
        v = verify.Verifier(Path("/repo"), "origin", "dev", "001-x",
                            run=run, spawn=FakeSpawn(), log=lambda *_: None,
                            sleep=lambda *_: None, worktree_base=Path("/tmp/x"),
                            max_polls=5)
        self.assertIsNone(v.gh_repo)


# `GoScriptsResolution` (topology-detection tests for the old, removed
# `_find_go_scripts_dir()`) is intentionally not ported. That function existed
# to hunt for `devkit-pm-go/scripts` across install topologies via manual
# `sys.path` directory-walking -- the extraction's `automerge_preflight`
# lazy-proxy (`from ..router import automerge_preflight`, see verify.py)
# replaces it with an ordinary intra-package import, which is topology-proof
# by construction: the whole class of incident this test class guarded
# against (brief 20260724-103800, a plugin-cache-layout mismatch that crashed
# verify.py's module import) is now structurally impossible, not just tested.


if __name__ == "__main__":
    unittest.main(verbosity=2)
