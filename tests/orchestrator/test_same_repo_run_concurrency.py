#!/usr/bin/env python3.14
"""Same-repo run concurrency contract (task 2.1 of
same-repo-run-concurrency-contract).

A launch for one specification must see other LIVE runs on the same repo under
a DIFFERENT specification -- the per-spec `RunLock` cannot, since those runs
share the repo's worktrees/files but not its lock -- warn loudly, and halve its
own fan-out width, while still completing and merging normally.

Unit coverage for `_same_repo_live_runs()` and `_resolve_max_workers()`'s
`same_repo_live` cap, plus a real-`_full_real_inner` end-to-end case reusing
the lifecycle harness fixtures (repo builder, fake gh, real integrate/verify).
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worktrail.orchestrator import live
from worktrail.router.run_record import main as run_record_main
from worktrail.shared.homedir import worktrail_home

# `test_pipeline_e2e.py`'s AC-019 regression runs every suite in this directory
# as a standalone script, so cross-module fixture reuse is a flat import after
# adding this directory to sys.path (same pattern as
# `test_slot_refill_scheduler.py`'s `test_budget_resume` import) rather than a
# package-relative one.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lifecycle.test_lifecycle_harness import (
    DEVKIT_SPEC_REL,
    _install_fake_gh,
    _LifecycleCase,
    _mk_devkit_repo,
    _publish_to_bare_origin,
    _run_full_real,
)


def _git(repo: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *argv], check=True, capture_output=True, text=True
    )


def _mk_git_repo(tmp: Path) -> Path:
    """A real repo with one file committed on main -- lets a run record be
    classified stale (worktree gone, its file already landed on base)."""
    repo = tmp / "target-repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.md").write_text("tracked\n", encoding="utf-8")
    _git(repo, "add", "tracked.md")
    _git(repo, "-c", "commit.gpgsign=false", "commit", "-m", "initial")
    return repo


def _seed_record(
    runs_root: Path, repo: Path, request: str, specification: str | None = None
) -> dict:
    """Start a real (non-terminal) run record for `repo` under `runs_root`."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = run_record_main(
            [
                "start",
                "--repo",
                str(repo),
                "--request",
                request,
                "--route",
                "F",
                "--risk",
                "low",
                "--dir",
                str(runs_root),
            ]
        )
    assert rc == 0
    started = json.loads(out.getvalue())
    if specification is not None:
        run_record_main(["set", started["path"], "specification", specification])
    return started


# Minimal current-schema routing config, mirroring tests/conftest.py's autouse
# seed (one target per harness, all sharing one default_tier row): the e2e
# case spawns through `_full_real_inner`'s model resolution, which has no
# built-in fallback, so an isolated home needs a routing file of its own.
_ROUTING_YAML = (
    "targets:\n"
    "  claude-sub:\n"
    "    harness: claude\n"
    "    pool: subscription\n"
    "  codex-sub:\n"
    "    harness: codex\n"
    "    pool: subscription\n"
    "  opencode-free:\n"
    "    harness: opencode\n"
    "    pool: free\n"
    "tiers:\n"
    "  t2-build:\n"
    "    claude-sub:\n"
    "      model: sonnet\n"
    "    codex-sub:\n"
    "      model: gpt-5.4-mini\n"
    "    opencode-free:\n"
    "      model: opencode/deepseek-v4-flash-free\n"
    "default_tier: t2-build\n"
)


class _IsolatedHomeCase(unittest.TestCase):
    """Per-test `WORKTRAIL_HOME` + `GO_ROUTING_FILE`, mirroring
    `tests/conftest.py`'s autouse isolation fixture.

    `test_pipeline_e2e.py`'s AC-019 regression runs every suite in this
    directory as a standalone script, where that fixture does not apply --
    without this, `worktrail_home()/runs` would be the operator's real
    `~/.worktrail/runs`, which these tests would both read and write.
    """

    def setUp(self):
        self._home_tmp = tempfile.TemporaryDirectory()
        home = Path(self._home_tmp.name)
        (home / "routing.yaml").write_text(_ROUTING_YAML, encoding="utf-8")
        self._env = unittest.mock.patch.dict(
            os.environ,
            {
                "WORKTRAIL_HOME": str(home),
                "GO_ROUTING_FILE": str(home / "routing.yaml"),
            },
        )
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._home_tmp.cleanup()


