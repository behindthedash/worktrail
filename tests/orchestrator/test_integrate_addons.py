#!/usr/bin/env python3
"""Group-PR path wiring for add-ons (integrate.py's `integrate_one`):

  - an unconfigured repo's group integration produces no add-on commit
  - a configured add-on's output is committed before push
  - a `required` add-on failure quarantines the group

Real git repos are used for the merge/commit/push mechanics (cheap,
deterministic -- see `test_base_refresh.py`), with only `gh` subprocess
calls and the drift gate stubbed out, since neither is what this task
covers (3.1-3.3 cover the runner itself; 4.1/4.2 cover this wiring).

Run: python3 test_integrate_addons.py
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest.mock import patch

from worktrail.addons.base import AddOnResult
from worktrail.orchestrator import integrate

Proc = namedtuple("Proc", "returncode stdout stderr")


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _init_bare_and_clone(tmp: Path):
    """Create a bare 'origin' repo plus a local clone on branch 'main'."""
    origin = tmp / "origin.git"
    origin.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)

    local = tmp / "local"
    subprocess.run(["git", "clone", "-q", str(origin), str(local)], check=True, capture_output=True)
    _run(local, "config", "user.email", "t@t")
    _run(local, "config", "user.name", "T")
    (local / "README.md").write_text("init\n")
    _run(local, "add", "-A")
    _run(local, "commit", "-q", "-m", "init")
    _run(local, "branch", "-M", "main")
    _run(local, "push", "-q", "-u", "origin", "main")
    return origin, local


def _make_task_branch(repo: Path, branch: str, filename: str, content: str) -> None:
    """Branch off main and add one file, then return to main."""
    _run(repo, "checkout", "-q", "-B", branch, "main")
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    _run(repo, "add", "-A")
    _run(repo, "commit", "-q", "-m", f"impl {branch}")
    _run(repo, "checkout", "-q", "main")


_REAL_SUBPROCESS_RUN = subprocess.run


def _fake_gh(cmd, *args, **kwargs):
    """Stand in for `gh` calls issued directly via `subprocess.run`
    (pr view/list/create). `subprocess.run` is one shared module object, so
    patching it here also intercepts `live._git`'s real git calls -- pass
    those straight through to the real implementation and only fake `gh`."""
    if cmd[:3] == ["gh", "pr", "view"]:
        return Proc(1, "", "no pull requests found")
    if cmd[:3] == ["gh", "pr", "list"]:
        return Proc(0, "[]", "")
    if cmd[:3] == ["gh", "pr", "create"]:
        return Proc(0, "https://github.com/o/r/pull/1\n", "")
    if cmd and cmd[0] == "git":
        return _REAL_SUBPROCESS_RUN(cmd, *args, **kwargs)
    raise AssertionError(f"unexpected subprocess.run call: {cmd}")


class _NoopAddOn:
    name = "noop"

    def install(self, ctx):
        pass

    def configure(self, ctx):
        pass

    def run(self, ctx):
        return AddOnResult(changed=False, detail="nothing to do", paths=[])


class _WritingAddOn:
    """Writes a file and reports it for the runner to stage/commit."""

    name = "docsync"

    def install(self, ctx):
        pass

    def configure(self, ctx):
        pass

    def run(self, ctx):
        (Path(ctx.worktree) / "GENERATED.md").write_text("synced output\n")
        return AddOnResult(changed=True, detail="synced output", paths=[Path("GENERATED.md")])


class _FailingAddOn:
    name = "docsync"

    def install(self, ctx):
        pass

    def configure(self, ctx):
        pass

    def run(self, ctx):
        raise RuntimeError("boom")


class _IntegrateAddonsTestBase(unittest.TestCase):
    """Shared real-git fixture: one independent group ("base") with one
    deliverable task, integrated against a real bare 'origin' remote.

    `_run_drift_gate` is stubbed (it shells out to `pre_pr_gate.py`, which
    is orthogonal to add-on wiring) and `gh` calls are faked so no network
    access is required.
    """

    def setUp(self):
        self._tmp_ctx = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp_ctx.name)
        self.origin, self.repo = _init_bare_and_clone(self.tmp)
        self.spec_id = "testspec"
        _make_task_branch(self.repo, f"{self.spec_id}/task-001", "src/a.txt", "a\n")

        self.g = {"name": "base", "tasks": ["TASK-001"], "reqs": [], "depends_on": []}
        self.tasks = [{"id": "TASK-001", "status": "done", "kind": "impl", "deps": []}]
        self.status = {"TASK-001": "done"}
        self.group_branch: dict = {}
        self.quarantined: dict = {}

        self._drift_patch = patch.object(integrate, "_run_drift_gate", return_value=(True, "ok"))
        self._drift_patch.start()
        self._gh_patch = patch("worktrail.orchestrator.integrate.subprocess.run", side_effect=_fake_gh)
        self._gh_patch.start()

    def tearDown(self):
        self._gh_patch.stop()
        self._drift_patch.stop()
        self._tmp_ctx.cleanup()

    def _integrate(self, policy):
        return integrate.integrate_one(
            self.g, self.repo, self.spec_id, self.tasks, "origin", "run1", "main",
            None, self.status, self.group_branch, self.quarantined,
            policy=policy,
        )

    def _pushed_branch_log(self, branch: str) -> str:
        """Commit subjects reachable on `branch` at `origin`, oldest first."""
        _run(self.repo, "fetch", "-q", "origin", branch)
        log = _run(self.repo, "log", "--format=%s", f"origin/{branch}")
        return log.stdout


class UnconfiguredRepoNoAddonCommit(_IntegrateAddonsTestBase):
    def test_no_policy_produces_no_addon_commit(self):
        result = self._integrate(policy=None)

        self.assertIsNotNone(result, "an unconfigured repo must still produce a PR")
        self.assertNotIn("base", self.quarantined)
        subjects = self._pushed_branch_log("run1/base")
        self.assertNotIn("chore(", subjects, "no add-on ran, so no chore(...) commit is expected")

    def test_empty_add_ons_policy_produces_no_addon_commit(self):
        result = self._integrate(policy={"add_ons": {}})

        self.assertIsNotNone(result)
        self.assertNotIn("base", self.quarantined)
        subjects = self._pushed_branch_log("run1/base")
        self.assertNotIn("chore(", subjects)

    def test_disabled_add_on_produces_no_commit(self):
        with patch("worktrail.addons.runner.addon_for", return_value=_NoopAddOn()):
            result = self._integrate(policy={"add_ons": {"noop": {"enabled": False}}})

        self.assertIsNotNone(result)
        self.assertNotIn("base", self.quarantined)
        subjects = self._pushed_branch_log("run1/base")
        self.assertNotIn("chore(", subjects)


class ConfiguredAddonCommittedBeforePush(_IntegrateAddonsTestBase):
    def test_addon_output_reaches_pushed_branch(self):
        with patch("worktrail.addons.runner.addon_for", return_value=_WritingAddOn()):
            result = self._integrate(policy={"add_ons": {"docsync": {}}})

        self.assertIsNotNone(result, "a successful non-required add-on must not block the PR")
        self.assertNotIn("base", self.quarantined)

        subjects = self._pushed_branch_log("run1/base")
        self.assertIn(
            "chore(docsync): synced output", subjects,
            "add-on output must be committed and land on the pushed branch, i.e. "
            "before the push -- a commit made only after push would never appear here",
        )


class RequiredAddonFailureQuarantinesGroup(_IntegrateAddonsTestBase):
    def test_required_failure_quarantines_and_blocks_push(self):
        with patch("worktrail.addons.runner.addon_for", return_value=_FailingAddOn()):
            result = self._integrate(policy={"add_ons": {"docsync": {"required": True}}})

        self.assertIsNone(result, "a required add-on failure must not produce a PR")
        self.assertIn("base", self.quarantined)
        self.assertIn("required add-on failed", self.quarantined["base"])
        self.assertIn("boom", self.quarantined["base"])

        # The group branch must never have reached the remote.
        ls_remote = subprocess.run(
            ["git", "ls-remote", str(self.origin), "run1/base"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(ls_remote.stdout.strip(), "", "quarantined group must not be pushed")

    def test_non_required_failure_does_not_quarantine(self):
        with patch("worktrail.addons.runner.addon_for", return_value=_FailingAddOn()):
            result = self._integrate(policy={"add_ons": {"docsync": {}}})

        self.assertIsNotNone(result, "a non-required add-on failure must not block the PR")
        self.assertNotIn("base", self.quarantined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
