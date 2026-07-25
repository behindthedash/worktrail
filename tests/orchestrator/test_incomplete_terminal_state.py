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

    def test_validate_task_metadata_rejects_impl_without_files(self):
        with self.assertRaisesRegex(RuntimeError, "TASK-001"):
            live.validate_task_metadata(
                [{"id": "TASK-001", "status": "pending", "kind": "impl", "files": []}]
            )

    def test_validate_task_metadata_allows_tail_without_files(self):
        live.validate_task_metadata(
            [{"id": "TASK-999", "status": "pending", "kind": "cleanup", "files": []}]
        )


class FanoutCompleteTailKinds(unittest.TestCase):
    """`full-real` dispatches the fan-out with with_tail=False, so tail-kind tasks
    (e2e, cleanup) stay `pending` by design. `_fanout_complete` must exclude them in
    that mode -- otherwise the run reports FAN-OUT INCOMPLETE forever and never
    integrates (the bug that stranded spec 015)."""

    def test_pending_tail_excluded_when_with_tail_false(self):
        tasks = [
            {"id": "TASK-001", "status": "done", "kind": "impl"},
            {"id": "TASK-002", "status": "pending", "kind": "e2e"},
            {"id": "TASK-003", "status": "pending", "kind": "cleanup"},
        ]
        self.assertTrue(live._fanout_complete(tasks, with_tail=False))
        # With tail in scope, those same pending tail tasks DO block completion.
        self.assertFalse(live._fanout_complete(tasks, with_tail=True))

    def test_pending_impl_blocks_completion_in_both_modes(self):
        tasks = [
            {"id": "TASK-001", "status": "pending", "kind": "impl"},
            {"id": "TASK-002", "status": "done", "kind": "e2e"},
        ]
        self.assertFalse(live._fanout_complete(tasks, with_tail=False))
        self.assertFalse(live._fanout_complete(tasks, with_tail=True))

    def test_pending_impl_depending_only_on_tail_is_held_out_in_fanout_mode(self):
        tasks = [
            {"id": "TASK-001", "status": "done", "kind": "impl"},
            {"id": "TASK-018", "status": "pending", "kind": "e2e"},
            {"id": "TASK-019", "status": "pending", "kind": "impl", "deps": ["TASK-018"]},
        ]
        self.assertTrue(live._fanout_complete(tasks, with_tail=False))
        self.assertFalse(live._fanout_complete(tasks, with_tail=True))
        detail = live._fanout_incomplete_detail(tasks, with_tail=False)
        self.assertEqual(detail["failed_tasks"], [])
        self.assertEqual(detail["blocked_tasks"], [])
        self.assertEqual(detail["summary"], [])

    def test_default_is_strict_all_terminal(self):
        # Default with_tail=True preserves the legacy strict check for any caller.
        self.assertFalse(
            live._fanout_complete([{"id": "T1", "status": "pending", "kind": "cleanup"}])
        )

    def test_all_terminal_complete_in_both_modes(self):
        tasks = [
            {"id": "TASK-001", "status": "done", "kind": "impl"},
            {"id": "TASK-002", "status": "failed", "kind": "impl"},
            {"id": "TASK-003", "status": "completed", "kind": "cleanup"},
        ]
        self.assertTrue(live._fanout_complete(tasks, with_tail=False))
        self.assertTrue(live._fanout_complete(tasks, with_tail=True))

    def test_incomplete_detail_reports_failed_root_cause_before_blocked_task(self):
        tasks = [
            {"id": "TASK-010", "status": "failed", "kind": "impl", "deps": []},
            {"id": "TASK-011", "status": "pending", "kind": "impl", "deps": ["TASK-010"]},
            {"id": "TASK-099", "status": "pending", "kind": "cleanup", "deps": []},
        ]

        detail = live._fanout_incomplete_detail(tasks, with_tail=False)

        self.assertEqual(
            detail["failed_tasks"],
            [{"id": "TASK-010", "status": "failed"}],
        )
        self.assertEqual(
            detail["blocked_tasks"],
            [{"id": "TASK-011", "status": "pending", "blocked_by": ["TASK-010"]}],
        )
        self.assertEqual(
            detail["summary"],
            ["TASK-010=failed", "TASK-011=pending (blocked by TASK-010)"],
        )

    def test_incomplete_detail_names_unresolved_external_dependency(self):
        """AC-014: a task blocked solely on an unresolved external-dependencies entry
        surfaces that reason through `_fanout_incomplete_detail`'s `blocked_tasks`,
        the same path an existing same-spec-failure-blocked task uses today."""
        tasks = [
            {
                "id": "TASK-020",
                "status": "pending",
                "kind": "impl",
                "deps": [],
                "external_deps": ["098-other-spec/TASK-005"],
                "external_deps_ok": False,
                "external_deps_blockers": [
                    "098-other-spec/TASK-005 unresolved (task file not found)"
                ],
            },
        ]

        detail = live._fanout_incomplete_detail(tasks, with_tail=False)

        self.assertEqual(detail["failed_tasks"], [])
        self.assertEqual(
            detail["blocked_tasks"],
            [
                {
                    "id": "TASK-020",
                    "status": "pending",
                    "blocked_by": ["098-other-spec/TASK-005 unresolved (task file not found)"],
                }
            ],
        )
        self.assertIn(
            "TASK-020=pending (blocked by 098-other-spec/TASK-005 unresolved "
            "(task file not found))",
            detail["summary"],
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


class StrandedAfterIntegrate(unittest.TestCase):
    """When the journal marks every recorded group integrated, `full-real` skips
    integrate. A task added to the spec and fanned out on that resume forms a group
    absent from the journal -- its work reaches no PR. `_stranded_after_integrate`
    must surface those tasks so the operator is told to re-run with --re-integrate."""

    def test_new_group_tasks_are_stranded(self):
        # TASK-003 has no deps -> its own feature group, absent from the journal.
        tasks = [
            {"id": "TASK-001", "status": "done", "kind": "impl"},
            {"id": "TASK-002", "status": "done", "kind": "impl"},
            {"id": "TASK-003", "status": "done", "kind": "impl"},
        ]
        journal_groups = {
            "feature-1": {"pr_url": "u1", "head_branch": "b1", "state": "MERGED"},
            "feature-2": {"pr_url": "u2", "head_branch": "b2", "state": "MERGED"},
        }
        self.assertEqual(live._stranded_after_integrate(tasks, journal_groups), ["TASK-003"])

    def test_no_stranded_when_every_group_in_journal(self):
        tasks = [
            {"id": "TASK-001", "status": "done", "kind": "impl"},
            {"id": "TASK-002", "status": "done", "kind": "impl"},
        ]
        journal_groups = {
            "feature-1": {"pr_url": "u1", "head_branch": "b1", "state": "MERGED"},
            "feature-2": {"pr_url": "u2", "head_branch": "b2", "state": "MERGED"},
        }
        self.assertEqual(live._stranded_after_integrate(tasks, journal_groups), [])

    def test_non_terminal_task_in_new_group_excluded(self):
        # A pending task in an un-integrated group is not yet stranded work.
        tasks = [
            {"id": "TASK-001", "status": "done", "kind": "impl"},
            {"id": "TASK-002", "status": "pending", "kind": "impl"},
        ]
        journal_groups = {
            "feature-1": {"pr_url": "u1", "head_branch": "b1", "state": "MERGED"},
        }
        self.assertEqual(live._stranded_after_integrate(tasks, journal_groups), [])

    def test_tail_tasks_never_counted(self):
        # Tail-kind tasks are held out of every group, so they can't be stranded.
        tasks = [
            {"id": "TASK-001", "status": "done", "kind": "impl"},
            {"id": "TASK-002", "status": "done", "kind": "cleanup"},
        ]
        journal_groups = {
            "feature-1": {"pr_url": "u1", "head_branch": "b1", "state": "MERGED"},
        }
        self.assertEqual(live._stranded_after_integrate(tasks, journal_groups), [])


if __name__ == "__main__":
    unittest.main()
