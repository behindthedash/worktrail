#!/usr/bin/env python3
"""Unit tests for the periodic stale-worktree sweep (report-only).

Exercises real throwaway git repos + real `git worktree add` checkouts rather
than mocking subprocess -- the logic under test *is* the git plumbing
(status, cherry, ls-remote, rev-list), so a fake would just re-assert the
mock.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router import sweep_stale_worktrees as ssw


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def _init_bare_remote(tmp: Path) -> Path:
    remote = tmp / "remote.git"
    remote.mkdir()
    _git(remote, "init", "-q", "--bare", "-b", "main")
    return remote


def _init_canonical(tmp: Path, remote: Path) -> Path:
    canonical = tmp / "myrepo"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(canonical)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(canonical, "config", "user.email", "test@example.com")
    _git(canonical, "config", "user.name", "Test")
    (canonical / "README.md").write_text("base\n", encoding="utf-8")
    _git(canonical, "add", ".")
    _git(canonical, "commit", "-q", "-m", "base")
    _git(canonical, "push", "-q", "origin", "main")
    return canonical


def _worktrees_dir(canonical: Path) -> Path:
    return canonical.parent / f"{canonical.name}-worktrees"


def _add_worktree(canonical: Path, branch: str, dir_name: str | None = None) -> Path:
    # Directory names never contain slashes (worktree.py's real convention uses
    # `{spec_id}-{task_id}`, not the branch name itself, as the checkout dir).
    wt = _worktrees_dir(canonical) / (dir_name or branch.replace("/", "-"))
    _git(canonical, "worktree", "add", "-b", branch, str(wt), "main")
    _git(wt, "config", "user.email", "test@example.com")
    _git(wt, "config", "user.name", "Test")
    return wt


class SweepStaleWorktreesTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.remote = _init_bare_remote(self.tmp)
        self.canonical = _init_canonical(self.tmp, self.remote)

    def tearDown(self):
        self._tmp.cleanup()


class TestNotARepo(SweepStaleWorktreesTestCase):
    def test_non_repo_reports_unchecked(self):
        row = ssw.sweep_repo(self.tmp / "not-a-repo", do_fetch=False)
        self.assertFalse(row["checked"])
        self.assertEqual(row["worktrees"], [])


class TestNoWorktreesDir(SweepStaleWorktreesTestCase):
    def test_repo_with_no_worktrees_reports_empty(self):
        row = ssw.sweep_repo(self.canonical, do_fetch=False)
        self.assertTrue(row["checked"])
        self.assertEqual(row["worktree_count"], 0)


class TestDirtyIsKept(SweepStaleWorktreesTestCase):
    def test_uncommitted_change_is_dirty_not_reclaimable(self):
        wt = _add_worktree(self.canonical, "feature/dirty")
        (wt / "scratch.txt").write_text("wip\n", encoding="utf-8")
        row = ssw.sweep_repo(self.canonical, do_fetch=False)
        self.assertEqual(row["worktree_count"], 1)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "DIRTY")
        self.assertFalse(entry["reclaimable"])


class TestReviewsScratchNotCountedDirty(SweepStaleWorktreesTestCase):
    def test_untracked_openspec_reviews_file_alone_is_not_dirty(self):
        wt = _add_worktree(self.canonical, "feature/reviews-only")
        reviews_dir = wt / "openspec" / "changes" / "some-change" / "reviews"
        reviews_dir.mkdir(parents=True)
        (reviews_dir / "code-review.md").write_text("scratch\n", encoding="utf-8")
        # unmerged (branch has no commits beyond main) + clean except reviews/ scratch
        row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertNotEqual(entry["state"], "DIRTY")


class TestMergedIsReclaimable(SweepStaleWorktreesTestCase):
    def test_squash_equivalent_commit_on_base_marks_merged(self):
        wt = _add_worktree(self.canonical, "feature/done")
        (wt / "feature.txt").write_text("shipped\n", encoding="utf-8")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", "add feature")
        _git(wt, "push", "-q", "origin", "feature/done")

        # Simulate a squash-merge onto main: same patch content, different commit.
        (self.canonical / "feature.txt").write_text("shipped\n", encoding="utf-8")
        _git(self.canonical, "add", ".")
        _git(self.canonical, "commit", "-q", "-m", "add feature (squash)")
        _git(self.canonical, "push", "-q", "origin", "main")
        _git(self.canonical, "fetch", "-q", "origin")

        row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "MERGED")
        self.assertTrue(entry["reclaimable"])


class TestUnmergedIsKept(SweepStaleWorktreesTestCase):
    def test_pushed_unmerged_branch_is_kept(self):
        wt = _add_worktree(self.canonical, "feature/in-progress")
        (wt / "feature.txt").write_text("wip\n", encoding="utf-8")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", "wip commit")
        _git(wt, "push", "-q", "origin", "feature/in-progress")
        _git(self.canonical, "fetch", "-q", "origin")

        row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "UNMERGED")
        self.assertFalse(entry["reclaimable"])


class TestGoneRemoteIsReclaimable(SweepStaleWorktreesTestCase):
    def test_deleted_remote_branch_with_cached_clean_tracking_ref_is_reclaimable(self):
        wt = _add_worktree(self.canonical, "feature/abandoned")
        (wt / "feature.txt").write_text("abandoned\n", encoding="utf-8")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", "abandoned work")
        _git(wt, "push", "-q", "origin", "feature/abandoned")
        _git(self.canonical, "fetch", "-q", "origin")
        # Delete the branch directly on the bare remote (not `git push --delete`,
        # which also prunes the *local* cached tracking ref on modern git and
        # would collapse this into the "no tracking ref at all" ambiguous case
        # below). This reproduces a remote branch removed by something other
        # than this checkout -- e.g. GitHub deleting it on PR merge -- while the
        # local `refs/remotes/origin/feature/abandoned` cache is still present
        # and confirms zero commits unaccounted for.
        _git(self.remote, "update-ref", "-d", "refs/heads/feature/abandoned")

        row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "GONE")
        self.assertTrue(entry["reclaimable"])


class TestNoTrackingRefWithoutPrEvidenceIsKept(SweepStaleWorktreesTestCase):
    def test_pushed_then_fully_pruned_branch_with_no_pr_evidence_is_kept(self):
        # No remote-tracking ref at all (never pushed, or pushed+fully pruned --
        # indistinguishable from git alone) and no `gh` PR record (this test's
        # remote is a bare local repo, not GitHub, so `gh` finds nothing) must
        # default to keep -- never wrongly reclaim what might be unique local work.
        wt = _add_worktree(self.canonical, "feature/local-only")
        (wt / "feature.txt").write_text("local\n", encoding="utf-8")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", "local only commit")

        row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertFalse(entry["reclaimable"])


_SPEC = "some-spec"
_RUNPLAN_TASKS = [
    {"id": "1.1", "files": ["a.py"], "deps": []},
    {"id": "1.2", "files": ["b.py"], "deps": ["1.1"]},
]


def _write_journal(canonical: Path, spec_id: str, groups: dict) -> Path:
    wt_dir = _worktrees_dir(canonical)
    wt_dir.mkdir(parents=True, exist_ok=True)
    path = wt_dir / f"run-{spec_id}.json"
    path.write_text(json.dumps({"groups": groups}), encoding="utf-8")
    return path


def _write_runplan(canonical: Path, spec_id: str, tasks: list) -> Path:
    runplans_dir = _worktrees_dir(canonical) / "runplans"
    runplans_dir.mkdir(parents=True, exist_ok=True)
    path = runplans_dir / f"{spec_id}-abc123.json"
    path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    return path


def _no_reconcile(*_args, **_kwargs):
    return None


class QuarantineSweepTestCase(SweepStaleWorktreesTestCase):
    """Real-git fixtures plus a QUARANTINED journal group `feature-1` (tasks
    1.1 + 1.2 per the RunPlan cache) with a PR URL."""

    PR_URL = "https://example.com/pr/2"

    def setUp(self):
        super().setUp()
        _write_runplan(self.canonical, _SPEC, _RUNPLAN_TASKS)
        _write_journal(
            self.canonical,
            _SPEC,
            {"feature-1": {"state": "QUARANTINED", "pr_url": self.PR_URL}},
        )

    def _pushed_unmerged_worktree(self, dir_name: str) -> Path:
        # Committed and pushed, but `git cherry` still reports the commit
        # unmerged -- today's classification is UNMERGED.
        wt = _add_worktree(
            self.canonical, f"{_SPEC}/{dir_name}", dir_name=f"{_SPEC}-{dir_name}"
        )
        (wt / f"{dir_name}.txt").write_text("work\n", encoding="utf-8")
        _git(wt, "add", ".")
        _git(wt, "commit", "-q", "-m", f"{dir_name} work")
        _git(wt, "push", "-q", "origin", f"{_SPEC}/{dir_name}")
        _git(self.canonical, "fetch", "-q", "origin")
        return wt


class TestAttributionIndex(QuarantineSweepTestCase):
    def test_task_and_verify_worktrees_attributed_to_quarantined_group(self):
        index = ssw.build_attribution_index(self.canonical)
        self.assertEqual(
            set(index), {f"{_SPEC}-1.1", f"{_SPEC}-1.2", f"{_SPEC}-verify-feature-1"}
        )
        for entry in index.values():
            self.assertEqual(entry["spec_id"], _SPEC)
            self.assertEqual(entry["group"], "feature-1")
            self.assertEqual(entry["pr_url"], self.PR_URL)

    def test_merged_and_open_groups_never_indexed(self):
        _write_journal(
            self.canonical,
            _SPEC,
            {
                "feature-1": {"state": "MERGED", "pr_url": self.PR_URL},
                "feature-2": {"state": "OPEN", "pr_url": "https://example.com/pr/3"},
            },
        )
        self.assertEqual(ssw.build_attribution_index(self.canonical), {})

    def test_missing_runplan_still_attributes_verify_worktree_only(self):
        for path in (_worktrees_dir(self.canonical) / "runplans").glob("*.json"):
            path.unlink()
        index = ssw.build_attribution_index(self.canonical)
        self.assertEqual(set(index), {f"{_SPEC}-verify-feature-1"})

    def test_missing_runplan_leaves_task_worktree_on_git_only_path(self):
        for path in (_worktrees_dir(self.canonical) / "runplans").glob("*.json"):
            path.unlink()
        self._pushed_unmerged_worktree("1.1")
        with (
            patch.object(ssw, "pr_state_for_url", return_value="MERGED"),
            patch.object(ssw.quarantine_selfcheck, "reconcile_finding", _no_reconcile),
        ):
            row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "UNMERGED")
        self.assertFalse(entry["reclaimable"])


class TestQuarantineMerged(QuarantineSweepTestCase):
    def test_merged_pr_state_reclaims_branch_cherry_reports_unmerged(self):
        wt = self._pushed_unmerged_worktree("1.1")
        self.assertFalse(
            ssw.is_merged_into_base(self.canonical, "main", f"{_SPEC}/1.1")
        )
        with (
            patch.object(ssw, "pr_state_for_url", return_value="MERGED") as pr_view,
            patch.object(ssw.quarantine_selfcheck, "reconcile_finding", _no_reconcile),
        ):
            row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["path"], str(wt))
        self.assertEqual(entry["state"], "QUARANTINE-MERGED")
        self.assertTrue(entry["reclaimable"])
        self.assertIn(self.PR_URL, entry["reason"])
        self.assertEqual(pr_view.call_args.args[1], self.PR_URL)

    def test_reconcile_record_reclaims_with_empty_pr_url(self):
        _write_journal(
            self.canonical, _SPEC, {"feature-1": {"state": "QUARANTINED", "pr_url": ""}}
        )
        self._pushed_unmerged_worktree("verify-feature-1")
        record = {
            "spec_id": _SPEC,
            "group": "feature-1",
            "method": "base-branch-files",
            "evidence": ["a.py", "b.py"],
        }
        with (
            patch.object(ssw, "pr_state_for_url", return_value=None) as pr_view,
            patch.object(
                ssw.quarantine_selfcheck, "reconcile_finding", return_value=record
            ) as reconcile,
        ):
            row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "QUARANTINE-MERGED")
        self.assertTrue(entry["reclaimable"])
        self.assertIn("base-branch-files", entry["reason"])
        self.assertEqual(pr_view.call_args.args[1], "")
        self.assertEqual(reconcile.call_args.args[1]["pr_url"], "")

    def test_open_pr_without_reconciliation_falls_through(self):
        self._pushed_unmerged_worktree("1.2")
        with (
            patch.object(ssw, "pr_state_for_url", return_value="OPEN"),
            patch.object(ssw.quarantine_selfcheck, "reconcile_finding", _no_reconcile),
        ):
            row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "UNMERGED")
        self.assertFalse(entry["reclaimable"])

    def test_dirty_attributed_worktree_stays_dirty(self):
        wt = self._pushed_unmerged_worktree("1.1")
        (wt / "scratch.txt").write_text("wip\n", encoding="utf-8")
        with (
            patch.object(ssw, "pr_state_for_url", return_value="MERGED") as pr_view,
            patch.object(ssw.quarantine_selfcheck, "reconcile_finding", _no_reconcile),
        ):
            row = ssw.sweep_repo(self.canonical, do_fetch=False)
        entry = row["worktrees"][0]
        self.assertEqual(entry["state"], "DIRTY")
        self.assertFalse(entry["reclaimable"])
        pr_view.assert_not_called()

    def test_evidence_lookup_runs_once_per_group(self):
        for name in ("1.1", "1.2", "verify-feature-1"):
            self._pushed_unmerged_worktree(name)
        with (
            patch.object(ssw, "pr_state_for_url", return_value="MERGED") as pr_view,
            patch.object(ssw.quarantine_selfcheck, "reconcile_finding", _no_reconcile),
        ):
            row = ssw.sweep_repo(self.canonical, do_fetch=False)
        self.assertEqual(row["worktree_count"], 3)
        self.assertEqual(
            [w["state"] for w in row["worktrees"]], ["QUARANTINE-MERGED"] * 3
        )
        self.assertEqual(pr_view.call_count, 1)

    def test_sweep_leaves_worktree_branch_and_journal_untouched(self):
        wt = self._pushed_unmerged_worktree("1.1")
        journal = _worktrees_dir(self.canonical) / f"run-{_SPEC}.json"
        journal_before = journal.read_bytes()
        head_before = _git(wt, "rev-parse", "HEAD").stdout
        with (
            patch.object(ssw, "pr_state_for_url", return_value="MERGED"),
            patch.object(ssw.quarantine_selfcheck, "reconcile_finding", _no_reconcile),
        ):
            row = ssw.sweep_repo(self.canonical, do_fetch=False)
        self.assertEqual(row["reclaimable_count"], 1)
        self.assertTrue(wt.is_dir())
        self.assertTrue((wt / "1.1.txt").exists())
        self.assertEqual(_git(wt, "rev-parse", "HEAD").stdout, head_before)
        _git(self.canonical, "rev-parse", "--verify", f"refs/heads/{_SPEC}/1.1")
        self.assertIn(str(wt), _git(self.canonical, "worktree", "list").stdout)
        self.assertEqual(journal.read_bytes(), journal_before)


class TestDiscoverRepos(SweepStaleWorktreesTestCase):
    def test_discover_repos_skips_worktrees_container(self):
        _add_worktree(self.canonical, "feature/x")
        found = ssw.discover_repos(self.tmp)
        self.assertIn(self.canonical.resolve(), [p.resolve() for p in found])
        names = [p.name for p in found]
        self.assertNotIn(f"{self.canonical.name}-worktrees", names)


if __name__ == "__main__":
    unittest.main()
