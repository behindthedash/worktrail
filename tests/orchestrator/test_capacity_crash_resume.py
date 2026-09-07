#!/usr/bin/env python3
"""capacity-crash-resume-retryable 1.1: a drive() crash caused by capacity
exhaustion (`NoExecutionTarget` -- every routing cell gated) says nothing about
the task, so it must be journaled `terminal_status: "retryable"`. Replay then
leaves the task pending and the next resume re-dispatches it without `--fresh`.
Every other crash keeps today's non-retryable `"failed"`.
"""

import contextlib
import io
import json
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path

from worktrail.orchestrator import live
from worktrail.runtime.selection import InvalidCandidate, NoExecutionTarget


def _init_repo(root: Path) -> Path:
    repo = root / "repo"
    spec_dir = repo / "docs" / "specs" / "001-x" / "tasks"
    spec_dir.mkdir(parents=True)
    (spec_dir / "TASK-001.md").write_text(
        "---\nid: TASK-001\nstatus: pending\ndependencies: []\n"
        "files: [src/task-001.txt]\nkind: impl\nreview: skip\n---\nbody\n"
    )
    (repo / "README.md").write_text("x\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "init"],
        check=True,
        capture_output=True,
    )
    return repo


class _RaisingSpawn:
    def __init__(self, exc: BaseException):
        self._exc = exc

    def __call__(self, role, task, wt):
        raise self._exc


def _integrate_one(
    g,
    repo,
    spec_id,
    tasks,
    remote,
    run_id,
    base,
    journal_path,
    status,
    group_branch,
    quarantined,
    **kwargs,
):
    quarantined[g["name"]] = "no deliverable tasks"


class _FakeVerifier:
    def __init__(self):
        self._lock = threading.Lock()

    def verify_one(
        self,
        group,
        group_branch_ref,
        delivered,
        merged,
        quarantined,
        lock,
        self_merged=None,
        armed=None,
        post_merge_regressed=None,
    ):
        with lock:
            quarantined[group["name"]] = "not integrated"


def _drive_entries(journal_path: Path) -> list:
    journal = json.loads(Path(journal_path).read_text())
    return [e for e in journal["entries"] if e.get("role") == "drive"]


class CrashTerminalStatusTest(unittest.TestCase):
    def test_no_execution_target_is_retryable(self):
        self.assertEqual(
            live._crash_terminal_status(
                NoExecutionTarget([("claude", "haiku", "gated")])
            ),
            "retryable",
        )

    def test_subclass_of_no_execution_target_is_retryable(self):
        class _Subclass(NoExecutionTarget):
            pass

        self.assertEqual(
            live._crash_terminal_status(_Subclass([("claude", "haiku", "gated")])),
            "retryable",
        )

    def test_invalid_candidate_is_failed(self):
        self.assertEqual(live._crash_terminal_status(InvalidCandidate("bad")), "failed")

    def test_plain_runtime_error_is_failed(self):
        self.assertEqual(live._crash_terminal_status(RuntimeError("boom")), "failed")


class LiveRunRealCrashJournalTest(unittest.TestCase):
    def _run(self, exc):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = live.journal_path_for(repo, "docs/specs/001-x")
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                live.live_run_real(
                    repo,
                    "docs/specs/001-x",
                    max_workers=1,
                    out_cassette=str(journal_path),
                    run_id="test-capacity-crash",
                    spawn=_RaisingSpawn(exc),
                )
            return _drive_entries(journal_path), buf.getvalue()

    def test_capacity_crash_journals_retryable_and_prints_no_capacity(self):
        entries, out = self._run(NoExecutionTarget([("claude", "haiku", "gated")]))
        self.assertEqual(len(entries), 1, entries)
        report = entries[0]["report"]
        self.assertEqual(report["terminal_status"], "retryable")
        self.assertTrue(report["notes"].startswith("drive crashed:"), report["notes"])
        self.assertIn("no capacity:", out)
        self.assertIn("will re-dispatch on resume", out)
        self.assertNotIn("-- marking failed", out)

    def test_ordinary_crash_journals_failed_and_prints_marking_failed(self):
        entries, out = self._run(RuntimeError("boom"))
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["report"]["terminal_status"], "failed")
        self.assertTrue(
            entries[0]["report"]["notes"].startswith("drive crashed:"),
            entries[0]["report"]["notes"],
        )
        self.assertIn("-- marking failed", out)
        self.assertNotIn("no capacity:", out)


class PipelineSchedulerCrashJournalTest(unittest.TestCase):
    def _run(self, exc):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo(Path(tmp))
            journal_path = Path(tmp) / "pipeline-journal.json"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                live._pipeline_scheduler(
                    repo=repo,
                    spec_rel="docs/specs/001-x",
                    remote="origin",
                    base="main",
                    model="haiku",
                    max_workers=1,
                    timeout=30,
                    resume=False,
                    only=None,
                    role_models=None,
                    run_budget=None,
                    journal_path=str(journal_path),
                    run_id="test-capacity-crash-pipeline",
                    _spawn=_RaisingSpawn(exc),
                    _integrate_one=_integrate_one,
                    _make_verifier=_FakeVerifier,
                )
            return _drive_entries(journal_path), buf.getvalue()

    def test_capacity_crash_is_retryable(self):
        entries, out = self._run(NoExecutionTarget([("claude", "haiku", "gated")]))
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["report"]["terminal_status"], "retryable")
        self.assertIn("no capacity:", out)

    def test_ordinary_crash_is_failed(self):
        entries, out = self._run(RuntimeError("boom"))
        self.assertEqual(len(entries), 1, entries)
        self.assertEqual(entries[0]["report"]["terminal_status"], "failed")
        self.assertIn("-- marking failed", out)


class ReplayOfCrashEntryTest(unittest.TestCase):
    def _replayed_status(self, terminal_status: str) -> str:
        tasks = [{"id": "TASK-001", "status": "pending", "dependencies": []}]
        journal = {
            "entries": [
                live._journal_failure_entry(
                    tasks[0],
                    "drive",
                    "drive crashed: RuntimeError('boom')",
                    1.0,
                    2.0,
                    terminal_status=terminal_status,
                )
            ]
        }
        live.reconcile_from_journal(tasks, journal)
        return tasks[0]["status"]

    def test_retryable_drive_entry_leaves_task_pending(self):
        self.assertEqual(self._replayed_status("retryable"), "pending")

    def test_failed_drive_entry_leaves_task_failed(self):
        self.assertEqual(self._replayed_status("failed"), "failed")


if __name__ == "__main__":
    unittest.main()
