#!/usr/bin/env python3
"""Tests for quarantine_selfcheck.py. Run: python3 -m pytest test_quarantine_selfcheck.py -q"""
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from worktrail.router.quarantine_selfcheck import (
    _files_on_base,
    _group_files,
    _merged_pr_matching,
    check_repo,
    main,
    reconcile_finding,
    sweep,
)


def _journal(worktrees_dir: Path, spec_id: str, groups: dict) -> Path:
    worktrees_dir.mkdir(parents=True, exist_ok=True)
    path = worktrees_dir / f"run-{spec_id}.json"
    path.write_text(json.dumps({"groups": groups}), encoding="utf-8")
    return path


def _repo_with_worktrees(root: Path, name: str) -> Path:
    repo = root / name
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    return repo


def _write_runplan(repo: Path, spec_id: str, tasks: list) -> Path:
    runplans_dir = repo.parent / f"{repo.name}-worktrees" / "runplans"
    runplans_dir.mkdir(parents=True, exist_ok=True)
    path = runplans_dir / f"{spec_id}-abc123.json"
    path.write_text(json.dumps({"tasks": tasks}), encoding="utf-8")
    return path


_RUNPLAN_TASKS = [
    {"id": "1.1", "files": ["a.py"], "deps": []},
    {"id": "1.2", "files": ["b.py"], "deps": ["1.1"]},
]


_CLEAN_GROUPS = {"1.1": {"state": "MERGED", "pr_url": "https://example.com/pr/1"}}
_QUARANTINED_GROUPS = {
    "1.1": {"state": "MERGED", "pr_url": "https://example.com/pr/1"},
    "1.2": {"state": "QUARANTINED", "pr_url": "https://example.com/pr/2"},
}


class TestCheckRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_no_run_journals_yields_empty_findings(self):
        repo = _repo_with_worktrees(self.tmp, "myrepo")
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])

    def test_journal_without_quarantined_group_yields_empty_findings(self):
        repo = _repo_with_worktrees(self.tmp, "myrepo")
        worktrees_dir = self.tmp / "myrepo-worktrees"
        _journal(worktrees_dir, "some-spec", _CLEAN_GROUPS)
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])

    def test_journal_with_one_quarantined_group_yields_one_finding(self):
        repo = _repo_with_worktrees(self.tmp, "myrepo")
        worktrees_dir = self.tmp / "myrepo-worktrees"
        journal_path = _journal(worktrees_dir, "some-spec", _QUARANTINED_GROUPS)
        three_days_ago = time.time() - 3 * 86400.0
        os.utime(journal_path, (three_days_ago, three_days_ago))
        result = check_repo(repo)
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(finding["spec_id"], "some-spec")
        self.assertEqual(finding["group"], "1.2")
        self.assertEqual(finding["pr_url"], "https://example.com/pr/2")
        self.assertAlmostEqual(finding["age_days"], 3.0, places=1)


class TestGroupFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_no_runplan_cache_yields_none(self):
        repo = _repo_with_worktrees(self.tmp, "myrepo")
        self.assertIsNone(_group_files(repo, "some-spec", "feature-1"))

    def test_group_name_matches_yields_file_union(self):
        repo = _repo_with_worktrees(self.tmp, "myrepo")
        _write_runplan(repo, "some-spec", _RUNPLAN_TASKS)
        result = _group_files(repo, "some-spec", "feature-1")
        self.assertEqual(result, ["a.py", "b.py"])

    def test_group_name_not_found_yields_none(self):
        repo = _repo_with_worktrees(self.tmp, "myrepo")
        _write_runplan(repo, "some-spec", _RUNPLAN_TASKS)
        self.assertIsNone(_group_files(repo, "some-spec", "feature-99"))


def _git_repo(root: Path, name: str) -> Path:
    """A real git repo (not the fake `.git`-dir stand-in) with an initial commit
    on `files`, for tests that shell out to real `git ls-tree`."""
    repo = root / name
    repo.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    return repo


def _commit_files(repo: Path, files: dict) -> None:
    for rel_path, content in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "test commit"], cwd=repo, check=True)


class TestFilesOnBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_all_files_present_on_base(self):
        repo = _git_repo(self.tmp, "myrepo")
        _commit_files(repo, {"a.py": "1", "b.py": "2"})
        self.assertTrue(_files_on_base(repo, ["a.py", "b.py"], base="main"))

    def test_one_file_missing_from_base(self):
        repo = _git_repo(self.tmp, "myrepo")
        _commit_files(repo, {"a.py": "1"})
        self.assertFalse(_files_on_base(repo, ["a.py", "missing.py"], base="main"))

    def test_default_base_uses_current_branch(self):
        repo = _git_repo(self.tmp, "myrepo")
        _commit_files(repo, {"a.py": "1"})
        self.assertTrue(_files_on_base(repo, ["a.py"]))


