#!/usr/bin/env python3
"""Regression tests for incomplete fan-out terminal state handling."""

import json
import tempfile
import unittest
from pathlib import Path

from worktrail.orchestrator import integrate
from worktrail.orchestrator import live
from worktrail.orchestrator import progress


class IncompleteTerminalState(unittest.TestCase):
    def test_partial_legacy_done_journal_renders_fanout_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "run-015.json"
            journal_path.write_text(
                json.dumps(
                    {
                        "spec_id": "015-cloudinary-to-sharp-migration",
                        "run_id": "full-1780600293",
                        "integrate_complete": True,
                        "entries": [
                            {
                                "task": "TASK-001",
                                "role": "implement",
                                "report": {
                                    "status": "success",
                                    "head_sha": "abc123",
                                    "tests": "passed",
                                    "review_status": None,
                                },
                                "started_at": 1.0,
                                "ended_at": 2.0,
                                "duration_s": 1.0,
                            }
                        ],
                    }
                )
            )
            progress.heartbeat_path(journal_path).write_text(
                json.dumps({"phase": "done", "run_id": "full-1780600293"})
            )
            tasks = [
                {"id": "TASK-001", "status": "pending", "retry_count": 0},
                {"id": "TASK-002", "status": "pending", "retry_count": 0},
            ]
            live.reconcile_from_journal(tasks, json.loads(journal_path.read_text()))

            rendered = progress.render(journal_path, tasks=tasks, now=10.0)

            self.assertIn("phase: fanout_failed", rendered)
            self.assertNotIn("phase: done", rendered)
            self.assertIn("0/2 tasks done", rendered)

    def test_terminal_failure_entry_reconciles_to_failed(self):
        tasks = [{"id": "TASK-001", "status": "pending", "retry_count": 0}]
        journal = {
            "entries": [
                live._journal_failure_entry(
                    {"id": "TASK-001"}, "review", "review timed out", 1.0, 2.0
                )
            ]
        }

        live.reconcile_from_journal(tasks, journal)

        self.assertEqual(tasks[0]["status"], "failed")

    def test_retryable_implement_parse_failure_retries_on_resume(self):
        tasks = [{"id": "TASK-001", "status": "pending", "retry_count": 0}]
        journal = {
            "entries": [
                live._journal_failure_entry(
                    {"id": "TASK-001"},
                    "implement",
                    "report parse failed: no report-back JSON block found",
                    1.0,
                    2.0,
                    terminal_status="retryable",
                )
            ]
        }

        live.reconcile_from_journal(tasks, journal)

        self.assertEqual(tasks[0]["status"], "pending")

    def test_validate_task_metadata_rejects_impl_without_files_or_deps(self):
        with self.assertRaisesRegex(RuntimeError, "TASK-001"):
            live.validate_task_metadata(
                [{"id": "TASK-001", "status": "pending", "kind": "impl", "files": [], "deps": []}]
            )

    def test_validate_task_metadata_allows_tail_without_files(self):
        live.validate_task_metadata(
            [{"id": "TASK-999", "status": "pending", "kind": "cleanup", "files": []}]
        )

    def test_validate_task_metadata_allows_scope_less_task_serialized_behind_a_dep(self):
        """compile.py's own prompt tells the model an empty `files` list is "the safe
        answer" because the task stays "serialised behind its neighbours" --
        `runplan.apply_to_tasks` enforces exactly that by refusing to drop a baseline
        dependency edge when either endpoint lacks file scope. A task that kept that
        dependency edge is the case the prompt promises is safe and must not crash the
        run; a task with neither files nor a dependency boundary has no such guarantee
        and must still be rejected (covered above)."""
        live.validate_task_metadata(
            [
                {"id": "TASK-001", "status": "done", "kind": "impl", "files": ["a.py"]},
                {
                    "id": "TASK-002",
                    "status": "pending",
                    "kind": "impl",
                    "files": [],
                    "deps": ["TASK-001"],
                },
            ]
        )

    def test_validate_task_metadata_rejects_pending_same_file_siblings_with_no_order(self):
        """go-20260805-172326: a compiled plan left two sibling tasks both declaring
        the same file with no dependency between them. `runnable_frontier`'s per-tick
        file lock happens to serialise them anyway at runtime, but this is the graph-
        level assertion that used to depend on a human eyeballing the printed dep
        table before launch."""
        with self.assertRaisesRegex(RuntimeError, r"shared\.py.*TASK-001.*TASK-002"):
            live.validate_task_metadata(
                [
                    {"id": "TASK-000", "status": "done", "kind": "impl", "files": ["a.py"]},
                    {
                        "id": "TASK-001",
                        "status": "pending",
                        "kind": "impl",
                        "files": ["shared.py"],
                        "deps": ["TASK-000"],
                    },
                    {
                        "id": "TASK-002",
                        "status": "pending",
                        "kind": "impl",
                        "files": ["shared.py"],
                        "deps": ["TASK-000"],
                    },
                ]
            )

    def test_validate_task_metadata_allows_same_file_tasks_ordered_by_a_dep(self):
        live.validate_task_metadata(
            [
                {"id": "TASK-001", "status": "pending", "kind": "impl", "files": ["shared.py"]},
                {
                    "id": "TASK-002",
                    "status": "pending",
                    "kind": "impl",
                    "files": ["shared.py"],
                    "deps": ["TASK-001"],
                },
            ]
        )

    def test_validate_task_metadata_ignores_a_collision_where_both_tasks_are_already_done(self):
        """Nothing left to protect: both writers already ran."""
        live.validate_task_metadata(
            [
                {"id": "TASK-001", "status": "done", "kind": "impl", "files": ["shared.py"]},
                {"id": "TASK-002", "status": "done", "kind": "impl", "files": ["shared.py"]},
            ]
        )


class IntegrateCompleteMarker(unittest.TestCase):
    def test_integrate_complete_not_written_until_all_groups_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "run.json"
            journal_path.write_text(
                json.dumps({"groups": {"base": {"state": "OPEN", "pr_url": "u"}}})
            )

            complete = integrate._mark_integrate_complete_if_terminal(
                str(journal_path),
                [{"name": "base"}, {"name": "feature-1"}],
            )

            self.assertFalse(complete)
            self.assertNotIn("integrate_complete", json.loads(journal_path.read_text()))

    def test_integrate_complete_written_when_all_groups_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal_path = Path(tmp) / "run.json"
            journal_path.write_text(
                json.dumps(
                    {
                        "groups": {
                            "base": {"state": "MERGED", "pr_url": "u"},
                            "feature-1": {"state": "QUARANTINED", "pr_url": ""},
                        }
                    }
                )
            )

            complete = integrate._mark_integrate_complete_if_terminal(
                str(journal_path),
                [{"name": "base"}, {"name": "feature-1"}],
            )

            self.assertTrue(complete)
            self.assertTrue(json.loads(journal_path.read_text())["integrate_complete"])


if __name__ == "__main__":
    unittest.main()