class SameRepoLiveRunsDetection(_IsolatedHomeCase):
    """`_same_repo_live_runs()`: repo-wide scan reduced to OTHER specifications."""

    def test_returns_other_spec_live_run_and_omits_own_spec_and_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _mk_git_repo(tmp)
            runs_root = worktrail_home() / "runs"

            other = _seed_record(runs_root, repo, "other spec run", "other-spec")
            own = _seed_record(runs_root, repo, "launching run", "spec-id")
            stale = _seed_record(runs_root, repo, "stale run", "third-spec")
            run_record_main(["set", stale["path"], "base_branch", "main"])
            run_record_main(
                ["set", stale["path"], "worktree", str(tmp / "gone-worktree")]
            )
            run_record_main(["append", stale["path"], "files_changed", "tracked.md"])

            found = live._same_repo_live_runs(repo, "spec-id")

            self.assertEqual([e["run_id"] for e in found], [other["run_id"]])
            self.assertEqual(found[0]["specification"], "other-spec")
            ids = {e["run_id"] for e in found}
            self.assertNotIn(own["run_id"], ids)
            self.assertNotIn(stale["run_id"], ids)

    def test_returns_empty_list_when_repo_has_no_records_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _mk_git_repo(Path(tmp))

            self.assertEqual(live._same_repo_live_runs(repo, "spec-id"), [])

    def test_policy_run_record_dir_overrides_the_machine_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _mk_git_repo(tmp)
            runs_root = tmp / "policy-runs"
            (repo / ".worktrail").mkdir()
            (repo / ".worktrail" / "policy.yaml").write_text(
                f"run_record_dir: {runs_root}\n", encoding="utf-8"
            )
            other = _seed_record(runs_root, repo, "other spec run", "other-spec")
            # Same repo, same specification split, but under the machine default:
            # a policy-configured root must be honored, not merged with it.
            _seed_record(
                worktrail_home() / "runs", repo, "default-root run", "other-spec"
            )

            found = live._same_repo_live_runs(repo, "spec-id")

            self.assertEqual([e["run_id"] for e in found], [other["run_id"]])


