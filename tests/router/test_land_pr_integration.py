#!/usr/bin/env python3
"""End-to-end `land_pr()` runs against real `git` and a bare `origin`, with
`tests/orchestrator/lifecycle/fake_gh.py` first on `PATH` (the suite's
canonical subprocess-level `gh` stand-in -- see its own module docstring)
instead of a Python-level mock, so the real argument-building/JSON-parsing
code in `land_pr.py` actually executes.

The one seam still faked is `conductor_compile`'s model spawn
(`worktrail.conductor.compile._default_spawn`): an OpenSpec change whose
tasks lack an authored `files:` scope needs a real inference pass to compile,
and this suite has no agent-CLI credentials wired in (same constraint
`check_compile_markers.py`'s own module docstring documents for CI). Patching
just that one function -- not `conductor_compile.main()` itself -- keeps the
rest of the compile-marker gate's real code (fingerprint comparison, marker
read/write, git add/commit) on the real execution path.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import land_pr

_HERE = Path(__file__).resolve().parent
_FAKE_GH = _HERE.parent / "orchestrator" / "lifecycle" / "fake_gh.py"

POLICY_YAML = 'pre_pr_cmd: "true"\n'

TASKS_MD_NO_FILE_SCOPE = "## 1. Setup\n\n- [ ] 1.1 Do the thing\n"

TASKS_MD_WITH_FILE_SCOPE = (
    "## 1. Setup\n\n- [ ] 1.1 Do the thing\n  files: src/thing.py\n"
)


class RecordingRunner:
    """Wraps the real `subprocess.run` so the real `git`/`gh` argument
    building in `land_pr.py` actually executes end to end, while still
    letting the test inspect exactly what was invoked (e.g. the `--label`
    flags on `gh pr create`, which the fake `gh` state file does not
    persist)."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs) -> subprocess.CompletedProcess:
        self.calls.append(list(cmd))
        check = kwargs.pop("check", False)
        return subprocess.run(cmd, check=check, **kwargs)

    def calls_matching(self, *prefix: str) -> list[list[str]]:
        return [c for c in self.calls if c[: len(prefix)] == list(prefix)]


class _LandPrIntegrationBase(unittest.TestCase):
    def setUp(self) -> None:
        self._orig_path = os.environ.get("PATH", "")
        self._orig_gh_state = os.environ.get("GH_FAKE_STATE")
        self.addCleanup(self._restore_env)

    def _restore_env(self) -> None:
        os.environ["PATH"] = self._orig_path
        if self._orig_gh_state is None:
            os.environ.pop("GH_FAKE_STATE", None)
        else:
            os.environ["GH_FAKE_STATE"] = self._orig_gh_state

    def _git(self, cwd, *args, check=True):
        return subprocess.run(
            ["git", "-C", str(cwd), *args], check=check, capture_output=True, text=True
        )

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _make_repo_and_remote(self, tmp_path: Path) -> tuple[Path, Path]:
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        self._git(repo, "config", "user.email", "t@t")
        self._git(repo, "config", "user.name", "T")
        self._write(repo / ".worktrail" / "policy.yaml", POLICY_YAML)
        self._write(repo / "README.md", "hello\n")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "base")

        remote = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
        self._git(repo, "remote", "add", "origin", str(remote))
        self._git(repo, "push", "-q", "origin", "main")
        return repo, remote

    def _install_fake_gh(self, tmp_path: Path, remote: Path) -> None:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        shim = bin_dir / "gh"
        shim.write_text(f'#!/bin/sh\nexec {sys.executable} {_FAKE_GH} "$@"\n')
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
        state = tmp_path / "gh-state.json"
        state.write_text(
            json.dumps(
                {"remote": str(remote), "base": "main", "next_number": 1, "prs": {}}
            )
        )
        os.environ["PATH"] = f"{bin_dir}:{self._orig_path}"
        os.environ["GH_FAKE_STATE"] = str(state)

    def _gh_state(self) -> dict:
        return json.loads(Path(os.environ["GH_FAKE_STATE"]).read_text())


