"""_validate_retained_task_branch auto-merge repair (stale retained branches).

When the run base advances while a task's retained branch sits quarantined or
its background run was killed, resume used to fail loud with WorktreeAddError
("is stale") and require the operator's mechanical repair: merge the base into
the branch in its retained worktree. Observed repeatedly in production
(2026-08-28, 11 relaunches of one run). With the branch's own worktree passed
as `wt`, the validator now performs exactly that repair when the merge is
clean, and still fails loud on conflicts or when no worktree is available.
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.orchestrator import live


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


class RetainedBranchAutoMergeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-q", "-b", "main")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        (self.repo / "shared.txt").write_text("base\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "base")
        # Task branch with its own commit, checked out in a retained worktree.
        self.wt = base / "wt"
        _git(self.repo, "worktree", "add", "-q", "-b", "spec/t1", str(self.wt), "main")
        (self.wt / "task.txt").write_text("task work\n")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "task work")
        self.task_head = _git(self.wt, "rev-parse", "HEAD").stdout.strip()

    def _advance_main(self, filename: str, content: str) -> None:
        (self.repo / filename).write_text(content)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", f"advance {filename}")

    def test_fresh_branch_returns_no_events(self):
        events = live._validate_retained_task_branch(
            self.repo, "spec/t1", "main", self.task_head, wt=self.wt
        )
        self.assertEqual(events, [])

    def test_stale_branch_with_worktree_auto_merges_cleanly(self):
        self._advance_main("other.txt", "advanced\n")
        events = live._validate_retained_task_branch(
            self.repo, "spec/t1", "main", self.task_head, wt=self.wt
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "retained_branch_auto_merged")
        self.assertEqual(events[0]["branch"], "spec/t1")
        # Ancestry repaired: main is now an ancestor of the branch, and the
        # journaled task head is still contained (merge preserved it).
        _git(self.repo, "merge-base", "--is-ancestor", "main", "spec/t1")
        _git(self.repo, "merge-base", "--is-ancestor", self.task_head, "spec/t1")
        # The task's own work is intact in the worktree.
        self.assertEqual((self.wt / "task.txt").read_text(), "task work\n")

    def test_stale_branch_with_conflict_aborts_and_raises(self):
        (self.wt / "shared.txt").write_text("task version\n")
        _git(self.wt, "add", "-A")
        _git(self.wt, "commit", "-qm", "task touches shared")
        head_before = _git(self.wt, "rev-parse", "HEAD").stdout.strip()
        self._advance_main("shared.txt", "main version\n")
        with self.assertRaises(live.WorktreeAddError) as ctx:
            live._validate_retained_task_branch(
                self.repo, "spec/t1", "main", None, wt=self.wt
            )
        self.assertIn("is stale", str(ctx.exception))
        self.assertIn("conflict", str(ctx.exception).lower())
        # Merge was aborted: no MERGE_HEAD, branch head unchanged, work intact.
        merge_head = subprocess.run(
            ["git", "-C", str(self.wt), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(merge_head.returncode, 0)
        self.assertEqual(_git(self.wt, "rev-parse", "HEAD").stdout.strip(), head_before)
        self.assertEqual((self.wt / "shared.txt").read_text(), "task version\n")

    def test_stale_branch_without_worktree_raises_unchanged(self):
        self._advance_main("other.txt", "advanced\n")
        with self.assertRaises(live.WorktreeAddError) as ctx:
            live._validate_retained_task_branch(
                self.repo, "spec/t1", "main", self.task_head
            )
        self.assertIn("is stale", str(ctx.exception))
        self.assertNotIn("conflict", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()
