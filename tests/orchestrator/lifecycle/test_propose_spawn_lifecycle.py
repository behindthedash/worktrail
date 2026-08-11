#!/usr/bin/env python3
"""Lifecycle harness: prove `worktrail-skill-dispatch`'s propose spawn (PR
#264) actually lands `openspec/changes/<id>/` on disk, and that a return
code of 0 alone is NOT proof of that -- the silent no-op both failure modes
in PR #264 share.

`tests/router/test_skill_dispatch.py` mocks `subprocess.run` and only checks
argv construction. This harness runs the REAL `subprocess.run` against a
fake `claude`/`codex`/`opencode` binary on PATH (`fake_propose_agent.py`) so
`--cwd` targeting, `--write` permission wiring, and process-cwd propagation
all execute for real, then asserts on the filesystem -- not the exit code.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from worktrail.router import skill_dispatch

_HERE = Path(__file__).resolve().parent
_FAKE_AGENT = _HERE / "fake_propose_agent.py"

SUPPORTED_AGENTS = ("claude", "codex", "opencode")


def _install_fake_agents(bin_dir: Path) -> None:
    bin_dir.mkdir(exist_ok=True)
    for agent in SUPPORTED_AGENTS:
        shim = bin_dir / agent
        shim.write_text(f"#!/bin/sh\nexec {sys.executable} {_FAKE_AGENT} {agent} \"$@\"\n")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)


class ProposeSpawnLandsChangeDirTests(unittest.TestCase):
    """Regression guard for PR #264: `--cwd`/`--write` argv actually reaches
    the child and actually lets it write, for every supported agent."""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.wt = tmp / "wt"
        self.wt.mkdir()
        bin_dir = tmp / "bin"
        _install_fake_agents(bin_dir)
        self._old_path = os.environ["PATH"]
        os.environ["PATH"] = f"{bin_dir}:{self._old_path}"
        self.addCleanup(lambda: os.environ.__setitem__("PATH", self._old_path))
        self._old_change_id = os.environ.get("FAKE_PROPOSE_CHANGE_ID")
        os.environ["FAKE_PROPOSE_CHANGE_ID"] = "probe-change"
        self.addCleanup(self._restore_change_id)
        self._old_codex_home = os.environ.get("WORKTRAIL_CODEX_HOME")
        os.environ["WORKTRAIL_CODEX_HOME"] = str(tmp / "codex-home")
        self.addCleanup(self._restore_codex_home)

    def _restore_change_id(self):
        if self._old_change_id is None:
            os.environ.pop("FAKE_PROPOSE_CHANGE_ID", None)
        else:
            os.environ["FAKE_PROPOSE_CHANGE_ID"] = self._old_change_id

    def _restore_codex_home(self):
        if self._old_codex_home is None:
            os.environ.pop("WORKTRAIL_CODEX_HOME", None)
        else:
            os.environ["WORKTRAIL_CODEX_HOME"] = self._old_codex_home

    def _change_dir(self) -> Path:
        return self.wt / "openspec" / "changes" / "probe-change"

    def test_each_agent_lands_the_full_change_dir(self):
        for agent in SUPPORTED_AGENTS:
            with self.subTest(agent=agent):
                arguments = [
                    "--agent", agent, "--skill", "openspec-propose",
                    "--args", "probe", "--cwd", str(self.wt), "--write",
                ]
                if agent == "codex":
                    arguments.append("--no-inherit-codex-auth")
                result = skill_dispatch.main(arguments)
                self.assertEqual(result, 0)
                change_dir = self._change_dir()
                self.assertTrue((change_dir / "proposal.md").exists())
                self.assertTrue((change_dir / "design.md").exists())
                self.assertTrue((change_dir / "tasks.md").exists())
                for f in change_dir.iterdir():
                    f.unlink()
                change_dir.rmdir()

    def test_without_write_claude_and_opencode_exit_0_and_write_nothing(self):
        """Pins the exact PR #264 failure shape: a return code of 0 is not
        proof of success -- callers MUST assert the change dir exists."""
        for agent in ("claude", "opencode"):
            with self.subTest(agent=agent):
                result = skill_dispatch.main([
                    "--agent", agent, "--skill", "openspec-propose",
                    "--args", "probe", "--cwd", str(self.wt),
                ])
                self.assertEqual(result, 0)
                self.assertFalse(self._change_dir().exists())

    def test_codex_needs_no_write_flag_because_it_always_has_workspace_write(self):
        result = skill_dispatch.main([
            "--agent", "codex", "--skill", "openspec-propose",
            "--args", "probe", "--cwd", str(self.wt), "--no-inherit-codex-auth",
        ])
        self.assertEqual(result, 0)
        self.assertTrue((self._change_dir() / "proposal.md").exists())


@unittest.skipUnless(
    os.environ.get("WORKTRAIL_REAL_CODEX_AUTH_TEST") == "1",
    "set WORKTRAIL_REAL_CODEX_AUTH_TEST=1 for the authenticated Codex lifecycle probe",
)
class RealAuthenticatedCodexLifecycleTests(unittest.TestCase):
    def test_inherited_chatgpt_session_writes_scoped_lifecycle_roots(self):
        parent_home = skill_dispatch.resolve_parent_codex_home()
        status = subprocess.run(
            ["codex", "login", "status"],
            env={**os.environ, "CODEX_HOME": str(parent_home)},
            capture_output=True,
            text=True,
            check=False,
        )
        if not skill_dispatch._is_chatgpt_login_status(status):
            self.skipTest("parent Codex is not authenticated with ChatGPT")
        if not (parent_home / "auth.json").is_file():
            self.skipTest("ChatGPT login is not using file-backed auth.json")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cwd = root / "repo"
            runs = root / "runs"
            worktrees = root / "repo-worktrees"
            skills = root / "skills"
            child_home = root / "child-home"
            for directory in (cwd, runs, worktrees):
                directory.mkdir()
            probe = skills / "authenticated-lifecycle-probe"
            probe.mkdir(parents=True)
            (probe / "SKILL.md").write_text(
                "---\nname: authenticated-lifecycle-probe\n"
                "description: Write two exact lifecycle proof files.\n---\n"
                f"Write the exact text `ok` to `{runs / 'run-record-proof'}` and "
                f"`{worktrees / 'worktree-proof'}` using shell commands, then stop.\n"
            )
            with mock.patch.dict(
                os.environ,
                {"WORKTRAIL_SKILL_ROOT": str(skills), "CODEX_HOME": str(parent_home)},
                clear=False,
            ):
                result = skill_dispatch.main([
                    "--agent", "codex",
                    "--skill", "authenticated-lifecycle-probe",
                    "--cwd", str(cwd),
                    "--codex-home", str(child_home),
                    "--add-dir", str(runs),
                    "--add-dir", str(worktrees),
                    "--write",
                ])
            self.assertEqual(result, 0)
            self.assertEqual((runs / "run-record-proof").read_text(), "ok")
            self.assertEqual((worktrees / "worktree-proof").read_text(), "ok")


if __name__ == "__main__":
    unittest.main()
