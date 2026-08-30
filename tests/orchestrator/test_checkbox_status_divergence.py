#!/usr/bin/env python3
"""Post-run conservation invariant: every task this run's in-memory record
calls DONE must be reflected as done in the task-source artifact (tasks.md
checkbox / TASK-*.md status) actually on `<remote>/<base>` right now.

Generalizes two incidents each fixed one-off before this check existed:
- PR #414: `integrate_one`'s dependency-branch-gone fallback silently reverted
  a different group's already-landed `tasks.md` checkboxes via an
  unconditioned `-X ours` reconcile merge.
- PR #847: a synthetic tail-reconciliation group's checkbox write was scoped
  to only its own leaf task id, leaving every superseded ancestor's checkbox
  untouched even though its code landed in the same merge.

Both were only caught by a human manually diffing `origin/main` against
`tasks.md` after the fact. These tests pin the general contract:
`integrate.detect_checkbox_status_divergence` re-reads the artifact from the
live base branch and compares it against the run's own final per-task status,
independent of any bookkeeping the run itself performed.

Run: python3 -m pytest tests/orchestrator/test_checkbox_status_divergence.py
"""

from __future__ import annotations

import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path

from worktrail.orchestrator import integrate


def _run_git(cwd, *args):
    r = subprocess.run(
        ["git", *args], check=False, cwd=str(cwd), capture_output=True, text=True
    )
    assert r.returncode == 0, f"git {' '.join(args)} failed: {r.stderr}"
    return r.stdout.strip()


def _task(task_id, status="done"):
    return {"id": task_id, "status": status}


TASKS_MD_ALL_TICKED = textwrap.dedent(
    """\
    ## 1. Setup

    - [x] 1.1 Create module structure
    - [x] 1.2 Add dependency
    """
)

TASKS_MD_ONE_REVERTED = textwrap.dedent(
    """\
    ## 1. Setup

    - [x] 1.1 Create module structure
    - [ ] 1.2 Add dependency
    """
)


class CheckboxStatusDivergence(unittest.TestCase):
    def _init_repo_with_tasks_md(self, tmpdir, change_id, tasks_md):
        repo = Path(tmpdir) / "myrepo"
        repo.mkdir()
        _run_git(repo, "init", "-q", "-b", "main")
        change_dir = repo / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True)
        (change_dir / "tasks.md").write_text(tasks_md)
        _run_git(repo, "add", "-A")
        _run_git(
            repo,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "-m",
            "add tasks.md",
        )
        # Local stand-in for the remote-tracking ref the detector fetches and
        # compares against -- no real remote needed for these tests.
        _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")
        return repo

    def _rewrite_tasks_md_on_base(self, repo, change_id, tasks_md):
        """Simulate the artifact diverging from what the run believes -- e.g.
        PR #414's stale `-X ours` reconcile reverting an already-ticked box."""
        (repo / "openspec" / "changes" / change_id / "tasks.md").write_text(tasks_md)
        _run_git(repo, "add", "-A")
        _run_git(
            repo,
            "-c",
            "user.email=t@example.com",
            "-c",
            "user.name=T",
            "commit",
            "-q",
            "-m",
            "revert a checkbox",
        )
        _run_git(repo, "update-ref", "refs/remotes/origin/main", "main")

    def test_no_finding_when_checkbox_ticked_on_base(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_repo_with_tasks_md(
                tmpdir, "add-export", TASKS_MD_ALL_TICKED
            )
            findings = integrate.detect_checkbox_status_divergence(
                repo, "origin", "main", "add-export", [_task("1.1"), _task("1.2")]
            )
            self.assertEqual(findings, [])

    def test_flags_done_task_whose_checkbox_is_not_ticked_on_base(self):
        """The PR #414/#847 shape: the run's own record says 1.2 is DONE, but
        the artifact actually on base disagrees."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_repo_with_tasks_md(
                tmpdir, "add-export", TASKS_MD_ALL_TICKED
            )
            self._rewrite_tasks_md_on_base(
                repo, "add-export", TASKS_MD_ONE_REVERTED
            )

            findings = integrate.detect_checkbox_status_divergence(
                repo, "origin", "main", "add-export", [_task("1.1"), _task("1.2")]
            )

            self.assertEqual(len(findings), 1)
            self.assertEqual(findings[0]["task"], "1.2")
            self.assertEqual(findings[0]["base_status"], "pending")

    def test_non_terminal_task_is_not_flagged(self):
        """A task still in flight has no delivery obligation yet -- only
        coordinator.DONE statuses are subject to the invariant."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_repo_with_tasks_md(
                tmpdir, "add-export", TASKS_MD_ONE_REVERTED
            )
            findings = integrate.detect_checkbox_status_divergence(
                repo, "origin", "main", "add-export", [_task("1.2", status="implementing")]
            )
            self.assertEqual(findings, [])

    def test_no_remote_or_base_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_repo_with_tasks_md(
                tmpdir, "add-export", TASKS_MD_ALL_TICKED
            )
            self.assertEqual(
                integrate.detect_checkbox_status_divergence(
                    repo, None, "main", "add-export", [_task("1.1")]
                ),
                [],
            )
            self.assertEqual(
                integrate.detect_checkbox_status_divergence(
                    repo, "origin", None, "add-export", [_task("1.1")]
                ),
                [],
            )

    def test_no_done_tasks_returns_empty_without_touching_git(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = self._init_repo_with_tasks_md(
                tmpdir, "add-export", TASKS_MD_ALL_TICKED
            )
            findings = integrate.detect_checkbox_status_divergence(
                repo, "origin", "main", "add-export", [_task("1.1", status="implementing")]
            )
            self.assertEqual(findings, [])


if __name__ == "__main__":
    unittest.main()
