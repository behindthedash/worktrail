#!/usr/bin/env python3
"""Unit tests for the `sdd-workflow` conductor target-repo resolver (stdlib unittest)."""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router import resolve_repo as rr


@contextlib.contextmanager
def _isolated_tmp():
    """Yield a temp dir with is_git_repo bounded to only paths within it.

    On some systems /tmp itself contains a stray .git directory, which causes
    find_repo_root to detect a repo root for every temp path. This context manager
    patches is_git_repo so it only returns True for .git entries created inside
    the temp dir, isolating tests from ambient filesystem pollution.
    """
    with tempfile.TemporaryDirectory() as t:
        root = Path(t).resolve()
        orig = rr.is_git_repo

        def _bounded(path: Path) -> bool:
            p = Path(path).resolve()
            if not str(p).startswith(str(root)):
                return False
            return orig(path)

        with patch.object(rr, "is_git_repo", _bounded):
            yield root


def _mkrepo(parent: Path, name: str) -> Path:
    d = parent / name
    (d / ".git").mkdir(parents=True)
    return d


def _mkplain(parent: Path, name: str) -> Path:
    d = parent / name
    d.mkdir(parents=True)
    return d


class DetectionTests(unittest.TestCase):
    def test_is_git_repo_dir_and_file(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            r = _mkrepo(root, "repo-dir")
            self.assertTrue(rr.is_git_repo(r))
            # worktree case: .git is a FILE
            wt = root / "repo-file"
            wt.mkdir()
            (wt / ".git").write_text("gitdir: /somewhere\n")
            self.assertTrue(rr.is_git_repo(wt))
            self.assertFalse(rr.is_git_repo(_mkplain(root, "plain")))

    def test_find_repo_root_from_nested(self):
        with tempfile.TemporaryDirectory() as t:
            repo = _mkrepo(Path(t), "app")
            nested = repo / "src" / "deep"
            nested.mkdir(parents=True)
            self.assertEqual(rr.find_repo_root(nested), repo.resolve())

    def test_find_repo_root_none(self):
        with _isolated_tmp() as root:
            self.assertIsNone(rr.find_repo_root(_mkplain(root, "nope")))

    def test_find_repo_root_worktree_resolves_to_canonical(self):
        """A worktree `.git` file pointing to <canonical>/.git/worktrees/<name>
        should resolve to the canonical repo root, not the worktree path."""
        with _isolated_tmp() as root:
            # Create canonical repo: root/myproject/.git/
            canonical = _mkrepo(root, "myproject")
            wt_gitdir = canonical / ".git" / "worktrees" / "feat"
            wt_gitdir.mkdir(parents=True)
            # Create worktree dir: root/myproject-worktrees/feat/.git (file)
            wt_root = root / "myproject-worktrees" / "feat"
            wt_root.mkdir(parents=True)
            wt_dot_git = wt_root / ".git"
            wt_dot_git.write_text(f"gitdir: {wt_gitdir}\n")
            # Sub-dir inside worktree
            nested = wt_root / "src" / "lib"
            nested.mkdir(parents=True)
            result = rr.find_repo_root(nested)
            self.assertEqual(result, canonical.resolve())


class HintTests(unittest.TestCase):
    def test_initials(self):
        self.assertEqual(rr.initials("gracefully-giving-back"), "ggb")
        self.assertEqual(rr.initials("my_cool_app"), "mca")

    def test_derive_acronym(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            ggb = _mkrepo(root, "gracefully-giving-back")
            _mkrepo(root, "other-project")
            cands = rr.list_candidate_repos(root)
            self.assertEqual(rr.derive_from_hint(cands, "let's GO on GGB"), [ggb])

    def test_derive_substring(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            pay = _mkrepo(root, "payments-service")
            _mkrepo(root, "notifications")
            cands = rr.list_candidate_repos(root)
            self.assertEqual(rr.derive_from_hint(cands, "work on payments"), [pay])

    def test_derive_ambiguous_returns_multiple(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mkrepo(root, "api-gateway")
            _mkrepo(root, "api-worker")
            cands = rr.list_candidate_repos(root)
            self.assertEqual(len(rr.derive_from_hint(cands, "the api")), 2)

    def test_empty_hint_no_match(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mkrepo(root, "app")
            self.assertEqual(rr.derive_from_hint(rr.list_candidate_repos(root), ""), [])

    def test_stopwords_never_substring_match(self):
        """'fix the upload timeout' must not derive `behindthedash` via 'the'."""
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            _mkrepo(root, "behindthedash")
            _mkrepo(root, "other-project")
            cands = rr.list_candidate_repos(root)
            self.assertEqual(rr.derive_from_hint(cands, "fix the upload timeout"), [])


class ResolveTests(unittest.TestCase):
    def test_in_repo(self):
        with tempfile.TemporaryDirectory() as t:
            repo = _mkrepo(Path(t), "app")
            sub = repo / "pkg"
            sub.mkdir()
            res = rr.resolve(sub)
            self.assertEqual(res["mode"], "in-repo")
            self.assertEqual(res["repo"], str(repo.resolve()))

    def test_single_candidate(self):
        with _isolated_tmp() as root:
            repo = _mkrepo(root, "only-app")
            res = rr.resolve(root)
            self.assertEqual(res["mode"], "single-candidate")
            self.assertEqual(res["repo"], str(repo.resolve()))

    def test_derived_with_hint(self):
        with _isolated_tmp() as root:
            ggb = _mkrepo(root, "gracefully-giving-back")
            _mkrepo(root, "side-project")
            res = rr.resolve(root, "deploy GGB please")
            self.assertEqual(res["mode"], "derived")
            self.assertEqual(res["repo"], str(ggb.resolve()))

    def test_ambiguous_no_hint(self):
        with _isolated_tmp() as root:
            _mkrepo(root, "a-repo")
            _mkrepo(root, "b-repo")
            res = rr.resolve(root)
            self.assertEqual(res["mode"], "ambiguous")
            self.assertIsNone(res["repo"])
            self.assertEqual(len(res["candidates"]), 2)

    def test_ambiguous_hint_misses_falls_back_to_all(self):
        with _isolated_tmp() as root:
            _mkrepo(root, "alpha")
            _mkrepo(root, "beta")
            res = rr.resolve(root, "something unrelated zzz")
            self.assertEqual(res["mode"], "ambiguous")
            self.assertEqual(len(res["candidates"]), 2)

    def test_none(self):
        with _isolated_tmp() as root:
            _mkplain(root, "just-a-folder")
            res = rr.resolve(root)
            self.assertEqual(res["mode"], "none")
            self.assertIsNone(res["repo"])

    def test_workspace_root_prefers_projects_over_home_git(self):
        with _isolated_tmp() as root:
            (root / "REPOS.md").write_text("# Workspace\n")
            projects = root / "projects"
            projects.mkdir()
            alpha = _mkrepo(projects, "alpha")
            _mkrepo(projects, "beta")
            (root / ".git").mkdir()
            res = rr.resolve(root)
            self.assertEqual(res["mode"], "ambiguous")
            self.assertIsNone(res["repo"])
            self.assertIn(str(alpha.resolve()), res["candidates"])

    def test_in_repo_wins_inside_workspace_layout(self):
        """cwd inside a repo under ~/projects must resolve in-repo, not bounce
        to the workspace-root ambiguous picker (the workspace walk used to
        shadow in-repo detection on the standard layout)."""
        with _isolated_tmp() as root:
            (root / "REPOS.md").write_text("# Workspace\n")
            (root / ".git").mkdir()  # home dir itself is a git repo
            projects = root / "projects"
            projects.mkdir()
            alpha = _mkrepo(projects, "alpha")
            _mkrepo(projects, "beta")
            sub = alpha / "src"
            sub.mkdir()
            res = rr.resolve(sub)
            self.assertEqual(res["mode"], "in-repo")
            self.assertEqual(res["repo"], str(alpha.resolve()))

    def test_workspace_root_hint_derives_repo(self):
        with _isolated_tmp() as root:
            (root / "REPOS.md").write_text("# Workspace\n")
            projects = root / "projects"
            projects.mkdir()
            ggb = _mkrepo(projects, "gracefully-giving-back")
            _mkrepo(projects, "developer-kit")
            (root / ".git").mkdir()
            res = rr.resolve(root, "work on ggb")
            self.assertEqual(res["mode"], "derived")
            self.assertEqual(res["repo"], str(ggb.resolve()))


class MainCLITests(unittest.TestCase):
    def test_cli_in_repo_mode(self):
        with tempfile.TemporaryDirectory() as t:
            repo = _mkrepo(Path(t), "app")
            buf = io.StringIO()
            import sys

            old = sys.stdout
            sys.stdout = buf
            try:
                rr.main(["--start", str(repo / "src")])
            finally:
                sys.stdout = old
            self.assertIn("in-repo", buf.getvalue())

    def test_cli_json_flag(self):
        with _isolated_tmp() as root:
            _mkrepo(root, "myapp")
            buf = io.StringIO()
            import sys

            old = sys.stdout
            sys.stdout = buf
            try:
                rr.main(["--start", str(root), "--json"])
            finally:
                sys.stdout = old
            data = __import__("json").loads(buf.getvalue())
            self.assertIn("mode", data)

    def test_cli_ambiguous_mode(self):
        with _isolated_tmp() as root:
            _mkrepo(root, "repo-a")
            _mkrepo(root, "repo-b")
            buf = io.StringIO()
            import sys

            old = sys.stdout
            sys.stdout = buf
            try:
                rr.main(["--start", str(root)])
            finally:
                sys.stdout = old
            self.assertIn("ambiguous", buf.getvalue())

    def test_cli_none_mode(self):
        with _isolated_tmp() as root:
            _mkplain(root, "not-a-repo")
            buf = io.StringIO()
            import sys

            old = sys.stdout
            sys.stdout = buf
            try:
                rr.main(["--start", str(root)])
            finally:
                sys.stdout = old
            self.assertIn("none", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
