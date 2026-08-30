#!/usr/bin/env python3
"""Unit tests for the repo-resolution staleness guard (stdlib unittest).

Exercises real throwaway git repos rather than mocking subprocess -- the
logic under test *is* the git plumbing (fetch, rev-list --left-right), so a
fake would just re-assert the mock.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.router import check_repo_freshness as crf


def _git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    )


def _init_repo(branch: str = "main") -> str:
    d = tempfile.mkdtemp(prefix="repo-freshness-")
    _git(d, "init", "-q", "-b", branch)
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    (Path(d) / "README.md").write_text("base\n", encoding="utf-8")
    _git(d, "add", ".")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _clone(remote: str, branch: str = "main") -> str:
    d = tempfile.mkdtemp(prefix="repo-freshness-clone-")
    subprocess.run(
        ["git", "clone", "-q", "-b", branch, remote, d],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(d, "config", "user.email", "test@example.com")
    _git(d, "config", "user.name", "Test")
    return d


class TestNotAGitRepo(unittest.TestCase):
    def test_non_repo_is_unchecked(self):
        with tempfile.TemporaryDirectory() as t:
            res = crf.check(Path(t))
            self.assertFalse(res["checked"])
            self.assertFalse(res["stale"])
            self.assertIsNone(res["warning"])


class TestDetachedHead(unittest.TestCase):
    def test_detached_head_is_unchecked_with_warning(self):
        remote = _init_repo()
        clone = _clone(remote)
        _git(clone, "checkout", "-q", "--detach", "HEAD")
        res = crf.check(Path(clone), do_fetch=False)
        self.assertFalse(res["checked"])
        self.assertIn("detached HEAD", str(res["warning"]))


class TestFreshAndBehind(unittest.TestCase):
    def test_up_to_date_clone_is_fresh(self):
        remote = _init_repo()
        clone = _clone(remote)
        res = crf.check(Path(clone))
        self.assertTrue(res["checked"])
        self.assertFalse(res["stale"])
        self.assertEqual(res["behind"], 0)
        self.assertEqual(res["branch"], "main")

    def test_clone_behind_new_upstream_commit_is_stale_after_fetch(self):
        remote = _init_repo()
        clone = _clone(remote)
        # Someone else pushes a new commit directly to the "remote" -- this
        # is exactly the kudera-consulting incident: the clone's checkout
        # never learns about it until something fetches.
        (Path(remote) / "docs-specs-go-policy.yaml").write_text(
            "pre_pr_cmd: true\n", encoding="utf-8"
        )
        _git(remote, "add", ".")
        _git(remote, "commit", "-q", "-m", "add policy upstream")

        res = crf.check(Path(clone), do_fetch=True)
        self.assertTrue(res["checked"])
        self.assertTrue(res["stale"])
        self.assertEqual(res["behind"], 1)
        self.assertEqual(res["ahead"], 0)
        self.assertIn("git pull", str(res["warning"]))

    def test_no_fetch_skips_network_and_uses_cached_ref(self):
        remote = _init_repo()
        clone = _clone(remote)
        (Path(remote) / "new-file.txt").write_text("x\n", encoding="utf-8")
        _git(remote, "add", ".")
        _git(remote, "commit", "-q", "-m", "new commit upstream")

        # Without fetching, the clone's cached origin/main ref is still the
        # old commit, so HEAD and origin/main match and it reports fresh --
        # this is the documented tradeoff of --no-fetch, not a bug.
        res = crf.check(Path(clone), do_fetch=False)
        self.assertTrue(res["checked"])
        self.assertFalse(res["stale"])

        res_fetched = crf.check(Path(clone), do_fetch=True)
        self.assertTrue(res_fetched["stale"])
        self.assertEqual(res_fetched["behind"], 1)

    def test_local_commit_ahead_of_remote_is_not_stale(self):
        remote = _init_repo()
        clone = _clone(remote)
        (Path(clone) / "local-work.txt").write_text("wip\n", encoding="utf-8")
        _git(clone, "add", ".")
        _git(clone, "commit", "-q", "-m", "local wip commit")

        res = crf.check(Path(clone))
        self.assertTrue(res["checked"])
        self.assertFalse(res["stale"])
        self.assertEqual(res["ahead"], 1)
        self.assertEqual(res["behind"], 0)

    def test_unknown_remote_branch_is_unchecked(self):
        remote = _init_repo()
        clone = _clone(remote)
        res = crf.check(Path(clone), branch="does-not-exist", do_fetch=False)
        self.assertFalse(res["checked"])
        self.assertIn("freshness unknown", str(res["warning"]))


class TestCli(unittest.TestCase):
    def test_json_output_matches_check_result(self):
        import io
        import json
        from contextlib import redirect_stdout

        remote = _init_repo()
        clone = _clone(remote)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = crf.main(["--repo", clone, "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(buf.getvalue()), crf.check(Path(clone)))

    def test_no_fetch_flag_is_wired(self):
        remote = _init_repo()
        clone = _clone(remote)
        (Path(remote) / "new-file.txt").write_text("x\n", encoding="utf-8")
        _git(remote, "add", ".")
        _git(remote, "commit", "-q", "-m", "new commit upstream")

        import io
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = crf.main(["--repo", clone, "--no-fetch", "--json"])
        self.assertEqual(rc, 0)
        self.assertIn('"stale": false', buf.getvalue())


if __name__ == "__main__":
    unittest.main()