class TestMergedPrMatching(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = _repo_with_worktrees(self.tmp, "myrepo")

    def _run(self, stdout="", returncode=0, raise_exc=None):
        if raise_exc:
            return patch("subprocess.run", side_effect=raise_exc)
        result = subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout)
        return patch("subprocess.run", return_value=result)

    def test_matching_merged_pr_returns_its_url(self):
        prs = [{"url": "https://example.com/pr/9",
                "files": [{"path": "a.py"}, {"path": "b.py"}, {"path": "c.py"}]}]
        with self._run(stdout=json.dumps(prs)):
            result = _merged_pr_matching(self.repo, ["a.py", "b.py"])
        self.assertEqual(result, "https://example.com/pr/9")

    def test_no_matching_pr_returns_none(self):
        prs = [{"url": "https://example.com/pr/9", "files": [{"path": "unrelated.py"}]}]
        with self._run(stdout=json.dumps(prs)):
            result = _merged_pr_matching(self.repo, ["a.py"])
        self.assertIsNone(result)

    def test_gh_nonzero_exit_returns_none(self):
        with self._run(returncode=1, stdout=""):
            self.assertIsNone(_merged_pr_matching(self.repo, ["a.py"]))

    def test_gh_missing_returns_none(self):
        with self._run(raise_exc=FileNotFoundError()):
            self.assertIsNone(_merged_pr_matching(self.repo, ["a.py"]))

    def test_gh_timeout_returns_none(self):
        with self._run(raise_exc=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            self.assertIsNone(_merged_pr_matching(self.repo, ["a.py"]))

    def test_gh_bad_json_returns_none(self):
        with self._run(stdout="not json"):
            self.assertIsNone(_merged_pr_matching(self.repo, ["a.py"]))


class TestReconcileFinding(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_no_runplan_cache_stays_unreconciled(self):
        repo = _git_repo(self.tmp, "myrepo")
        finding = {"spec_id": "some-spec", "group": "feature-1"}
        self.assertIsNone(reconcile_finding(repo, finding))

    def test_base_branch_files_present_reconciles(self):
        repo = _git_repo(self.tmp, "myrepo")
        _commit_files(repo, {"a.py": "1", "b.py": "2"})
        _write_runplan(repo, "some-spec", _RUNPLAN_TASKS)
        finding = {"spec_id": "some-spec", "group": "feature-1"}
        result = reconcile_finding(repo, finding)
        self.assertIsNotNone(result)
        self.assertEqual(result["method"], "base-branch-files")
        self.assertEqual(result["spec_id"], "some-spec")
        self.assertEqual(result["group"], "feature-1")
        self.assertEqual(result["evidence"], ["a.py", "b.py"])

    def _gh_only_side_effect(self, gh_result):
        """Real `git` calls pass through to the real subprocess.run (so
        `_files_on_base`'s own git ls-tree genuinely sees no commit / misses);
        only `gh` invocations get the rigged result -- a blanket mock would
        also intercept `_files_on_base`'s internal git calls."""
        real_run = subprocess.run

        def _side_effect(cmd, *args, **kwargs):
            if cmd and cmd[0] == "gh":
                return gh_result
            return real_run(cmd, *args, **kwargs)

        return _side_effect

    def test_merged_pr_files_reconciles_when_base_branch_missing(self):
        repo = _git_repo(self.tmp, "myrepo")
        # No commit -- files absent from base -- forces the PR-search fallback.
        _write_runplan(repo, "some-spec", _RUNPLAN_TASKS)
        finding = {"spec_id": "some-spec", "group": "feature-1"}
        prs = [{"url": "https://example.com/pr/9",
                "files": [{"path": "a.py"}, {"path": "b.py"}]}]
        gh_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(prs))
        with patch("subprocess.run", side_effect=self._gh_only_side_effect(gh_result)):
            reconciliation = reconcile_finding(repo, finding)
        self.assertIsNotNone(reconciliation)
        self.assertEqual(reconciliation["method"], "merged-pr-files")
        self.assertEqual(reconciliation["evidence"], "https://example.com/pr/9")

    def test_neither_signal_matches_stays_unreconciled(self):
        repo = _git_repo(self.tmp, "myrepo")
        _write_runplan(repo, "some-spec", _RUNPLAN_TASKS)
        finding = {"spec_id": "some-spec", "group": "feature-1"}
        gh_result = subprocess.CompletedProcess(args=[], returncode=1, stdout="")
        with patch("subprocess.run", side_effect=self._gh_only_side_effect(gh_result)):
            self.assertIsNone(reconcile_finding(repo, finding))


class TestCheckRepoReconciliation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_reconciled_group_excluded_from_findings(self):
        repo = _git_repo(self.tmp, "myrepo")
        _commit_files(repo, {"a.py": "1", "b.py": "2"})
        _write_runplan(repo, "quarantine-reconciliation", _RUNPLAN_TASKS)
        worktrees_dir = self.tmp / "myrepo-worktrees"
        groups = {"1.1": {"state": "MERGED", "pr_url": "https://example.com/pr/1"},
                  "feature-1": {"state": "QUARANTINED", "pr_url": ""}}
        _journal(worktrees_dir, "quarantine-reconciliation", groups)

        result = check_repo(repo)

        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["reconciled"]), 1)
        self.assertEqual(result["reconciled"][0]["group"], "feature-1")
        self.assertEqual(result["reconciled"][0]["method"], "base-branch-files")

    def test_unreconciled_group_stays_in_findings(self):
        repo = _repo_with_worktrees(self.tmp, "myrepo")
        worktrees_dir = self.tmp / "myrepo-worktrees"
        _journal(worktrees_dir, "some-spec", _QUARANTINED_GROUPS)
        # No RunPlan cache -- reconciliation is skipped, byte-identical to
        # pre-change behavior.
        result = check_repo(repo)
        self.assertEqual(len(result["findings"]), 1)
        self.assertEqual(result["findings"][0]["group"], "1.2")
        self.assertEqual(result["reconciled"], [])


