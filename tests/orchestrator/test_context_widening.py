#!/usr/bin/env python3
"""Integration test for adaptive context read-widening.

When a review worker reports context_quality=insufficient with non-empty
missing_context, drive() stages those paths as task["_extra_reads"] so
LiveSpawn injects them into the next fix prompt. This file verifies:

  1. insufficient review → missing_context reaches the fix dispatch
  2. sufficient review with populated missing_context → fix is NOT widened
  3. too_much review → fix is NOT widened

Uses an injected RecordingSpawn (no real claude -p) against a throwaway
git repo so tests are hermetic and fast.
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
    dispatch,
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
        ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
    ).stdout.strip()


def _report(
    task_id: str,
    role: str,
    sha: str,
    *,
    review_status=None,
    context_quality="sufficient",
    missing_context=None,
) -> spawnlib.SpawnResult:
    rs = f'"{review_status}"' if review_status else "null"
    mc = json.dumps(missing_context or [])
    return spawnlib.SpawnResult(
        text=(
            f'```json\n{{"task":"{task_id}","step":"{role}","status":"success",'
            f'"head_sha":"{sha[:8]}","review_status":{rs},'
            f'"context_quality":"{context_quality}","missing_context":{mc}}}\n```'
        ),
        usage={},
    )


class _BaseRecordingSpawn:
    """Base spawn that records what _extra_reads was set on the task at fix time."""

    def __init__(self):
        self.calls: list = []
        self.fix_extra_reads_seen: list = []

    def _review_count(self) -> int:
        return sum(1 for c in self.calls if c == "review")

    def __call__(self, role: str, task: dict, wt: Path) -> spawnlib.SpawnResult:
        self.calls.append(role)
        # Capture whatever drive() staged before the fix spawn
        if role == dispatch.ROLE_FIX:
            self.fix_extra_reads_seen = list(task.get("_extra_reads") or [])
        sha = subprocess.run(
            ["git", "-C", str(wt), "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        if role in ("implement", "fix"):
            sha = _commit_file(wt, "foo.py", f"{role}\n")
        return self._report_for(task["id"], role, sha)

    def _report_for(self, task_id: str, role: str, sha: str) -> spawnlib.SpawnResult:
        raise NotImplementedError


class InsufficientReviewSpawn(_BaseRecordingSpawn):
    """First review: FAILED with context_quality=insufficient + missing_context."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(
                task_id,
                role,
                sha,
                review_status="FAILED",
                context_quality="insufficient",
                missing_context=["src/helper.py", "tests/test_helper.py"],
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class SufficientReviewSpawn(_BaseRecordingSpawn):
    """First review: FAILED but context_quality=sufficient (missing_context populated but ignored)."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(
                task_id,
                role,
                sha,
                review_status="FAILED",
                context_quality="sufficient",
                missing_context=["src/helper.py"],
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class TooMuchReviewSpawn(_BaseRecordingSpawn):
    """First review: FAILED with context_quality=too_much."""

    def _report_for(self, task_id, role, sha):
        if role == "review" and self._review_count() == 1:
            return _report(
                task_id,
                role,
                sha,
                review_status="FAILED",
                context_quality="too_much",
                missing_context=["src/helper.py"],
            )
        if role == "review":
            return _report(task_id, role, sha, review_status="PASSED")
        return _report(task_id, role, sha)


class TestContextWidening(unittest.TestCase):
    def test_scope_escalation_journal_resumes_at_widened_fix(self):
        task = {"id": "TASK-001", "status": "pending", "files": ["src/foo.py"]}
        journal = {
            "entries": [
                {
                    "task": "TASK-001",
                    "role": "fix",
                    "report": {"status": "failed"},
                    "scope_escalated": True,
                    "scope_added_files": ["src/helper.py"],
                }
            ]
        }

        live.reconcile_from_journal([task], journal)

        self.assertEqual(task["status"], "fixing")
        self.assertEqual(task["files"], ["src/foo.py", "src/helper.py"])
        self.assertTrue(task["_scope_escalated"])

    def test_failed_fix_can_widen_once_to_existing_noncolliding_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            (wt / "src" / "helper.py").write_text("x\n")
            task = {"id": "TASK-001", "status": "fixing", "files": ["src/foo.py"]}
            report = {"status": "failed", "missing_context": ["src/helper.py"]}
            self.assertEqual(
                live._scope_escalation_files(task, report, wt, {task["id"]: task}),
                ["src/helper.py"],
            )
            task["_scope_escalated"] = True
            self.assertEqual(
                live._scope_escalation_files(task, report, wt, {task["id"]: task}), []
            )

    def test_scope_widening_rejects_inflight_file_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            (wt / "src" / "helper.py").write_text("x\n")
            task = {"id": "TASK-001", "status": "fixing", "files": ["src/foo.py"]}
            other = {
                "id": "TASK-002",
                "status": "implementing",
                "files": ["src/helper.py"],
            }
            report = {"status": "failed", "missing_context": ["src/helper.py"]}
            self.assertEqual(
                live._scope_escalation_files(
                    task, report, wt, {task["id"]: task, other["id"]: other}
                ),
                [],
            )

    def _run(self, spawn_cls):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal = str(Path(tmp) / "run-001-x.json")
            spawn = spawn_cls()
            live.live_run_real(
                repo,
                "docs/specs/001-x",
                max_workers=1,
                out_cassette=journal,
                run_id="test-widening",
                spawn=spawn,
            )
            return spawn

    def test_insufficient_review_widens_fix_reads(self):
        """context_quality=insufficient → missing_context staged for next fix dispatch."""
        spawn = self._run(InsufficientReviewSpawn)
        self.assertIn(
            "src/helper.py",
            spawn.fix_extra_reads_seen,
            "src/helper.py must be staged as extra read for fix after insufficient review",
        )
        self.assertIn(
            "tests/test_helper.py",
            spawn.fix_extra_reads_seen,
            "tests/test_helper.py must be staged as extra read for fix after insufficient review",
        )

    def test_sufficient_review_does_not_widen(self):
        """context_quality=sufficient must NOT widen fix reads, even if missing_context is set."""
        spawn = self._run(SufficientReviewSpawn)
        self.assertEqual(
            spawn.fix_extra_reads_seen,
            [],
            "sufficient context_quality must not stage extra reads for fix",
        )

    def test_too_much_review_does_not_widen(self):
        """context_quality=too_much must NOT widen fix reads."""
        spawn = self._run(TooMuchReviewSpawn)
        self.assertEqual(
            spawn.fix_extra_reads_seen,
            [],
            "too_much context_quality must not stage extra reads for fix",
        )


if __name__ == "__main__":
    unittest.main()
