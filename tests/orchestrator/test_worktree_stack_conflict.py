#!/usr/bin/env python3
"""Regression test: `add_stacked_worktree` must not silently continue when a
sibling dependency branch cannot be merged into a stacked worktree.

Reproduces the incident from `~/.go/runs/datalena/go-20260730-133115.yaml`:
two sibling tasks (no dependency edge between them) both touched the same
file; a dependent task's stacked worktree tried to merge both sibling
branches in and hit a conflict on the second merge. The old behavior printed
a warning and proceeded on the first branch only, silently missing the second
sibling's commits -- the dependent then failed much later, far from the real
cause. It must now raise immediately so `_safe_drive` can isolate the failure
to just this task instead of letting it corrupt the worktree's content.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "Test")
    (path / "shared.py").write_text("base\n")
    (path / "README.md").write_text("base\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-q", "-m", "base")


def _branch_editing_shared_file(repo, branch, content, start="HEAD"):
    base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "-B", branch, start)
    (Path(repo) / "shared.py").write_text(content)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", f"edit shared.py on {branch}")
    _git(repo, "checkout", "-q", base)


class AddStackedWorktreeSiblingConflict(unittest.TestCase):
    def test_raises_instead_of_silently_dropping_sibling_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_repo(repo)

            spec_id = "102-x"
            # Two independent siblings (no dep edge between them) both edit
            # shared.py from the same base -- exactly the 1.2/1.3 scenario.
            _branch_editing_shared_file(repo, f"{spec_id}/task-001", "from task-001\n")
            _branch_editing_shared_file(repo, f"{spec_id}/task-002", "from task-002\n")

            by_id = {
                "TASK-001": {"id": "TASK-001", "deps": []},
                "TASK-002": {"id": "TASK-002", "deps": []},
                "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
            }
            wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
            wt.parent.mkdir(parents=True)

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(repo, spec_id, by_id["TASK-003"], by_id, wt)
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # No lingering merge state left behind for a human/resume to trip on.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "merge must have been aborted cleanly")


class AddStackedWorktreeResolveSpawnUnverified(unittest.TestCase):
    """4.3: resolve spawn provided, worker reports success, but the git state
    doesn't back it up -- must be treated as unverified and raise, exactly as
    if no resolve had been attempted at all."""

    def _setup_conflict(self, tmp):
        repo = Path(tmp) / "repo"
        repo.mkdir()
        _init_repo(repo)

        spec_id = "102-x"
        _branch_editing_shared_file(repo, f"{spec_id}/task-001", "from task-001\n")
        _branch_editing_shared_file(repo, f"{spec_id}/task-002", "from task-002\n")

        by_id = {
            "TASK-001": {"id": "TASK-001", "deps": []},
            "TASK-002": {"id": "TASK-002", "deps": []},
            "TASK-003": {"id": "TASK-003", "deps": ["TASK-001", "TASK-002"]},
        }
        wt = Path(tmp) / "wt" / f"{spec_id}-task-003"
        wt.parent.mkdir(parents=True)
        return repo, spec_id, by_id, wt

    def test_reports_success_but_merge_head_still_present(self):
        # Worker claims success and even cleans up the conflict markers, but
        # never runs `git merge --continue` -- MERGE_HEAD is still there.
        def fake_spawn(prompt, wt):
            (Path(wt) / "shared.py").write_text("resolved, but never committed\n")
            _git(wt, "add", "shared.py")
            return '```json\n{"task": "TASK-003", "step": "assembly-resolve", "status": "success"}\n```'

        with tempfile.TemporaryDirectory() as tmp:
            repo, spec_id, by_id, wt = self._setup_conflict(tmp)

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(
                    repo,
                    spec_id,
                    by_id["TASK-003"],
                    by_id,
                    wt,
                    assembly_resolve_spawn=fake_spawn,
                )
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # Rejected resolution must leave no lingering merge state behind.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "merge must have been aborted cleanly")
            self.assertNotEqual(
                subprocess.run(
                    ["git", "-C", str(wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
                    capture_output=True,
                ).returncode,
                0,
                "MERGE_HEAD must not remain after a rejected resolution",
            )

    def test_reports_success_but_conflict_markers_remain(self):
        # Worker claims success and completes the merge, but never actually
        # removed the conflict markers from the file it staged/committed.
        def fake_spawn(prompt, wt):
            _git(wt, "add", "shared.py")  # stage as-is, markers still present
            _git(wt, "commit", "-q", "-m", "resolve (bogus, markers left in)")
            return '```json\n{"task": "TASK-003", "step": "assembly-resolve", "status": "success"}\n```'

        with tempfile.TemporaryDirectory() as tmp:
            repo, spec_id, by_id, wt = self._setup_conflict(tmp)

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(
                    repo,
                    spec_id,
                    by_id["TASK-003"],
                    by_id,
                    wt,
                    assembly_resolve_spawn=fake_spawn,
                )
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # The worker's own (bogus) commit is not undone by our abort --
            # `merge --abort` only undoes an in-progress merge, and this
            # worker committed it. What matters is that we did NOT accept the
            # resolution and continue on as if the stack succeeded: the raise
            # above already confirms that.


if __name__ == "__main__":
    unittest.main()
