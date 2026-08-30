"""Real-git tests for `worktrail.addons.runner.run_addons`.

Mirrors the real-git fixture pattern in
`tests/orchestrator/test_integrate_extras.py::SyntheticFanoutTests` -- the
stage-and-commit sequence under test (`git add` -> `git diff --cached
--quiet` -> `git commit`) is only meaningfully exercised against a real
repo, not a mocked one.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.addons.base import AddOnResult
from worktrail.addons.runner import AddOnFailure, run_addons


class _FakeAddOn:
    """A minimal `AddOn` whose `run()` behavior is set by the test."""

    def __init__(self, name: str, run_fn):
        self.name = name
        self._run_fn = run_fn

    def install(self, ctx) -> None:
        pass

    def configure(self, ctx) -> None:
        pass

    def run(self, ctx):
        return self._run_fn(ctx)


class RunAddonsTests(unittest.TestCase):
    def _init_repo(self, root: Path) -> Path:
        repo = root / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "t@t"], check=True
        )
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
        (repo / "README.md").write_text("init\n")
        subprocess.run(
            ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
            check=True,
            capture_output=True,
        )
        return repo

    def _head_sha(self, repo: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def _last_commit_message(self, repo: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%s"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def test_changed_files_committed_with_expected_message_prefix(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            before = self._head_sha(repo)

            def _run(ctx):
                (ctx.worktree / "generated.txt").write_text("synced output\n")
                return AddOnResult(
                    changed=True,
                    detail="synced 1 file",
                    paths=[ctx.worktree / "generated.txt"],
                )

            addon = _FakeAddOn("aspens", _run)
            policy = {"add_ons": {"aspens": {}}}

            with patch("worktrail.addons.runner.addon_for", return_value=addon):
                logs = run_addons(repo, repo, policy)

            self.assertNotEqual(before, self._head_sha(repo))
            self.assertEqual(
                self._last_commit_message(repo), "chore(aspens): synced 1 file"
            )
            self.assertEqual(len(logs), 1)
            log = logs[0]
            self.assertEqual(log.name, "aspens")
            self.assertTrue(log.changed)
            self.assertTrue(log.committed)
            self.assertTrue(log.ok)

    def test_no_op_run_produces_no_commit(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            before = self._head_sha(repo)

            def _run(ctx):
                return AddOnResult(changed=False, detail="up to date", paths=[])

            addon = _FakeAddOn("aspens", _run)
            policy = {"add_ons": {"aspens": {}}}

            with patch("worktrail.addons.runner.addon_for", return_value=addon):
                logs = run_addons(repo, repo, policy)

            self.assertEqual(before, self._head_sha(repo))
            self.assertEqual(len(logs), 1)
            log = logs[0]
            self.assertFalse(log.changed)
            self.assertFalse(log.committed)
            self.assertTrue(log.ok)

    def test_non_fatal_failure_is_swallowed_and_logged(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            before = self._head_sha(repo)

            def _run(ctx):
                raise RuntimeError("boom")

            addon = _FakeAddOn("aspens", _run)
            # No `required` key -- defaults to non-fatal per design D4.
            policy = {"add_ons": {"aspens": {}}}

            with (
                patch("worktrail.addons.runner.addon_for", return_value=addon),
                patch("builtins.print") as mock_print,
            ):
                logs = run_addons(repo, repo, policy)

            self.assertEqual(before, self._head_sha(repo))
            self.assertEqual(len(logs), 1)
            log = logs[0]
            self.assertEqual(log.name, "aspens")
            self.assertFalse(log.changed)
            self.assertFalse(log.committed)
            self.assertFalse(log.ok)
            self.assertIn("RuntimeError: boom", log.detail)
            printed = " ".join(str(c.args[0]) for c in mock_print.call_args_list)
            self.assertIn("aspens", printed)
            self.assertIn("non-fatal", printed)

    def test_required_failure_propagates(self):
        with tempfile.TemporaryDirectory() as t:
            repo = self._init_repo(Path(t))
            before = self._head_sha(repo)

            def _run(ctx):
                raise RuntimeError("boom")

            addon = _FakeAddOn("aspens", _run)
            policy = {"add_ons": {"aspens": {"required": True}}}

            with (
                patch("worktrail.addons.runner.addon_for", return_value=addon),
                self.assertRaises(AddOnFailure) as cm,
            ):
                run_addons(repo, repo, policy)

            self.assertEqual(cm.exception.name, "aspens")
            self.assertIn("boom", cm.exception.detail)
            # A required failure must not leave a partial commit behind.
            self.assertEqual(before, self._head_sha(repo))


if __name__ == "__main__":
    unittest.main()
