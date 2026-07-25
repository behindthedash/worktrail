#!/usr/bin/env python3
"""Unit tests for the drift-sweep repo discovery filter (stdlib unittest)."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router import spec_sync_sweep_discovery as ssd


def _git_init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, capture_output=True, text=True, check=True)


def _make_repo_with_specs(root: Path, name: str) -> Path:
    repo = root / name
    _git_init(repo)
    (repo / "docs" / "specs").mkdir(parents=True)
    return repo


def _make_repo_without_specs(root: Path, name: str) -> Path:
    repo = root / name
    _git_init(repo)
    return repo


def _make_non_git_dir_with_specs(root: Path, name: str) -> Path:
    d = root / name
    (d / "docs" / "specs").mkdir(parents=True)
    return d


class TestDiscoverReposWithSpecs(unittest.TestCase):
    def test_git_repo_with_docs_specs_is_included(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            repo = _make_repo_with_specs(root, "repo-with-specs")
            result = ssd.discover_repos_with_specs(root)
            self.assertIn(repo, result)

    def test_git_repo_without_docs_specs_is_excluded_no_error(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            repo = _make_repo_without_specs(root, "repo-without-specs")
            result = ssd.discover_repos_with_specs(root)
            self.assertNotIn(repo, result)

    def test_non_git_dir_with_docs_specs_is_excluded(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            d = _make_non_git_dir_with_specs(root, "not-a-repo")
            result = ssd.discover_repos_with_specs(root)
            self.assertNotIn(d, result)

    def test_new_qualifying_repo_appears_on_second_call(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            first = ssd.discover_repos_with_specs(root)
            self.assertEqual(first, [])
            repo = _make_repo_with_specs(root, "late-arrival")
            second = ssd.discover_repos_with_specs(root)
            self.assertIn(repo, second)

    def test_empty_repos_root_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(ssd.discover_repos_with_specs(Path(t)), [])

    def test_nonexistent_repos_root_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as t:
            missing = Path(t) / "does-not-exist"
            self.assertEqual(ssd.discover_repos_with_specs(missing), [])


if __name__ == "__main__":
    unittest.main()
