#!/usr/bin/env python3
"""Unit tests for preflight.py (stdlib unittest, mirrors test_pre_pr_gate.py style)."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router.preflight import (
    check, main, marker_path, read_marker, tree_state, write_marker,
)


class _GitRepoCase(unittest.TestCase):
    def _git(self, repo: str, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=True)

    def _init_repo(self, policy_yaml: str = 'pre_pr_cmd: "true"\n') -> str:
        d = tempfile.mkdtemp(prefix="preflight-")
        self._git(d, "init", "-q", "-b", "main")
        self._git(d, "config", "user.email", "test@example.com")
        self._git(d, "config", "user.name", "Test")
        spec = Path(d) / "docs" / "specs"
        spec.mkdir(parents=True)
        (spec / "go-policy.yaml").write_text(policy_yaml, encoding="utf-8")
        self._git(d, "add", ".")
        self._git(d, "commit", "-q", "-m", "base")
        return d

    def _write(self, repo: str, relpath: str, content: str = "x\n") -> None:
        path = Path(repo) / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


class TestTreeState(_GitRepoCase):
    def test_clean_tree_is_stable(self) -> None:
        repo = self._init_repo()
        self.assertEqual(tree_state(Path(repo)), tree_state(Path(repo)))

    def test_dirty_tree_changes_state(self) -> None:
        repo = self._init_repo()
        before = tree_state(Path(repo))
        self._write(repo, "dirty.txt")
        after = tree_state(Path(repo))
        self.assertNotEqual(before, after)

    def test_new_commit_changes_state(self) -> None:
        repo = self._init_repo()
        before = tree_state(Path(repo))
        self._write(repo, "committed.txt")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "add file")
        after = tree_state(Path(repo))
        self.assertNotEqual(before, after)

    def test_non_git_dir_returns_none(self) -> None:
        d = tempfile.mkdtemp(prefix="notgit-")
        self.assertIsNone(tree_state(Path(d)))


class TestMarkerRoundtrip(_GitRepoCase):
    def test_write_then_read_marker(self) -> None:
        repo = self._init_repo()
        state = tree_state(Path(repo))
        path = write_marker(Path(repo), state, "pytest -q")
        self.assertTrue(path.exists())
        marker = read_marker(Path(repo))
        self.assertEqual(marker["state"], state)
        self.assertEqual(marker["cmd"], "pytest -q")

    def test_marker_lives_in_private_git_dir(self) -> None:
        repo = self._init_repo()
        path = marker_path(Path(repo))
        self.assertEqual(path.parent, Path(repo) / ".git")

    def test_no_marker_returns_none(self) -> None:
        repo = self._init_repo()
        self.assertIsNone(read_marker(Path(repo)))

    def test_malformed_marker_returns_none(self) -> None:
        repo = self._init_repo()
        marker_path(Path(repo)).write_text("not json", encoding="utf-8")
        self.assertIsNone(read_marker(Path(repo)))


class TestCheckVerdict(_GitRepoCase):
    def test_unconfigured_repo_allows(self) -> None:
        repo = self._init_repo("base_branch: main\n")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_explicit_skip_allows(self) -> None:
        repo = self._init_repo("pre_pr_cmd: skip\n")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_docs_only_diff_allows(self) -> None:
        repo = self._init_repo(
            'pre_pr_cmd: "exit 9"\ndocs_only_paths:\n  - docs/**\n'
        )
        self._git(repo, "checkout", "-q", "-b", "feature")
        self._write(repo, "docs/new-note.md")
        self._git(repo, "add", ".")
        self._git(repo, "commit", "-q", "-m", "docs change")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_no_marker_and_configured_cmd_denies(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")
        self.assertIn("worktrail-preflight run", verdict["reason"])

    def test_matching_marker_allows(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        state = tree_state(Path(repo))
        write_marker(Path(repo), state, "true")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "allow")

    def test_stale_marker_denies(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        write_marker(Path(repo), "stale-state-from-before-a-commit", "true")
        verdict = check(Path(repo))
        self.assertEqual(verdict["decision"], "deny")

    def test_nonexistent_repo_denies(self) -> None:
        verdict = check(Path("/nonexistent/path/xyz"))
        self.assertEqual(verdict["decision"], "deny")


class TestCheckCli(_GitRepoCase):
    def test_check_cli_allow_exits_zero_and_prints_json(self) -> None:
        repo = self._init_repo("pre_pr_cmd: skip\n")
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["check", "--repo", repo])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["decision"], "allow")

    def test_check_cli_deny_exits_nonzero(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["check", "--repo", repo])
        self.assertEqual(code, 1)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["decision"], "deny")


class TestRunCli(_GitRepoCase):
    def test_run_records_marker_on_pass(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "true"\n')
        self.assertIsNone(read_marker(Path(repo)))
        code = main(["run", "--repo", repo])
        self.assertEqual(code, 0)
        marker = read_marker(Path(repo))
        self.assertIsNotNone(marker)
        self.assertEqual(marker["state"], tree_state(Path(repo)))
        # A subsequent check() now allows, proving the marker round-trips
        # through the exact same tree_state() the check path reads.
        self.assertEqual(check(Path(repo))["decision"], "allow")

    def test_run_does_not_record_marker_on_failure(self) -> None:
        repo = self._init_repo('pre_pr_cmd: "exit 3"\n')
        code = main(["run", "--repo", repo])
        self.assertEqual(code, 3)
        self.assertIsNone(read_marker(Path(repo)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
