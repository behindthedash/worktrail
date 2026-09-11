"""codex_sandbox_args(): default root set, de-dup/order, env escape hatches.

HOME, WORKTRAIL_HOME, and WORK_QUEUE_DIR are pinned per test so the operator
machine's real state dirs never leak into the emitted roots.
"""

import subprocess

import pytest

from worktrail.shared import codex_sandbox
from worktrail.shared.codex_sandbox import codex_sandbox_args, git_common_dir


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WORKTRAIL_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("WORK_QUEUE_DIR", str(tmp_path / "queue"))
    monkeypatch.delenv(codex_sandbox.SANDBOX_MODE_ENV, raising=False)
    monkeypatch.delenv(codex_sandbox.EXTRA_ROOTS_ENV, raising=False)


def _add_dirs(args):
    return [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]


def _git(*argv, cwd):
    subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-q", cwd=root)
    _git(
        "-c",
        "user.email=t@t",
        "-c",
        "user.name=t",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        "init",
        cwd=root,
    )
    return root


class TestDefaultRoots:
    def test_plain_dir(self, tmp_path):
        cwd = tmp_path / "plain"
        cwd.mkdir()
        args = codex_sandbox_args(cwd)
        assert args[:4] == [
            "-s",
            "workspace-write",
            "-c",
            "sandbox_workspace_write.network_access=true",
        ]
        assert "danger-full-access" not in args
        assert _add_dirs(args) == [
            str(cwd),
            str(tmp_path / "state"),
            str(tmp_path / "queue"),
        ]

    def test_git_common_dir_none_for_plain_dir(self, tmp_path):
        assert git_common_dir(tmp_path) is None

    def test_linked_worktree_gets_common_dir(self, tmp_path, repo):
        wt = tmp_path / "repo-worktrees" / "feat"
        _git("worktree", "add", "-q", "-b", "feat", str(wt), cwd=repo)
        assert git_common_dir(wt) == repo / ".git"
        roots = _add_dirs(codex_sandbox_args(wt))
        assert roots[:2] == [str(wt), str(repo / ".git")]
        assert str(repo) not in roots

    def test_repo_sibling_worktrees_root(self, tmp_path, repo):
        roots = _add_dirs(codex_sandbox_args(repo, repo=repo))
        assert str(tmp_path / "repo-worktrees") in roots
        assert roots.index(str(repo / ".git")) < roots.index(
            str(tmp_path / "repo-worktrees")
        )

    def test_nonexistent_roots_still_emitted(self, tmp_path):
        assert str(tmp_path / "state") in _add_dirs(codex_sandbox_args(tmp_path))


class TestExtrasAndDedup:
    def test_extras_merged_after_defaults(self, tmp_path):
        roots = _add_dirs(codex_sandbox_args(tmp_path, extra_roots=[tmp_path / "x"]))
        assert roots[-1] == str(tmp_path / "x")

    def test_env_extra_roots_appended(self, tmp_path, monkeypatch):
        monkeypatch.setenv(
            codex_sandbox.EXTRA_ROOTS_ENV, f"{tmp_path / 'a'}:{tmp_path / 'b'}"
        )
        roots = _add_dirs(codex_sandbox_args(tmp_path, extra_roots=[tmp_path / "x"]))
        assert roots[-3:] == [
            str(tmp_path / "x"),
            str(tmp_path / "a"),
            str(tmp_path / "b"),
        ]

    def test_dedup_stable_order(self, tmp_path, monkeypatch):
        monkeypatch.setenv(codex_sandbox.EXTRA_ROOTS_ENV, str(tmp_path / "queue"))
        roots = _add_dirs(
            codex_sandbox_args(tmp_path, extra_roots=[tmp_path, tmp_path / "state"])
        )
        assert roots == [
            str(tmp_path),
            str(tmp_path / "state"),
            str(tmp_path / "queue"),
        ]
        assert codex_sandbox_args(tmp_path) == codex_sandbox_args(tmp_path)


class TestSandboxModeEnv:
    def test_danger_full_access_prints_notice(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(codex_sandbox.SANDBOX_MODE_ENV, "danger-full-access")
        assert codex_sandbox_args(tmp_path) == ["-s", "danger-full-access"]
        assert (
            "codex sandbox: danger-full-access (WORKTRAIL_CODEX_SANDBOX_MODE override)"
            in capsys.readouterr().err
        )

    def test_workspace_write_explicit_is_silent(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(codex_sandbox.SANDBOX_MODE_ENV, "workspace-write")
        assert codex_sandbox_args(tmp_path)[:2] == ["-s", "workspace-write"]
        assert capsys.readouterr().err == ""

    def test_unknown_mode_raises_naming_variable(self, tmp_path, monkeypatch):
        monkeypatch.setenv(codex_sandbox.SANDBOX_MODE_ENV, "read-only")
        with pytest.raises(ValueError, match="WORKTRAIL_CODEX_SANDBOX_MODE"):
            codex_sandbox_args(tmp_path)


class TestHomeNeverEmitted:
    def test_home_not_a_root(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        monkeypatch.delenv("WORKTRAIL_HOME")
        monkeypatch.delenv("WORK_QUEUE_DIR")
        roots = _add_dirs(
            codex_sandbox_args(tmp_path / "plain", repo=tmp_path / "repo")
        )
        assert str(home) not in roots
        assert str(home / ".worktrail") in roots
        assert str(home / "work-queue") in roots
