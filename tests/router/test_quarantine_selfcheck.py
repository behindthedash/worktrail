#!/usr/bin/env python3
"""Tests for quarantine_selfcheck.py. Run: python3 -m pytest test_quarantine_selfcheck.py -q"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from worktrail.router.quarantine_selfcheck import check_repo, main, sweep


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
        _journal(worktrees_dir, "some-spec", _QUARANTINED_GROUPS)
        result = check_repo(repo)
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(finding["spec_id"], "some-spec")
        self.assertEqual(finding["group"], "1.2")
        self.assertEqual(finding["pr_url"], "https://example.com/pr/2")
        self.assertIsInstance(finding["age_days"], float)
        self.assertGreaterEqual(finding["age_days"], 0.0)


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