class RefusedCompileMarkerTests(_LandPrIntegrationBase):
    """(a)/(b): a change whose tasks carry no authored `files:` scope needs a
    real model to compile, which this hermetic suite cannot provide -- the
    compile-marker gate must therefore refuse, and refusal must never push."""

    def _run_case(
        self, tmp_path: Path, *, seed_stale_marker: bool
    ) -> land_pr.LandOutcome:
        repo, remote = self._make_repo_and_remote(tmp_path)
        self._install_fake_gh(tmp_path, remote)

        self._git(repo, "checkout", "-q", "-b", "feature")
        change_dir = repo / "openspec" / "changes" / "add-thing"
        self._write(change_dir / "proposal.md", "## Why\nBecause.\n")
        self._write(change_dir / "tasks.md", TASKS_MD_NO_FILE_SCOPE)

        if seed_stale_marker:
            # A marker committed against an EARLIER version of tasks.md --
            # the compile-marker gate re-compiles unconditionally regardless
            # of any pre-existing marker's freshness, so this still hits the
            # exact same "compile can't pass without a model" refusal as the
            # no-marker case, just from a stale rather than absent starting
            # point.
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "add change (old tasks.md)")
            (change_dir / ".compile-ok").write_text(
                "stale-fingerprint-from-before-tasks-md-changed\n", encoding="utf-8"
            )
            self._git(repo, "add", "-A")
            self._git(repo, "commit", "-q", "-m", "record stale compile marker")
            self._write(
                change_dir / "tasks.md",
                TASKS_MD_NO_FILE_SCOPE + "- [ ] 1.2 A second thing\n",
            )

        runner = RecordingRunner()
        request = land_pr.LandRequest(
            repo=str(repo),
            base_branch="main",
            title="Add thing",
            summary="Adds the thing.",
            route="B",
            risk="high",
            gates=["never_automerge"],
            commit_message="feat(add-thing): add change",
            runner=runner,
        )
        with mock.patch(
            "worktrail.conductor.compile._default_spawn",
            side_effect=RuntimeError("no agent-CLI credentials in this suite"),
        ):
            outcome = land_pr.land_pr(request)
        self.assertEqual(outcome.outcome, "refused")
        self.assertEqual(outcome.refused_step, "compile_marker")

        ls_remote = self._git(repo, "ls-remote", "origin", "feature")
        self.assertEqual(ls_remote.stdout.strip(), "")
        self.assertEqual(self._gh_state()["prs"], {})
        return outcome

    def test_no_marker_refuses_and_leaves_remote_untouched(self, tmp_path=None) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="land-pr-a-") as d:
            self._run_case(Path(d), seed_stale_marker=False)

    def test_stale_marker_refuses_and_leaves_remote_untouched(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="land-pr-b-") as d:
            self._run_case(Path(d), seed_stale_marker=True)


class MarkerCommittedPushedAndPrOpenedTests(_LandPrIntegrationBase):
    def test_files_scope_compiles_pushes_and_opens_labeled_pr(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="land-pr-c-") as d:
            tmp_path = Path(d)
            repo, remote = self._make_repo_and_remote(tmp_path)
            self._install_fake_gh(tmp_path, remote)

            self._git(repo, "checkout", "-q", "-b", "feature")
            change_dir = repo / "openspec" / "changes" / "add-thing"
            self._write(change_dir / "proposal.md", "## Why\nBecause.\n")
            self._write(change_dir / "tasks.md", TASKS_MD_WITH_FILE_SCOPE)

            runner = RecordingRunner()
            request = land_pr.LandRequest(
                repo=str(repo),
                base_branch="main",
                title="Add thing",
                summary="Adds the thing.",
                route="B",
                risk="high",
                gates=["never_automerge"],
                commit_message="feat(add-thing): add change",
                watch_timeout_s=5,
                runner=runner,
            )
            # A real model call would be a bug here: `files:` scope means
            # `needs_compile()` finds no gaps, so this path is never reached.
            with mock.patch(
                "worktrail.conductor.compile._default_spawn",
                side_effect=AssertionError(
                    "files: scope should skip the model entirely"
                ),
            ):
                outcome = land_pr.land_pr(request)

            # The compile marker is committed to the branch...
            marker_log = self._git(
                repo,
                "log",
                "--pretty=%s",
                "--",
                "openspec/changes/add-thing/.compile-ok",
            )
            self.assertIn("compile marker", marker_log.stdout)
            self.assertTrue((change_dir / ".compile-ok").exists())

            # ...the branch is pushed...
            ls_remote = self._git(repo, "ls-remote", "origin", "feature")
            self.assertNotEqual(ls_remote.stdout.strip(), "")

            # ...and exactly one PR landed in the fake state, opened with the
            # preflight-recorded labels (verified from the real `gh pr
            # create` invocation, since the fake state file itself doesn't
            # persist labels).
            state = self._gh_state()
            self.assertIn("feature", state["prs"])
            create_calls = runner.calls_matching("gh", "pr", "create")
            self.assertEqual(len(create_calls), 1)
            self.assertIn("--label", create_calls[0])
            self.assertIn("go:risk-high", create_calls[0])
            self.assertIn("go:no-automerge", create_calls[0])

            # CI is never observed by the fake `gh` (no `pr checks` support),
            # so this correctly can't complete as `landed` -- it's the
            # documented "needs reconciliation" ceiling, not a refusal (the
            # branch and PR are already live on the remote by this point).
            self.assertEqual(outcome.outcome, "ceiling")
            self.assertIsNotNone(outcome.pr_url)


if __name__ == "__main__":
    unittest.main(verbosity=2)
