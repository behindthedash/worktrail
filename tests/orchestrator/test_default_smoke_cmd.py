#!/usr/bin/env python3
"""Tests for auto-resolving the pre-PR gate command when `--smoke-cmd` is
omitted from a `worktrail-live full-real` invocation.

Motivation: `integrate.py`'s group-PR creation is a raw `subprocess.run(["gh",
"pr", "create", ...])` call -- never reachable by a Claude Code hook, headless
or interactive -- and the only gate in front of it (`--smoke-cmd`) was
entirely opt-in, sourced from policy by the *calling agent*, not the code.
Forgetting to pass it (which happened on this exact repo, which has
`pre_pr_cmd` configured) meant a group PR could open with zero test
verification. `live._default_smoke_cmd()` closes that gap by resolving the
same command `pre_pr_gate.py` would, directly from policy, whenever the
caller did not pass one explicitly.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.orchestrator import live


class DefaultSmokeCmdResolutionTests(unittest.TestCase):
    def _repo(self, policy_yaml: str | None) -> str:
        d = tempfile.mkdtemp(prefix="default-smoke-")
        if policy_yaml is not None:
            spec = Path(d) / ".worktrail"
            spec.mkdir(parents=True)
            (spec / "policy.yaml").write_text(policy_yaml, encoding="utf-8")
        return d

    def test_resolves_pre_pr_cmd_when_configured(self):
        repo = self._repo('pre_pr_cmd: "pytest -q"\n')
        self.assertEqual(live._default_smoke_cmd(Path(repo)), "pytest -q")

    def test_falls_back_to_integrate_smoke_cmd(self):
        repo = self._repo('integrate_smoke_cmd: "make check"\n')
        self.assertEqual(live._default_smoke_cmd(Path(repo)), "make check")

    def test_pre_pr_cmd_wins_over_integrate_smoke_cmd(self):
        repo = self._repo(
            'pre_pr_cmd: "pytest -q"\nintegrate_smoke_cmd: "make check"\n'
        )
        self.assertEqual(live._default_smoke_cmd(Path(repo)), "pytest -q")

    def test_unconfigured_repo_resolves_none(self):
        # No go-policy.yaml at all -- matches the pre-existing "omit
        # --smoke-cmd to skip" contract; this fix does not change behavior
        # for repos with no gate configured.
        repo = self._repo(None)
        self.assertIsNone(live._default_smoke_cmd(Path(repo)))

    def test_missing_repo_path_resolves_none(self):
        self.assertIsNone(live._default_smoke_cmd(Path("/nonexistent/repo/path")))

    def test_explicit_skip_resolves_none(self):
        repo = self._repo('pre_pr_cmd: "skip"\n')
        self.assertIsNone(live._default_smoke_cmd(Path(repo)))

    def test_explicit_skip_case_insensitive(self):
        repo = self._repo('pre_pr_cmd: "SKIP"\n')
        self.assertIsNone(live._default_smoke_cmd(Path(repo)))


class DefaultPostMergeSmokeCmdResolutionTests(unittest.TestCase):
    """Sibling of DefaultSmokeCmdResolutionTests for the cumulative post-merge
    gate (worktrail PR #167 follow-up): post_merge_smoke_cmd wins,
    integrate_smoke_cmd is the fallback, neither set = gate skipped."""

    def _repo(self, policy_yaml: str | None) -> str:
        d = tempfile.mkdtemp(prefix="default-post-merge-smoke-")
        if policy_yaml is not None:
            spec = Path(d) / ".worktrail"
            spec.mkdir(parents=True)
            (spec / "policy.yaml").write_text(policy_yaml, encoding="utf-8")
        return d

    def test_resolves_post_merge_smoke_cmd_when_configured(self):
        repo = self._repo('post_merge_smoke_cmd: "pytest -q -k smoke"\n')
        self.assertEqual(
            live._default_post_merge_smoke_cmd(Path(repo)), "pytest -q -k smoke"
        )

    def test_falls_back_to_integrate_smoke_cmd(self):
        repo = self._repo('integrate_smoke_cmd: "make check"\n')
        self.assertEqual(live._default_post_merge_smoke_cmd(Path(repo)), "make check")

    def test_post_merge_smoke_cmd_wins_over_integrate_smoke_cmd(self):
        repo = self._repo(
            'post_merge_smoke_cmd: "pytest -q -k smoke"\nintegrate_smoke_cmd: "make check"\n'
        )
        self.assertEqual(
            live._default_post_merge_smoke_cmd(Path(repo)), "pytest -q -k smoke"
        )

    def test_unconfigured_repo_resolves_none(self):
        repo = self._repo(None)
        self.assertIsNone(live._default_post_merge_smoke_cmd(Path(repo)))


class FullRealCLIAutoResolveTests(unittest.TestCase):
    """`live.main()` wiring: --smoke-cmd omitted auto-resolves from policy;
    an explicit --smoke-cmd always wins."""

    def _repo(self, policy_yaml: str | None) -> str:
        d = tempfile.mkdtemp(prefix="default-smoke-cli-")
        if policy_yaml is not None:
            spec = Path(d) / ".worktrail"
            spec.mkdir(parents=True)
            (spec / "policy.yaml").write_text(policy_yaml, encoding="utf-8")
        return d

    def _run(self, repo: str, *extra: str) -> dict:
        captured = {}

        def fake_full_real(*_args, **kwargs):
            captured.update(kwargs)
            return {}

        argv = [
            "full-real",
            "--repo",
            repo,
            "--spec",
            "docs/specs/001-foo",
            "--base",
            "main",
        ] + list(extra)
        with patch.object(live, "full_real", side_effect=fake_full_real):
            live.main(argv)
        return captured

    def test_smoke_cmd_omitted_auto_resolves_from_policy(self):
        repo = self._repo('pre_pr_cmd: "pytest -q"\n')
        captured = self._run(repo)
        self.assertEqual(captured.get("smoke_cmd"), "pytest -q")

    def test_explicit_smoke_cmd_wins_over_policy(self):
        repo = self._repo('pre_pr_cmd: "pytest -q"\n')
        captured = self._run(repo, "--smoke-cmd", "make check")
        self.assertEqual(captured.get("smoke_cmd"), "make check")

    def test_unconfigured_repo_still_passes_none(self):
        repo = self._repo(None)
        captured = self._run(repo)
        self.assertIsNone(captured.get("smoke_cmd"))

    def test_post_merge_smoke_cmd_omitted_auto_resolves_from_policy(self):
        repo = self._repo('post_merge_smoke_cmd: "pytest -q -k smoke"\n')
        captured = self._run(repo)
        self.assertEqual(captured.get("post_merge_smoke_cmd"), "pytest -q -k smoke")

    def test_explicit_post_merge_smoke_cmd_wins_over_policy(self):
        repo = self._repo('post_merge_smoke_cmd: "pytest -q -k smoke"\n')
        captured = self._run(repo, "--post-merge-smoke-cmd", "make postmerge")
        self.assertEqual(captured.get("post_merge_smoke_cmd"), "make postmerge")

    def test_post_merge_smoke_cmd_unconfigured_repo_still_passes_none(self):
        repo = self._repo(None)
        captured = self._run(repo)
        self.assertIsNone(captured.get("post_merge_smoke_cmd"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
