#!/usr/bin/env python3
"""Tests for branch_selfcheck.py. Run: python3 -m pytest tests/router/test_branch_selfcheck.py -q"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router.branch_selfcheck import (
    PROTECTED_BRANCHES,
    check_repo,
    merge_method,
    sweep,
)


def _run_git(args: list, cwd: Path) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True
    )


def _init_repo(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    _run_git(["init", "-q", "-b", "main"], repo)
    _run_git(["config", "user.email", "test@example.com"], repo)
    _run_git(["config", "user.name", "Test"], repo)
    _run_git(["commit", "--allow-empty", "-m", "init"], repo)
    return repo


def _branch_with_commit(repo: Path, branch: str, filename: str) -> None:
    _run_git(["checkout", "-b", branch, "main"], repo)
    (repo / filename).write_text("content\n", encoding="utf-8")
    _run_git(["add", filename], repo)
    _run_git(["commit", "-m", f"{branch} work"], repo)
    _run_git(["checkout", "main"], repo)


def _gh_only_side_effect(gh_result):
    """Real `git` calls pass through; only `gh` invocations get the rigged
    result -- mirrors test_quarantine_selfcheck.py's own pattern."""
    real_run = subprocess.run

    def _side_effect(cmd, *args, **kwargs):
        if cmd and cmd[0] == "gh":
            return gh_result
        return real_run(cmd, *args, **kwargs)

    return _side_effect


def _no_merged_pr():
    return patch(
        "subprocess.run",
        side_effect=_gh_only_side_effect(
            subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        ),
    )


def _gh_merged_for_branches(merged_branches: set):
    """A `gh` stub whose merged-PR answer depends on the `--head <branch>`
    queried, so a test can prove a branch is found merged ONLY through a
    specific tier (e.g. the indirect check querying a different branch than
    the one under test) rather than any gh call short-circuiting true."""
    real_run = subprocess.run

    def _side_effect(cmd, *args, **kwargs):
        if cmd and cmd[0] == "gh":
            head = cmd[cmd.index("--head") + 1] if "--head" in cmd else None
            prs = [{"number": 1}] if head in merged_branches else []
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=json.dumps(prs)
            )
        return real_run(cmd, *args, **kwargs)

    return patch("subprocess.run", side_effect=_side_effect)


class TestMergeMethod(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_regular_merge_is_ancestry(self):
        repo = _init_repo(self.tmp, "r1")
        _branch_with_commit(repo, "topic", "topic.txt")
        _run_git(["merge", "--no-ff", "topic", "-m", "merge topic"], repo)
        with _no_merged_pr():
            self.assertEqual(merge_method("topic", repo), "ancestry")

    def test_single_commit_squash_merge_is_cherry(self):
        repo = _init_repo(self.tmp, "r2")
        _branch_with_commit(repo, "topic", "topic.txt")
        _run_git(["merge", "--squash", "topic"], repo)
        _run_git(["commit", "-m", "squash merge topic"], repo)
        with _no_merged_pr():
            self.assertEqual(merge_method("topic", repo), "cherry")

    def test_multi_commit_squash_merge_needs_gh_fallback(self):
        repo = _init_repo(self.tmp, "r3")
        _run_git(["checkout", "-b", "topic", "main"], repo)
        (repo / "a.txt").write_text("1\n", encoding="utf-8")
        _run_git(["add", "a.txt"], repo)
        _run_git(["commit", "-m", "work 1"], repo)
        (repo / "a.txt").write_text("1\n2\n", encoding="utf-8")
        _run_git(["add", "a.txt"], repo)
        _run_git(["commit", "-m", "work 2"], repo)
        _run_git(["checkout", "main"], repo)
        _run_git(["merge", "--squash", "topic"], repo)
        _run_git(["commit", "-m", "squash merge topic"], repo)
        # git cherry alone can't see this (per-commit patch-ids don't match
        # the single squash commit) -- must fall through to gh.
        with _no_merged_pr():
            self.assertIsNone(merge_method("topic", repo))
        merged_pr = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=json.dumps([{"number": 9}])
        )
        with patch("subprocess.run", side_effect=_gh_only_side_effect(merged_pr)):
            self.assertEqual(merge_method("topic", repo), "merged-pr")

    def test_unmerged_branch_returns_none(self):
        repo = _init_repo(self.tmp, "r4")
        _branch_with_commit(repo, "topic", "topic.txt")
        with _no_merged_pr():
            self.assertIsNone(merge_method("topic", repo))

    def test_nonexistent_branch_returns_none(self):
        repo = _init_repo(self.tmp, "r5")
        with _no_merged_pr():
            self.assertIsNone(merge_method("ghost", repo))

    def test_indirect_merge_via_ancestor_intermediate_branch(self):
        """A task branch merges (real merge, ancestry preserved) into a group
        branch that itself lands via a SQUASH merge into main -- the squash
        breaks direct ancestry for everything below the group, and the
        group's extra commit means the task's own single-commit diff isn't
        cherry-equivalent to the squash commit either. The task branch never
        had its own PR (only the group did), so only the one-hop indirect
        check -- which asks whether the GROUP branch is merged -- finds it."""
        repo = _init_repo(self.tmp, "r6")
        _branch_with_commit(repo, "spec/task-1", "task1.txt")
        _run_git(["checkout", "-b", "spec/group", "main"], repo)
        _run_git(
            ["merge", "--no-ff", "spec/task-1", "-m", "merge task into group"], repo
        )
        (repo / "group-extra.txt").write_text("extra\n", encoding="utf-8")
        _run_git(["add", "group-extra.txt"], repo)
        _run_git(["commit", "-m", "group's own extra commit"], repo)
        _run_git(["checkout", "main"], repo)
        _run_git(["merge", "--squash", "spec/group"], repo)
        _run_git(["commit", "-m", "squash merge group"], repo)
        # gh reports the GROUP branch merged, but not the task branch itself
        # (it never had its own PR) -- isolates tier 4 from tier 3.
        with _gh_merged_for_branches({"spec/group"}):
            self.assertEqual(merge_method("spec/task-1", repo), "merged-indirectly")

    def test_gh_missing_fails_closed(self):
        repo = _init_repo(self.tmp, "r7")
        _run_git(["checkout", "-b", "topic", "main"], repo)
        (repo / "a.txt").write_text("1\n", encoding="utf-8")
        _run_git(["add", "a.txt"], repo)
        _run_git(["commit", "-m", "w1"], repo)
        (repo / "a.txt").write_text("1\n2\n", encoding="utf-8")
        _run_git(["add", "a.txt"], repo)
        _run_git(["commit", "-m", "w2"], repo)
        _run_git(["checkout", "main"], repo)
        _run_git(["merge", "--squash", "topic"], repo)
        _run_git(["commit", "-m", "squash merge topic"], repo)
        with patch(
            "subprocess.run",
            side_effect=_gh_only_side_effect(
                subprocess.CompletedProcess(args=[], returncode=127, stdout="")
            ),
        ):
            self.assertIsNone(merge_method("topic", repo))


class TestCheckRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_merged_branch_with_no_worktree_is_prunable(self):
        repo = _init_repo(self.tmp, "r1")
        _branch_with_commit(repo, "topic", "topic.txt")
        _run_git(["merge", "--no-ff", "topic", "-m", "merge topic"], repo)
        with _no_merged_pr():
            result = check_repo(repo)
        self.assertEqual(len(result["prunable"]), 1)
        entry = result["prunable"][0]
        self.assertEqual(entry["branch"], "topic")
        self.assertIsNone(entry["worktree_path"])
        self.assertEqual(entry["method"], "ancestry")

    def test_unmerged_branch_not_prunable(self):
        repo = _init_repo(self.tmp, "r2")
        _branch_with_commit(repo, "topic", "topic.txt")
        with _no_merged_pr():
            result = check_repo(repo)
        self.assertEqual(result["prunable"], [])

    def test_base_branch_never_prunable(self):
        repo = _init_repo(self.tmp, "r3")
        # main is trivially "merged into itself" by ancestry but must never
        # be a candidate at all -- it's excluded by name before classification.
        with _no_merged_pr():
            result = check_repo(repo)
        names = [f["branch"] for f in result["prunable"]]
        self.assertNotIn("main", names)

    def test_protected_branch_name_excluded_even_if_merged(self):
        repo = _init_repo(self.tmp, "r4")
        _branch_with_commit(repo, "dev", "dev.txt")
        _run_git(["merge", "--no-ff", "dev", "-m", "merge dev"], repo)
        with _no_merged_pr():
            result = check_repo(repo)
        names = [f["branch"] for f in result["prunable"]]
        self.assertNotIn("dev", names)
        self.assertIn("dev", PROTECTED_BRANCHES)

    def test_merged_branch_checked_out_in_clean_worktree_is_prunable(self):
        repo = _init_repo(self.tmp, "r5")
        _branch_with_commit(repo, "topic", "topic.txt")
        _run_git(["merge", "--no-ff", "topic", "-m", "merge topic"], repo)
        wt = self.tmp / "r5-worktrees" / "topic"
        _run_git(["worktree", "add", str(wt), "topic"], repo)
        with _no_merged_pr():
            result = check_repo(repo)
        entry = next(f for f in result["prunable"] if f["branch"] == "topic")
        self.assertEqual(entry["worktree_path"], str(wt))

    def test_merged_branch_checked_out_in_dirty_worktree_excluded(self):
        repo = _init_repo(self.tmp, "r6")
        _branch_with_commit(repo, "topic", "topic.txt")
        _run_git(["merge", "--no-ff", "topic", "-m", "merge topic"], repo)
        wt = self.tmp / "r6-worktrees" / "topic"
        _run_git(["worktree", "add", str(wt), "topic"], repo)
        (wt / "uncommitted.txt").write_text("wip\n", encoding="utf-8")
        with _no_merged_pr():
            result = check_repo(repo)
        names = [f["branch"] for f in result["prunable"]]
        self.assertNotIn("topic", names)

    def test_canonical_checkouts_current_branch_never_prunable(self):
        repo = _init_repo(self.tmp, "r7")
        _branch_with_commit(repo, "topic", "topic.txt")
        _run_git(["merge", "--no-ff", "topic", "-m", "merge topic"], repo)
        _run_git(["checkout", "topic"], repo)  # canonical checkout itself
        with _no_merged_pr():
            result = check_repo(repo)
        names = [f["branch"] for f in result["prunable"]]
        self.assertNotIn("topic", names)

    def test_non_git_directory_returns_empty(self):
        plain = self.tmp / "not-a-repo"
        plain.mkdir()
        result = check_repo(plain)
        self.assertEqual(result["prunable"], [])


class TestSweep(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_only_repos_with_prunable_branches_included(self):
        clean_repo = _init_repo(self.tmp, "clean-repo")
        _branch_with_commit(clean_repo, "topic", "topic.txt")  # unmerged

        dirty_repo = _init_repo(self.tmp, "dirty-repo")
        _branch_with_commit(dirty_repo, "topic", "topic.txt")
        _run_git(["merge", "--no-ff", "topic", "-m", "merge topic"], dirty_repo)

        with _no_merged_pr():
            results = sweep(self.tmp)
        names = [r["repo"] for r in results]
        self.assertIn("dirty-repo", names)
        self.assertNotIn("clean-repo", names)


if __name__ == "__main__":
    unittest.main()
