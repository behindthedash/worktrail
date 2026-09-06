"""`worktree_guard_hook`: a spawned worker is denied writes outside its own
worktree, and `spawnlib` injects the hook into every claude spawn via
`--settings` (the source `--setting-sources project,local` keeps)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import spawnlib, worktree_guard_hook
from worktrail.runtime.selection import Cell


def _payload(tool, cwd, **tool_input):
    return {"tool_name": tool, "cwd": cwd, "tool_input": tool_input}


class DecideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.wt = os.path.realpath(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_inside_worktree_allowed(self):
        for target in (f"{self.wt}/src/x.py", "src/x.py", self.wt):
            self.assertIsNone(
                worktree_guard_hook.decide(
                    _payload("Write", self.wt, file_path=target)
                ),
                target,
            )

    def test_write_outside_worktree_denied(self):
        outside = os.path.realpath(tempfile.gettempdir())
        for tool, key in (
            ("Write", "file_path"),
            ("Edit", "file_path"),
            ("NotebookEdit", "notebook_path"),
        ):
            d = worktree_guard_hook.decide(
                _payload(tool, self.wt, **{key: f"{outside}/other/x.py"})
            )
            self.assertIsNotNone(d, tool)
            out = d["hookSpecificOutput"]
            self.assertEqual(out["permissionDecision"], "deny")
            self.assertIn(self.wt, out["permissionDecisionReason"])

    def test_sibling_prefix_is_not_inside(self):
        # /tmp/wt-other must not pass as "inside /tmp/wt"
        d = worktree_guard_hook.decide(
            _payload("Write", self.wt, file_path=f"{self.wt}-other/x.py")
        )
        self.assertIsNotNone(d)

    def test_bash_naming_canonical_checkout_denied(self):
        with patch.object(
            worktree_guard_hook, "_canonical_root", return_value="/repo/canon"
        ):
            d = worktree_guard_hook.decide(
                _payload("Bash", self.wt, command="sed -i s/a/b/ /repo/canon/src/x.py")
            )
            self.assertIsNotNone(d)
            self.assertIn(
                "/repo/canon", d["hookSpecificOutput"]["permissionDecisionReason"]
            )
            self.assertIsNone(
                worktree_guard_hook.decide(
                    _payload("Bash", self.wt, command="pytest -q tests")
                )
            )

    def test_bash_allowed_when_not_a_linked_worktree(self):
        with patch.object(worktree_guard_hook, "_canonical_root", return_value=None):
            self.assertIsNone(
                worktree_guard_hook.decide(
                    _payload("Bash", self.wt, command="rm -rf /anything")
                )
            )

    def test_unguarded_tool_and_missing_cwd_allowed(self):
        self.assertIsNone(
            worktree_guard_hook.decide(
                _payload("Read", self.wt, file_path="/etc/passwd")
            )
        )
        self.assertIsNone(
            worktree_guard_hook.decide(
                {"tool_name": "Write", "tool_input": {"file_path": "/x"}}
            )
        )

    def test_canonical_root_of_linked_worktree(self):
        repo = Path(self.wt) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-q", "--allow-empty", "-m", "init"],
            check=True,
            env={
                **os.environ,
                "GIT_AUTHOR_NAME": "t",
                "GIT_AUTHOR_EMAIL": "t@t",
                "GIT_COMMITTER_NAME": "t",
                "GIT_COMMITTER_EMAIL": "t@t",
            },
        )
        wt = Path(self.wt) / "repo-worktrees" / "b"
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", str(wt), "-b", "b"],
            check=True,
        )
        self.assertEqual(
            worktree_guard_hook._canonical_root(str(wt)), os.path.realpath(str(repo))
        )
        self.assertIsNone(worktree_guard_hook._canonical_root(str(repo)))

    def test_main_fails_open_on_garbage_and_prints_deny_json(self):
        r = subprocess.run(
            [sys.executable, "-m", "worktrail.orchestrator.worktree_guard_hook"],
            input="not json",
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(worktree_guard_hook.__file__).parents[2]),
            },
        )
        self.assertEqual((r.returncode, r.stdout), (0, ""))
        r = subprocess.run(
            [sys.executable, "-m", "worktrail.orchestrator.worktree_guard_hook"],
            input=json.dumps(
                _payload("Write", self.wt, file_path="/definitely/elsewhere.py")
            ),
            capture_output=True,
            text=True,
            check=False,
            env={
                **os.environ,
                "PYTHONPATH": str(Path(worktree_guard_hook.__file__).parents[2]),
            },
        )
        self.assertEqual(r.returncode, 0)
        self.assertEqual(
            json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"], "deny"
        )


class SpawnInjectionTests(unittest.TestCase):
    def _cmd(self, extra=None):
        return spawnlib.build_cmd(
            "hi",
            Cell(
                harness="claude",
                model=None,
                effort=None,
                pool="subscription",
                target="claude-sub",
            ),
            extra_args=extra,
        )

    def test_claude_spawn_carries_guard_settings(self):
        cmd = self._cmd()
        idx = cmd.index("--settings")
        settings = json.loads(cmd[idx + 1])
        hook = settings["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], worktree_guard_hook.HOOK_MATCHER)
        self.assertIn(
            "worktrail.orchestrator.worktree_guard_hook", hook["hooks"][0]["command"]
        )
        self.assertIn("--setting-sources", cmd)

    def test_explicit_settings_respected(self):
        cmd = self._cmd(["--settings", "/my/settings.json"])
        self.assertEqual(cmd.count("--settings"), 1)
        self.assertEqual(cmd[cmd.index("--settings") + 1], "/my/settings.json")

    def test_non_claude_spawn_untouched(self):
        cmd = spawnlib.build_cmd(
            "hi",
            Cell(
                harness="codex",
                model=None,
                effort=None,
                pool="subscription",
                target="codex-sub",
            ),
        )
        self.assertNotIn("--settings", cmd)


if __name__ == "__main__":
    unittest.main()
