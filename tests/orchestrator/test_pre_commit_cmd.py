"""Unit + integration tests for the design-D6 pre-commit backstop (live.py).

After an implement/fix report with a `head_sha`, `_apply_pre_commit_backstop`
runs the repo's `pre_commit_cmd` (e.g. a formatter) in the worktree: in-scope
changes it makes are folded into the just-made commit (`--amend`, refreshing
`head_sha`); out-of-scope tracked changes are discarded and noted; a non-zero
exit or timeout is noted but never fails the task.
"""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import dispatch, live, spawnlib


def _init_git_repo(root: Path) -> Path:
    wt = root / "wt"
    wt.mkdir()
    subprocess.run(["git", "init", "-q", str(wt)], check=True)
    subprocess.run(["git", "-C", str(wt), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(wt), "config", "user.name", "T"], check=True)
    return wt


def _commit(wt: Path, rel: str, content: str, msg: str = "commit") -> str:
    f = wt / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", msg],
        check=True,
        capture_output=True,
    )
    return _head(wt)


def _head(wt: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _commit_count(wt: Path) -> int:
    return int(
        subprocess.run(
            ["git", "-C", str(wt), "rev-list", "--count", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
    )


class TestPreCommitBackstopUnit(unittest.TestCase):
    def test_unset_key_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = _init_git_repo(Path(tmp))
            sha = _commit(wt, "src/foo.py", "x\n")
            task = {"id": "TASK-001", "files": ["src/foo.py"]}
            rep = {"status": "success", "head_sha": sha[:8]}
            live._apply_pre_commit_backstop(wt, task, rep, None)
            self.assertEqual(rep["head_sha"], sha[:8])
            self.assertNotIn("_pre_commit_restored", task)
            self.assertNotIn("_pre_commit_error", task)

    def test_no_head_sha_runs_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = _init_git_repo(Path(tmp))
            _commit(wt, "src/foo.py", "x\n")
            task = {"id": "TASK-001", "files": ["src/foo.py"]}
            rep = {"status": "failed"}
            live._apply_pre_commit_backstop(wt, task, rep, "true")
            self.assertNotIn("head_sha", rep)

    def test_clean_tree_leaves_commit_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = _init_git_repo(Path(tmp))
            sha = _commit(wt, "src/foo.py", "x\n")
            task = {"id": "TASK-001", "files": ["src/foo.py"]}
            rep = {"status": "success", "head_sha": sha[:8]}
            live._apply_pre_commit_backstop(wt, task, rep, "true")
            self.assertEqual(rep["head_sha"], sha[:8])
            self.assertEqual(_head(wt), sha)
            self.assertNotIn("_pre_commit_restored", task)
            self.assertNotIn("_pre_commit_error", task)

    def test_in_scope_change_amended_and_head_sha_refreshed(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = _init_git_repo(Path(tmp))
            sha = _commit(wt, "src/foo.py", "x\n")
            before = _commit_count(wt)
            task = {"id": "TASK-001", "files": ["src/foo.py"]}
            rep = {"status": "success", "head_sha": sha[:8]}
            cmd = f"echo formatted >> {wt}/src/foo.py"
            live._apply_pre_commit_backstop(wt, task, rep, cmd)
            self.assertNotEqual(rep["head_sha"], sha[:8])
            self.assertEqual(rep["head_sha"], _head(wt)[:8])
            # amended, not a new commit
            self.assertEqual(_commit_count(wt), before)
            self.assertIn("formatted", (wt / "src" / "foo.py").read_text())

    def test_out_of_scope_change_restored_and_noted(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = _init_git_repo(Path(tmp))
            _commit(wt, "other.py", "orig\n", msg="init other")
            sha = _commit(wt, "src/foo.py", "x\n")
            task = {"id": "TASK-001", "files": ["src/foo.py"]}
            rep = {"status": "success", "head_sha": sha[:8]}
            cmd = f"echo mutated >> {wt}/other.py"
            live._apply_pre_commit_backstop(wt, task, rep, cmd)
            self.assertEqual(task["_pre_commit_restored"], ["other.py"])
            self.assertEqual((wt / "other.py").read_text(), "orig\n")
            self.assertEqual(rep["head_sha"], sha[:8])  # no in-scope change, no amend

    def test_non_zero_exit_noted_task_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = _init_git_repo(Path(tmp))
            sha = _commit(wt, "src/foo.py", "x\n")
            task = {"id": "TASK-001", "files": ["src/foo.py"]}
            rep = {"status": "success", "head_sha": sha[:8]}
            live._apply_pre_commit_backstop(wt, task, rep, "exit 3")
            self.assertIn("3", task["_pre_commit_error"])
            self.assertEqual(rep["head_sha"], sha[:8])
            self.assertNotIn("status", task)

    def test_timeout_noted_task_not_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = _init_git_repo(Path(tmp))
            sha = _commit(wt, "src/foo.py", "x\n")
            task = {"id": "TASK-001", "files": ["src/foo.py"]}
            rep = {"status": "success", "head_sha": sha[:8]}
            with mock.patch(
                "worktrail.orchestrator.live.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="slow", timeout=300),
            ):
                live._apply_pre_commit_backstop(wt, task, rep, "slow")
            self.assertIn("timed out", task["_pre_commit_error"])
            self.assertEqual(rep["head_sha"], sha[:8])
            self.assertNotIn("status", task)


def _init_spec_repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "docs" / "specs" / "001-x" / "tasks").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    fm = (
        "---\n"
        "id: TASK-001\n"
        "status: pending\n"
        "dependencies: []\n"
        "files: [src/foo.py]\n"
        "kind: impl\n"
        "review: skip\n"
        "---\nbody\n"
    )
    (repo / "docs" / "specs" / "001-x" / "tasks" / "TASK-001.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


class _ImplementOnlySpawn:
    """review: skip in frontmatter -> only implement/cleanup ever spawn."""

    def __init__(self, pre_commit_cmd=None):
        self.pre_commit_cmd = pre_commit_cmd
        self.calls: list = []

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        self.calls.append(role)
        assert role == dispatch.ROLE_IMPLEMENT
        f = Path(wt) / "src" / "foo.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("impl\n")
        subprocess.run(
            ["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True
        )
        subprocess.run(
            ["git", "-C", str(wt), "commit", "-q", "-m", "implement"],
            check=True,
            capture_output=True,
        )
        sha = _head(Path(wt))
        return spawnlib.SpawnResult(
            text=(
                '```json\n{{"task":"{}","step":"implement","status":"success",'
                '"head_sha":"{}","tests":"passed"}}\n```'
            ).format(task["id"], sha[:8]),
            usage={},
        )


class TestPreCommitBackstopIntegration(unittest.TestCase):
    def test_command_runs_before_journal_entry(self):
        import json

        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_spec_repo(Path(tmp))
            journal_path = str(Path(tmp) / "run-001-x.json")
            wt_base = Path(tmp) / "repo-worktrees"
            cmd = 'echo "# formatted" >> $(git rev-parse --show-toplevel)/src/foo.py'
            spawn = _ImplementOnlySpawn(pre_commit_cmd=cmd)
            live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=journal_path,
                run_id="test-precommit",
                spawn=spawn,
            )
            entries = json.loads(Path(journal_path).read_text())["entries"]
            impl_entry = next(e for e in entries if e.get("role") == "implement")
            wt = wt_base / "001-x-task-001"
            head = _head(wt)[:8]
            # The journal entry's head_sha is the AMENDED sha (backstop ran
            # before the entry was written), and the formatter's output is
            # part of that same commit -- no separate commit was made.
            self.assertEqual(impl_entry["report"]["head_sha"], head)
            self.assertIn("# formatted", (wt / "src" / "foo.py").read_text())


if __name__ == "__main__":
    unittest.main()
