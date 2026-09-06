#!/usr/bin/env python3
"""Integration regression for the openspec-propose kill-and-resume path (PR #455).

test_check_openspec_propose_resume.py unit-tests `check_openspec_propose_resume.check()`
in isolation by writing artifacts directly. This spawns a real subprocess
standing in for the headless openspec-propose child, kills it mid-authoring
so partial `openspec/changes/<id>/` artifacts land on disk the way an actual
OOM/host-disconnect kill would, then exercises the installed
`worktrail-check-openspec-propose-resume` CLI end-to-end and confirms its
output correctly signals `/opsx:update` instead of `/opsx:propose` -- the
routing decision `subagent-prompts.md`'s `#openspec-propose` procedure
depends on to avoid OpenSpec's own change-name-collision guardrail.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from ._subprocess_timeouts import subprocess_timeout_s

_FAKE_CHILD = Path(__file__).with_name("fake_openspec_propose_child.py")
_CLI = shutil.which("worktrail-check-openspec-propose-resume")


class KillMidAuthoringResumesViaUpdateTests(unittest.TestCase):
    def test_killed_child_leaves_partial_artifacts_and_cli_routes_to_update(self):
        self.assertIsNotNone(
            _CLI, "worktrail-check-openspec-propose-resume must be installed"
        )
        with tempfile.TemporaryDirectory() as t:
            worktree = Path(t)
            change_id = "kill-and-resume-regression"
            change_dir = worktree / "openspec" / "changes" / change_id
            ready = worktree / "ready"

            child = subprocess.Popen(
                [sys.executable, str(_FAKE_CHILD), str(change_dir), str(ready)]
            )
            try:
                deadline = time.monotonic() + subprocess_timeout_s()
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                self.assertTrue(ready.exists(), "fake child never signaled readiness")
                # SIGKILL -- same as an OOM kill, no graceful shutdown -- while
                # the child is still "mid-authoring" design.md/tasks.md. This
                # is the exact shape of the kill PR #455 fixed resume for.
                child.kill()
                child.wait(timeout=subprocess_timeout_s())
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=subprocess_timeout_s())

            self.assertTrue((change_dir / "proposal.md").is_file())
            self.assertFalse((change_dir / "design.md").exists())
            self.assertFalse((change_dir / "tasks.md").exists())

            payload = json.loads(
                subprocess.run(
                    [
                        _CLI,
                        "--worktree",
                        str(worktree),
                        "--change-id",
                        change_id,
                        "--json",
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
            )
            self.assertTrue(payload["checked"])
            self.assertTrue(payload["resumable"])
            self.assertEqual(payload["present"], ["proposal.md"])
            self.assertEqual(sorted(payload["missing"]), ["design.md", "tasks.md"])

            human = subprocess.run(
                [_CLI, "--worktree", str(worktree), "--change-id", change_id],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("RESUMABLE", human)
            self.assertIn("dispatch /opsx:update, not /opsx:propose", human)
            self.assertNotIn("safe to run a fresh openspec-propose", human)


class UnkilledFreshChangeStillRoutesToProposeTests(unittest.TestCase):
    def test_no_child_ever_ran_routes_to_fresh_propose(self):
        # Control case: no spawn ever touched this change-id, so the CLI must
        # not mistake "worktree exists" for "resumable" -- /opsx:propose stays
        # the correct dispatch and must not be redirected to a phantom resume.
        self.assertIsNotNone(
            _CLI, "worktrail-check-openspec-propose-resume must be installed"
        )
        with tempfile.TemporaryDirectory() as t:
            worktree = Path(t)
            change_id = "never-started"
            human = subprocess.run(
                [_CLI, "--worktree", str(worktree), "--change-id", change_id],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertIn("safe to run a fresh openspec-propose", human)
            self.assertNotIn("dispatch /opsx:update", human)


if __name__ == "__main__":
    unittest.main()
