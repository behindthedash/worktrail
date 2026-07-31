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

    def test_resolve_spawn_provided_worker_succeeds_verified_clean_no_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
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

            def _resolving_spawn(prompt, worktree):
                (Path(worktree) / "shared.py").write_text(
                    "merged: task-001 + task-002\n"
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "add", "-A"], check=True
                )
                subprocess.run(
                    ["git", "-C", str(worktree), "commit", "-q", "--no-edit"],
                    check=True,
                )
                return (
                    "```json\n"
                    '{"task": "TASK-003", "step": "resolve", "status": "success"}\n'
                    "```"
                )

            live.add_stacked_worktree(
                repo,
                spec_id,
                by_id["TASK-003"],
                by_id,
                wt,
                assembly_resolve_spawn=_resolving_spawn,
            )

            # No lingering merge state.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "resolved merge must leave a clean tree")

            # Worktree carries both siblings' commits.
            for branch in (f"{spec_id}/task-001", f"{spec_id}/task-002"):
                self.assertEqual(
                    _git(wt, "merge-base", "--is-ancestor", branch, "HEAD").returncode,
                    0,
                    f"{branch} commit must be an ancestor of the stacked worktree HEAD",
                )
            self.assertEqual(
                (wt / "shared.py").read_text(), "merged: task-001 + task-002\n"
            )

    def test_resolve_spawn_raises_aborts_merge_and_raises(self):
        """When a resolve spawn is provided but the worker itself raises/crashes,
        the crash must not be trusted as a resolution: the merge is aborted and
        `WorktreeStackConflictError` is raised with the same message format as
        the no-resolve-spawn path."""
        with tempfile.TemporaryDirectory() as tmp:
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

            calls = []

            def _spawn_raises(prompt, worktree):
                calls.append((prompt, worktree))
                raise RuntimeError("resolve worker crashed")

            with self.assertRaises(live.WorktreeStackConflictError) as ctx:
                live.add_stacked_worktree(
                    repo,
                    spec_id,
                    by_id["TASK-003"],
                    by_id,
                    wt,
                    assembly_resolve_spawn=_spawn_raises,
                )
            self.assertEqual(len(calls), 1, "resolve spawn must have been invoked once")
            self.assertIn("TASK-003", str(ctx.exception))
            self.assertIn("task-002", str(ctx.exception).lower())

            # No lingering merge state left behind -- `git merge --abort` ran despite
            # the spawn crash, exactly as it would with no resolve spawn at all.
            status = _git(wt, "status", "--porcelain").stdout
            self.assertEqual(status.strip(), "", "merge must have been aborted cleanly")
            self.assertEqual((Path(wt) / "shared.py").read_text(), "from task-001\n")


if __name__ == "__main__":
    unittest.main()
