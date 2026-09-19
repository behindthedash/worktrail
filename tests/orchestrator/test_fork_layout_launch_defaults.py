#!/usr/bin/env python3
"""`worktrail-live full-real` resolves its own remote/bootstrap from the repo.

Both flags used to be sourced from policy by the *calling agent*
(`skills/worktrail-go/references/subagent-prompts.md`'s documented
`worktrail-detach launch` block), which passed neither -- so `full-real` fell
back to a hard-coded `origin` and no bootstrap command at all.

On a fork layout whose `origin` is the read-only upstream (aspens:
origin=aspenkit/aspens, remote.pushDefault=fork=behindthedash/aspens) that
combination broke run go-20260918-190550 outright: the base refresh compared
against the upstream's `main`, task worktrees forked from a ref with no
`tasks.md`, 6 tasks crashed with `WorktreeMissingTaskFileError` within ~10s,
and the base group's smoke died on `vitest: not found` because nothing had
installed node_modules. Brief 20260918-213220 member 2.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import live


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


class DefaultRemoteResolutionTests(unittest.TestCase):
    def _repo(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="fork-layout-"))
        _git(d.parent, "init", "-q", "-b", "main", str(d))
        return d

    def test_unset_push_default_resolves_origin(self):
        self.assertEqual(live._default_remote(self._repo()), "origin")

    def test_push_default_wins_over_origin(self):
        repo = self._repo()
        _git(repo, "config", "remote.pushDefault", "fork")
        self.assertEqual(live._default_remote(repo), "fork")

    def test_empty_push_default_resolves_origin(self):
        repo = self._repo()
        _git(repo, "config", "remote.pushDefault", "")
        self.assertEqual(live._default_remote(repo), "origin")

    def test_non_git_path_resolves_origin(self):
        self.assertEqual(live._default_remote(Path("/nonexistent/repo/path")), "origin")


class DefaultBootstrapCmdResolutionTests(unittest.TestCase):
    def _repo(self, policy_yaml: str | None) -> Path:
        d = Path(tempfile.mkdtemp(prefix="bootstrap-default-"))
        if policy_yaml is not None:
            pol = d / ".worktrail"
            pol.mkdir(parents=True)
            (pol / "policy.yaml").write_text(policy_yaml, encoding="utf-8")
        return d

    def test_resolves_worktree_bootstrap_cmd_from_policy(self):
        repo = self._repo('worktree_bootstrap_cmd: "npm ci"\n')
        self.assertEqual(live._default_bootstrap_cmd(repo), "npm ci")

    def test_unset_resolves_none(self):
        self.assertIsNone(
            live._default_bootstrap_cmd(self._repo("base_branch: main\n"))
        )

    def test_no_policy_file_resolves_none(self):
        self.assertIsNone(live._default_bootstrap_cmd(self._repo(None)))


class CliDispatchTests(unittest.TestCase):
    """`main()` must resolve both values from the repo before calling
    `full_real`. A hard-coded `default="origin"` on the flag is
    indistinguishable from an explicit `--remote origin` by the time dispatch
    sees it, which is exactly why the fork layout stayed broken."""

    def _fork_repo(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="cli-dispatch-"))
        _git(d.parent, "init", "-q", "-b", "main", str(d))
        _git(d, "config", "remote.pushDefault", "fork")
        pol = d / ".worktrail"
        pol.mkdir(parents=True)
        (pol / "policy.yaml").write_text('worktree_bootstrap_cmd: "npm ci"\n')
        return d

    def test_dispatch_resolves_remote_and_bootstrap_from_the_repo(self):
        repo = self._fork_repo()
        seen: dict[str, object] = {}

        def fake_full_real(repo_path, spec_rel, remote, *args, **kwargs):
            seen["remote"] = remote
            seen["bootstrap_cmd"] = kwargs.get("bootstrap_cmd")
            return {}

        with patch.object(live, "full_real", fake_full_real):
            live.main(
                ["full-real", "--repo", str(repo), "--spec", "openspec/changes/y"]
            )
        self.assertEqual(seen["remote"], "fork")
        self.assertEqual(seen["bootstrap_cmd"], "npm ci")

    def test_explicit_flags_still_win(self):
        repo = self._fork_repo()
        seen: dict[str, object] = {}

        def fake_full_real(repo_path, spec_rel, remote, *args, **kwargs):
            seen["remote"] = remote
            seen["bootstrap_cmd"] = kwargs.get("bootstrap_cmd")
            return {}

        with patch.object(live, "full_real", fake_full_real):
            live.main(
                [
                    "full-real",
                    "--repo",
                    str(repo),
                    "--spec",
                    "openspec/changes/y",
                    "--remote",
                    "upstream",
                    "--bootstrap-cmd",
                    "pnpm i",
                ]
            )
        self.assertEqual(seen["remote"], "upstream")
        self.assertEqual(seen["bootstrap_cmd"], "pnpm i")


class SpecAtFanoutRefsTests(unittest.TestCase):
    """One loud failure before any worktree exists, instead of one
    `WorktreeMissingTaskFileError` per task plus a quarantined group each."""

    def _repo_with_spec(self, spec_rel: str) -> Path:
        d = Path(tempfile.mkdtemp(prefix="fanout-preflight-"))
        _git(d.parent, "init", "-q", "-b", "main", str(d))
        _git(d, "config", "user.email", "t@example.com")
        _git(d, "config", "user.name", "t")
        (d / "README.md").write_text("x\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-q", "-m", "init")
        spec = d / spec_rel
        spec.mkdir(parents=True)
        (spec / "tasks.md").write_text("- [ ] 1.1 do a thing\n")
        return d

    def test_passes_when_head_carries_the_spec(self):
        repo = self._repo_with_spec("openspec/changes/feature")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "spec")
        live._require_spec_at_fanout_refs(
            repo, "openspec/changes/feature", "origin", "main"
        )

    def test_raises_when_head_predates_the_spec(self):
        repo = self._repo_with_spec("openspec/changes/feature")
        # spec written but never committed: HEAD does not carry it
        with self.assertRaises(live.WorktreeMissingTaskFileError) as ctx:
            live._require_spec_at_fanout_refs(
                repo, "openspec/changes/feature", "origin", "main"
            )
        message = str(ctx.exception)
        self.assertIn("openspec/changes/feature", message)
        self.assertIn("Commit the spec", message)

    def test_missing_remote_base_ref_is_not_fatal(self):
        repo = self._repo_with_spec("openspec/changes/feature")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "spec")
        # `origin/main` does not exist at all -- nothing to warn about, and
        # certainly nothing to abort a local run over.
        live._require_spec_at_fanout_refs(
            repo, "openspec/changes/feature", "origin", "main"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
