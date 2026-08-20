#!/usr/bin/env python3
"""Regression for work-queue brief 20260817-223443, gap 3:
`_apply_step_commit` (and `live_run()`'s mirrored `drive()` closure) only
stamped `report_fields["terminal_status"]` for the review 3-strikes circuit
breaker's `"escalated"` outcome -- a normal role's terminal `status: "failed"`
report (e.g. a fix-role worker that legitimately declines an out-of-scope
change) transitions the task to `"failed"` but the journal entry never gets a
`terminal_status` key at all. `clear_tasks()`'s `_terminal_failure()` gate
then refused with "no failed/escalated journal entries" even though
`worktrail-live status` itself reported the task as `failed`.

Mirrors `test_review_circuit_breaker_terminal_status.py`'s hermetic
injected-spawn approach against a throwaway git repo.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402
from worktrail.orchestrator import spawnlib  # noqa: E402


def _init_repo(root: Path) -> Path:
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
        "---\nbody\n"
    )
    (repo / "docs" / "specs" / "001-x" / "tasks" / "TASK-001.md").write_text(fm)
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True, capture_output=True
    )
    return repo


def _commit_file(wt: Path, name: str, content: str) -> str:
    f = Path(wt) / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", name], check=True, capture_output=True
    )
    return subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _report(task_id: str, role: str, sha: str, *, review_status=None) -> spawnlib.SpawnResult:
    rs = f'"{review_status}"' if review_status else "null"
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"{role}","status":"success",'
            f'"head_sha":"{sha[:8]}","review_status":{rs}}}\n```'
        ),
        usage={},
    )


def _failed_report(task_id: str, role: str) -> spawnlib.SpawnResult:
    """A normal terminal failure report -- no `head_sha`, no `terminal_status`
    key, matching the shape a fix-role worker sends when it legitimately
    declines an out-of-scope change (e.g. "not making unauthorized changes
    outside this task's scope")."""
    return spawnlib.SpawnResult(
        text=f'```json\n{{"task":"{task_id}","step":"{role}","status":"failed"}}\n```',
        usage={},
    )


class ReviewFailsThenFixDeclines:
    """implement succeeds, review comes back FAILED once (routing to fix), then
    the fix-role worker sends a plain `status:"failed"` report -- the same
    shape `dispatch.transition()` produces for any role's normal terminal
    failure, not synthesized as a journal entry."""

    def __init__(self):
        self._commit_count = 0

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        if role == "implement":
            self._commit_count += 1
            sha = _commit_file(wt, "foo.py", f"implement-{self._commit_count}\n")
            return _report(task["id"], role, sha)
        if role == "review":
            return _report(task["id"], role, sha, review_status="FAILED")
        if role == "fix":
            return _failed_report(task["id"], role)
        raise AssertionError(f"unexpected role {role!r} for this scenario")


class TestFixRoleFailedTerminalStatus(unittest.TestCase):
    def test_normal_failed_report_stamps_terminal_status_and_is_clearable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=str(journal_path),
                run_id="test-fix-role-failed",
                spawn=ReviewFailsThenFixDeclines(),
            )

            journal = json.loads(journal_path.read_text())
            fix_entries = [e for e in journal["entries"] if e.get("role") == "fix"]
            self.assertEqual(len(fix_entries), 1, "expected exactly one fix-role entry")
            self.assertEqual(
                fix_entries[0]["report"].get("terminal_status"),
                "failed",
                "the fix-role entry that terminates the task 'failed' must carry "
                "terminal_status='failed' so clear_tasks() can find it",
            )

            # The bug's own symptom: clear_tasks() must be able to find and
            # remove this entry -- not refuse with "nothing to clear".
            self.assertEqual(
                live.clear_tasks(repo, "docs/specs/001-x", ["TASK-001"]),
                0,
                "clear_tasks() must succeed for a task that failed via a normal "
                "(non-escalated) terminal report",
            )
            cleared_journal = json.loads(journal_path.read_text())
            remaining_failed = [
                e
                for e in cleared_journal["entries"]
                if e.get("task") == "TASK-001"
                and (e.get("report") or {}).get("terminal_status") == "failed"
            ]
            self.assertEqual(remaining_failed, [], "the failed entry should have been cleared")
            self.assertTrue(
                any(e.get("task") == "TASK-001" for e in cleared_journal["entries"]),
                "earlier successful TASK-001 history should be kept, not wiped",
            )


if __name__ == "__main__":
    unittest.main()