class TestSweep(unittest.TestCase):
    def test_sweep_flags_only_repos_with_findings(self):
        tmp = Path(tempfile.mkdtemp())
        clean_repo = _repo_with_worktrees(tmp, "clean-repo")
        _journal(tmp / "clean-repo-worktrees", "spec-a", _CLEAN_GROUPS)
        flagged_repo = _repo_with_worktrees(tmp, "flagged-repo")
        _journal(tmp / "flagged-repo-worktrees", "spec-b", _QUARANTINED_GROUPS)
        _repo_with_worktrees(tmp, "no-worktrees-repo")

        results = sweep(tmp)

        flagged_names = {r["repo"] for r in results}
        self.assertEqual(flagged_names, {"flagged-repo"})
        self.assertNotIn(clean_repo.name, flagged_names)

    def test_sweep_skips_repos_without_git(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "not-a-repo").mkdir()
        results = sweep(tmp)
        self.assertEqual(results, [])


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_cli_exits_zero_when_clean(self):
        repo = _repo_with_worktrees(self.tmp, "clean-repo")
        _journal(self.tmp / "clean-repo-worktrees", "spec-a", _CLEAN_GROUPS)
        self.assertEqual(main(["--repo", str(repo)]), 0)

    def test_cli_exits_one_when_flagged(self):
        repo = _repo_with_worktrees(self.tmp, "flagged-repo")
        _journal(self.tmp / "flagged-repo-worktrees", "spec-b", _QUARANTINED_GROUPS)
        self.assertEqual(main(["--repo", str(repo)]), 1)

    def test_cli_json_output_shape(self):
        repo = _repo_with_worktrees(self.tmp, "flagged-repo")
        _journal(self.tmp / "flagged-repo-worktrees", "spec-b", _QUARANTINED_GROUPS)

        out = io.StringIO()
        with redirect_stdout(out):
            exit_code = main(["--repo", str(repo), "--json"])

        self.assertEqual(exit_code, 1)
        payload = json.loads(out.getvalue())
        self.assertIn("results", payload)
        self.assertEqual(payload["flagged"], 1)
        result = payload["results"][0]
        self.assertEqual(result["repo"], "flagged-repo")
        finding = result["findings"][0]
        self.assertEqual(finding["spec_id"], "spec-b")
        self.assertEqual(finding["group"], "1.2")
        self.assertIn("pr_url", finding)
        self.assertIn("age_days", finding)

    def test_cli_requires_a_target(self):
        with self.assertRaises(SystemExit):
            main([])


if __name__ == "__main__":
    unittest.main()
