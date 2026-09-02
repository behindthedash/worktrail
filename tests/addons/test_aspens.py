"""`AspensAddOn`: install/configure/run behavior against the machine-local
CLI-presence marker, `.aspens.json` init-once gate, and sync/commit output.
"""

from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from worktrail.addons import aspens as aspens_module
from worktrail.addons.aspens import AspensAddOn


class _MarkerIsolation(unittest.TestCase):
    """Redirects the machine-local install marker into a temp dir per test,
    so tests never read or write the real `~/.cache/worktrail/addons/aspens/`.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        cache_dir = Path(self._tmp.name) / "cache"
        marker = cache_dir / "last-check"
        self._cache_patch = patch.object(aspens_module, "CACHE_DIR", cache_dir)
        self._marker_patch = patch.object(aspens_module, "LAST_CHECK_MARKER", marker)
        self._cache_patch.start()
        self._marker_patch.start()
        self.addCleanup(self._cache_patch.stop)
        self.addCleanup(self._marker_patch.stop)
        self.marker = marker


class InstallTests(_MarkerIsolation):
    def test_install_skips_npm_call_when_marker_is_fresh(self):
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        self.marker.write_text(str(time.time()))

        with patch("worktrail.addons.aspens.subprocess.run") as mock_run:
            AspensAddOn().install(ctx=None)

        mock_run.assert_not_called()

    def test_install_runs_npm_call_when_marker_is_stale(self):
        self.marker.parent.mkdir(parents=True, exist_ok=True)
        stale = time.time() - (aspens_module.CHECK_INTERVAL_SECONDS + 60)
        self.marker.write_text(str(stale))

        with (
            patch("worktrail.addons.aspens.shutil.which", return_value=None),
            patch("worktrail.addons.aspens.subprocess.run") as mock_run,
        ):
            mock_run.return_value = SimpleNamespace(returncode=0)
            AspensAddOn().install(ctx=None)

        mock_run.assert_called_once()
        args = mock_run.call_args.args[0]
        self.assertEqual(args, ["npm", "install", "-g", "aspens"])

    def test_install_runs_npm_call_when_marker_is_missing(self):
        with (
            patch("worktrail.addons.aspens.shutil.which", return_value=None),
            patch("worktrail.addons.aspens.subprocess.run") as mock_run,
        ):
            mock_run.return_value = SimpleNamespace(returncode=0)
            AspensAddOn().install(ctx=None)

        mock_run.assert_called_once()

    def test_install_skips_npm_call_when_cli_already_on_path(self):
        """A present CLI (e.g. an operator's `npm link`ed fork) is never
        overwritten by a registry install; the marker is still refreshed."""
        with (
            patch(
                "worktrail.addons.aspens.shutil.which",
                return_value="/usr/local/bin/aspens",
            ),
            patch("worktrail.addons.aspens.subprocess.run") as mock_run,
        ):
            AspensAddOn().install(ctx=None)

        mock_run.assert_not_called()
        self.assertTrue(self.marker.exists())

    def test_install_touches_marker_after_checking(self):
        self.assertFalse(self.marker.exists())

        with patch("worktrail.addons.aspens.subprocess.run") as mock_run:
            mock_run.return_value = SimpleNamespace(returncode=0)
            AspensAddOn().install(ctx=None)

        self.assertTrue(self.marker.exists())


class ConfigureTests(_MarkerIsolation):
    def test_configure_is_a_no_op_when_aspens_json_exists(self):
        with tempfile.TemporaryDirectory() as t:
            worktree = Path(t)
            (worktree / ".aspens.json").write_text("{}")
            ctx = SimpleNamespace(worktree=worktree, config={})

            with patch("worktrail.addons.aspens.subprocess.run") as mock_run:
                AspensAddOn().configure(ctx)

            mock_run.assert_not_called()

    def test_configure_initializes_when_aspens_json_absent(self):
        with tempfile.TemporaryDirectory() as t:
            worktree = Path(t)
            ctx = SimpleNamespace(worktree=worktree, config={})

            with patch("worktrail.addons.aspens.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0)
                AspensAddOn().configure(ctx)

            mock_run.assert_called_once()
            args = mock_run.call_args.args[0]
            self.assertEqual(args[:3], ["aspens", "doc", "init"])

    def test_configure_never_installs_aspens_own_hook(self):
        with tempfile.TemporaryDirectory() as t:
            worktree = Path(t)
            ctx = SimpleNamespace(
                worktree=worktree, config={"target": "x", "backend": "y"}
            )

            with patch("worktrail.addons.aspens.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(returncode=0)
                AspensAddOn().configure(ctx)

            args = mock_run.call_args.args[0]
            self.assertNotIn("--install-hook", args)


class RunTests(_MarkerIsolation):
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

    def test_run_never_invokes_install_hook(self):
        with tempfile.TemporaryDirectory() as t:
            worktree = self._init_repo(Path(t))
            ctx = SimpleNamespace(worktree=worktree, config={}, timeout=30)
            real_run = subprocess.run

            def _fake_run(cmd, *args, **kwargs):
                if cmd[:2] == ["aspens", "doc"]:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return real_run(cmd, *args, **kwargs)

            with patch(
                "worktrail.addons.aspens.subprocess.run", side_effect=_fake_run
            ) as mock_run:
                AspensAddOn().run(ctx)

            sync_call = next(
                c for c in mock_run.call_args_list if c.args[0][:2] == ["aspens", "doc"]
            )
            self.assertNotIn("--install-hook", sync_call.args[0])

    def test_run_reports_changed_paths_for_a_successful_sync(self):
        with tempfile.TemporaryDirectory() as t:
            worktree = self._init_repo(Path(t))
            (worktree / "generated.md").write_text("synced content\n")
            ctx = SimpleNamespace(worktree=worktree, config={}, timeout=30)

            # `run()` shells out twice: once for `aspens doc sync` itself
            # (mocked, since the real CLI isn't installed in tests), and once
            # inside `_changed_paths` for `git status` (left real, since a
            # real untracked file is on disk to detect).
            real_run = subprocess.run

            def _fake_run(cmd, *args, **kwargs):
                if cmd[:2] == ["aspens", "doc"]:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return real_run(cmd, *args, **kwargs)

            with patch("worktrail.addons.aspens.subprocess.run", side_effect=_fake_run):
                result = AspensAddOn().run(ctx)

            self.assertTrue(result.changed)
            self.assertIn(Path("generated.md"), result.paths)

    def test_run_reports_no_change_when_sync_produces_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            worktree = self._init_repo(Path(t))
            ctx = SimpleNamespace(worktree=worktree, config={}, timeout=30)
            real_run = subprocess.run

            def _fake_run(cmd, *args, **kwargs):
                if cmd[:2] == ["aspens", "doc"]:
                    return SimpleNamespace(returncode=0, stdout="", stderr="")
                return real_run(cmd, *args, **kwargs)

            with patch("worktrail.addons.aspens.subprocess.run", side_effect=_fake_run):
                result = AspensAddOn().run(ctx)

            self.assertFalse(result.changed)
            self.assertEqual(result.paths, [])

    def test_run_raises_on_a_failing_sync(self):
        with tempfile.TemporaryDirectory() as t:
            worktree = self._init_repo(Path(t))
            ctx = SimpleNamespace(worktree=worktree, config={}, timeout=30)

            with patch("worktrail.addons.aspens.subprocess.run") as mock_run:
                mock_run.return_value = SimpleNamespace(
                    returncode=1, stdout="", stderr="sync failed: boom"
                )
                with self.assertRaises(RuntimeError) as cm:
                    AspensAddOn().run(ctx)

            self.assertIn("boom", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
