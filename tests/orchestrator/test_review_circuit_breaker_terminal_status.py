#!/usr/bin/env python3
"""Regression for brief 20260816-201126: the review 3-strikes circuit breaker
(dispatch.transition returning "escalated") was computed in-memory only --
_commit_step never stamped report.terminal_status="escalated" onto the entry
that actually trips the breaker, only onto downstream dependency-gate entries
it blocks. clear_tasks()'s literal terminal_status match then found nothing
for the escalated task and refused with "nothing to clear", forcing --fresh
(full journal discard) as the only workaround.

Uses an injected AlwaysFailReviewSpawn (no real claude -p) against a throwaway
git repo so the test is hermetic and fast.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import (
    live,
    spawnlib,
)


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
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


def _commit_file(wt: Path, name: str, content: str) -> str:
    f = Path(wt) / "src" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    subprocess.run(["git", "-C", str(wt), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(wt), "commit", "-q", "-m", name],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(wt), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _report(
    task_id: str, role: str, sha: str, *, review_status=None
) -> spawnlib.SpawnResult:
    rs = f'"{review_status}"' if review_status else "null"
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"{role}","status":"success",'
            f'"head_sha":"{sha[:8]}","review_status":{rs}}}\n```'
        ),
        usage={},
    )


class AlwaysFailReviewSpawn:
    """Every review comes back FAILED so the 3-strikes circuit breaker trips
    naturally (dispatch.transition -> "escalated") instead of being injected
    as a synthetic journal entry."""

    def __init__(self):
        self._commit_count = 0

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
        ).stdout.strip()
        if role in ("implement", "fix"):
            self._commit_count += 1
            sha = _commit_file(wt, "foo.py", f"{role}-{self._commit_count}\n")
        if role == "review":
            return _report(task["id"], role, sha, review_status="FAILED")
        return _report(task["id"], role, sha)


class TestReviewCircuitBreakerTerminalStatus(unittest.TestCase):
    def test_naturally_escalated_task_stamps_terminal_status_and_is_clearable(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            # clear_tasks() has no explicit-path override -- it always resolves
            # the journal via journal_path_for()'s fixed convention, so the
            # cassette written here must match it exactly for the two halves
            # of this test (natural escalation, then clear_tasks()) to agree
            # on which file they're looking at.
            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=str(journal_path),
                run_id="test-circuit-breaker",
                spawn=AlwaysFailReviewSpawn(),
            )

            journal = json.loads(journal_path.read_text())
            review_entries = [
                e for e in journal["entries"] if e.get("role") == "review"
            ]
            self.assertEqual(len(review_entries), 3, "expected exactly 3 strikes")
            escalating_entry = review_entries[-1]
            self.assertEqual(
                escalating_entry["report"].get("terminal_status"),
                "escalated",
                "the review entry that trips the circuit breaker must carry "
                "terminal_status=escalated so clear_tasks() can find it",
            )

            # The bug's own symptom: clear_tasks() must be able to find and
            # remove this entry -- not refuse with "nothing to clear".
            self.assertEqual(
                live.clear_tasks(repo, "docs/specs/001-x", ["TASK-001"]),
                0,
                "clear_tasks() must succeed for a naturally-escalated task",
            )
            # Only the terminal-failure (escalated) entry is removed -- earlier
            # successful implement/review/fix history for TASK-001 is kept, so
            # a resume continues from where it broke rather than from scratch
            # (clear_tasks()'s documented contract, distinct from --fresh).
            cleared_journal = json.loads(journal_path.read_text())
            remaining_escalated = [
                e
                for e in cleared_journal["entries"]
                if e.get("task") == "TASK-001"
                and (e.get("report") or {}).get("terminal_status") == "escalated"
            ]
            self.assertEqual(
                remaining_escalated,
                [],
                "the escalated entry should have been cleared",
            )
            self.assertTrue(
                any(e.get("task") == "TASK-001" for e in cleared_journal["entries"]),
                "earlier successful TASK-001 history should be kept, not wiped",
            )


if __name__ == "__main__":
    unittest.main()