class ResolveMaxWorkersSameRepoCap(_IsolatedHomeCase):
    """`_resolve_max_workers(..., same_repo_live=N)`: halve once, never below 1."""

    def _tasks(self, n: int) -> list:
        return [
            {"id": f"T{i}", "status": "pending", "files": [f"f{i}.py"], "deps": []}
            for i in range(n)
        ]

    def _repo(self, tmp: Path) -> Path:
        repo = tmp / "repo"
        repo.mkdir()
        return repo

    def _resolve(
        self, repo: Path, tasks: list, requested: int | None, same_repo_live: int
    ) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            width = live._resolve_max_workers(
                repo, tasks, requested, same_repo_live=same_repo_live
            )
        return width, out.getvalue()

    def test_explicit_request_halves_once_and_never_below_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))

            width, out = self._resolve(repo, self._tasks(7), 4, 1)
            self.assertEqual(width, 2)
            self.assertIn("fan-out workers: 2", out)
            self.assertIn("1 other live run(s) on repo", out)
            self.assertIn("same-repo concurrency", out)

            width, out = self._resolve(repo, self._tasks(7), 3, 1)
            self.assertEqual(width, 1)
            self.assertIn("fan-out workers: 1", out)
            self.assertIn("same-repo concurrency", out)

            width, _ = self._resolve(repo, self._tasks(7), 1, 1)
            self.assertEqual(width, 1)

            # Halved ONCE however many other runs are live.
            width, _ = self._resolve(repo, self._tasks(7), 4, 5)
            self.assertEqual(width, 2)

    def test_policy_max_workers_is_halved_with_the_reason_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))
            (repo / ".worktrail").mkdir()
            (repo / ".worktrail" / "policy.yaml").write_text(
                "max_workers: 4\n", encoding="utf-8"
            )

            width, out = self._resolve(repo, self._tasks(7), None, 1)

            self.assertEqual(width, 2)
            self.assertIn("fan-out workers: 2", out)
            self.assertIn("policy max_workers", out)
            self.assertIn("same-repo concurrency", out)
            self.assertIn("1 other live run(s) on repo", out)

    def test_plan_width_is_halved_with_the_reason_printed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))

            width, out = self._resolve(repo, self._tasks(4), None, 1)
            self.assertEqual(width, 2)
            self.assertIn("fan-out workers: 2", out)
            self.assertIn("plan width 4", out)
            self.assertIn("same-repo concurrency", out)

            width, out = self._resolve(repo, self._tasks(3), None, 1)
            self.assertEqual(width, 1)
            self.assertIn("fan-out workers: 1", out)

    def test_zero_other_live_runs_reproduces_todays_widths_and_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = self._repo(Path(tmp))

            # Explicit request: today's return value, and today's silence.
            width, out = self._resolve(repo, self._tasks(7), 2, 0)
            self.assertEqual(width, 2)
            self.assertEqual(out, "")

            (repo / ".worktrail").mkdir()
            policy = repo / ".worktrail" / "policy.yaml"
            policy.write_text("max_workers: 4\n", encoding="utf-8")
            width, out = self._resolve(repo, self._tasks(7), None, 0)
            self.assertEqual(width, 4)
            self.assertRegex(
                out, r"^\[\d\d:\d\d:\d\d\] fan-out workers: 4 \(policy max_workers\)\n$"
            )

            policy.write_text("max_parallel_workers: 6\n", encoding="utf-8")
            width, out = self._resolve(repo, self._tasks(7), None, 0)
            self.assertEqual(width, 6)
            self.assertRegex(
                out,
                r"^\[\d\d:\d\d:\d\d\] fan-out workers: 6 "
                r"\(plan width 7, max_parallel_workers 6\)\n$",
            )
            width, _ = self._resolve(repo, self._tasks(2), None, 0)
            self.assertEqual(width, 2)


class SameRepoConcurrencyEndToEnd(_IsolatedHomeCase, _LifecycleCase):
    """A real launch with a foreign live record seeded for the same repo."""

    def test_warns_halves_width_and_still_merges_the_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            repo = _mk_devkit_repo(tmp)
            remote = _publish_to_bare_origin(repo, tmp)
            env = _install_fake_gh(tmp, remote)

            # A live (non-terminal, no-worktree) record for this repo under a
            # DIFFERENT specification, in the isolated WORKTRAIL_HOME the
            # pipeline itself resolves (`worktrail_home()/runs/<repo>`).
            foreign = _seed_record(
                worktrail_home() / "runs",
                repo,
                "concurrent run under another specification",
                "other-spec",
            )

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                _run_full_real(repo, DEVKIT_SPEC_REL, env)

            stdout = out.getvalue()
            self.assertIn("WARN same-repo concurrency:", stdout)
            # The scan must run immediately after load_spec() -- before
            # apply_run_plan() (the first thing that prints the plan's
            # `parallelism:` summary line) does any planning work.
            self.assertLess(
                stdout.index("WARN same-repo concurrency:"),
                stdout.index("parallelism:"),
            )
            self.assertIn(foreign["run_id"], stdout)
            self.assertIn(foreign["path"], stdout)
            self.assertIn("other-spec", stdout)
            # The harness launches with --max-workers 3; one other live run
            # halves that to 1, and the width line names the reason.
            self.assertIn("fan-out workers: 1", stdout)
            self.assertIn("same-repo concurrency", stdout)
            # Detection is advisory: the run still completes and merges.
            self.assert_all_groups_merged(repo, DEVKIT_SPEC_REL)


if __name__ == "__main__":
    unittest.main(verbosity=2)
