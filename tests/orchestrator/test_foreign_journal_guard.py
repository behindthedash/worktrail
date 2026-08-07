#!/usr/bin/env python3
"""Unit coverage for `live.journal_foreign_task_ids`.

Guards `full_real`/`_full_real_inner` resume against replaying a journal that
belongs to a different spec/change whose trailing path name collides with the
current one -- see `journal_foreign_task_ids`'s docstring in
`src/worktrail/orchestrator/live.py`.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402


class JournalForeignTaskIdsAllMatchTest(unittest.TestCase):
    """2.1: a journal whose entries' task ids all match the current task set
    resumes exactly as before -- no foreign ids are reported."""

    def test_all_matching_task_ids_yield_no_foreign_ids(self):
        tasks = [
            {"id": "1.1", "status": "done"},
            {"id": "1.2", "status": "done"},
            {"id": "2.1", "status": "pending"},
        ]
        entries = [
            {"task": "1.1", "report": {"head_sha": "aaa111"}},
            {"task": "1.2", "report": {"head_sha": "bbb222"}},
        ]

        foreign = live.journal_foreign_task_ids(entries, tasks)

        self.assertEqual(foreign, set())

    def test_repeated_matching_task_id_still_yields_no_foreign_ids(self):
        tasks = [{"id": "1.1", "status": "done"}]
        entries = [
            {"task": "1.1", "report": {"head_sha": "aaa111"}},
            {"task": "1.1", "report": {"head_sha": "ccc333"}},
        ]

        foreign = live.journal_foreign_task_ids(entries, tasks)

        self.assertEqual(foreign, set())

    def test_empty_journal_yields_no_foreign_ids(self):
        tasks = [{"id": "1.1", "status": "pending"}]

        foreign = live.journal_foreign_task_ids([], tasks)

        self.assertEqual(foreign, set())


class JournalForeignTaskIdsForeignMatchTest(unittest.TestCase):
    """2.2 (helper level): entries whose task ids are absent from the current
    task set are reported as foreign."""

    def test_foreign_task_id_is_reported(self):
        tasks = [{"id": "1.1", "status": "done"}]
        entries = [{"task": "9.9", "report": {"head_sha": "deadbee"}}]

        foreign = live.journal_foreign_task_ids(entries, tasks)

        self.assertEqual(foreign, {"9.9"})

    def test_multiple_foreign_task_ids_are_all_reported(self):
        tasks = [{"id": "1.1", "status": "done"}]
        entries = [
            {"task": "9.9", "report": {"head_sha": "deadbee"}},
            {"task": "9.10", "report": {"head_sha": "feedbee"}},
        ]

        foreign = live.journal_foreign_task_ids(entries, tasks)

        self.assertEqual(foreign, {"9.9", "9.10"})


class JournalForeignTaskIdsMixedMatchTest(unittest.TestCase):
    """2.3 (helper level): a journal with a mix of matching and foreign task
    ids still reports the foreign ones -- partial collision is not treated
    as safe, and matching entries are not allowed to mask foreign ones."""

    def test_mixed_matching_and_foreign_task_ids_reports_only_foreign_ones(self):
        tasks = [{"id": "1.1", "status": "done"}, {"id": "1.2", "status": "pending"}]
        entries = [
            {"task": "1.1", "report": {"head_sha": "aaa111"}},
            {"task": "9.9", "report": {"head_sha": "deadbee"}},
        ]

        foreign = live.journal_foreign_task_ids(entries, tasks)

        self.assertEqual(foreign, {"9.9"})


class FullRealInnerForeignJournalRaisesTest(unittest.TestCase):
    """2.2: a journal with one or more entries whose task ids are absent from
    the current task set raises the foreign-journal error at the
    `_full_real_inner` resume block (spec-path-task-crosscheck 1.2), naming
    every foreign id, the journal path, and the `--spec` path, and telling the
    operator to re-run with `--fresh`. Nothing downstream (`live_run_real`,
    which would itself reconcile the real task list from this same journal)
    is ever reached -- the journal's foreign entries reconcile nothing."""

    def _run_and_capture_error(self, journal_entries, current_tasks, spec_rel):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = os.path.join(tmpdir, "journal.json")
            with open(journal_path, "w") as f:
                json.dump({"entries": journal_entries}, f)

            fake_git = MagicMock()
            fake_git.stdout = "dev"

            with patch("worktrail.orchestrator.live._git", return_value=fake_git), patch(
                "worktrail.orchestrator.live.journal_path_for", return_value=journal_path
            ), patch(
                "worktrail.orchestrator.live.taskformats.load_spec",
                return_value=("spec-id", current_tasks),
            ), patch(
                "worktrail.orchestrator.live.live_run_real"
            ) as mock_live_run_real:
                with self.assertRaises(RuntimeError) as ctx:
                    live._full_real_inner("/tmp/fake-repo", spec_rel, resume=True)

            mock_live_run_real.assert_not_called()
            return str(ctx.exception), journal_path

    def test_foreign_task_id_raises_runtime_error_naming_id_journal_and_spec(self):
        spec_rel = "docs/specs/spec-path-task-crosscheck"
        message, journal_path = self._run_and_capture_error(
            journal_entries=[{"task": "9.9", "role": "implement", "report": {"head_sha": "deadbee"}}],
            current_tasks=[{"id": "1.1", "status": "pending"}],
            spec_rel=spec_rel,
        )

        self.assertIn("9.9", message)
        self.assertIn(journal_path, message)
        self.assertIn(spec_rel, message)
        self.assertIn("--fresh", message)

    def test_multiple_foreign_task_ids_all_named_in_error(self):
        spec_rel = "docs/specs/spec-path-task-crosscheck"
        message, _ = self._run_and_capture_error(
            journal_entries=[
                {"task": "9.9", "role": "implement", "report": {"head_sha": "deadbee"}},
                {"task": "9.10", "role": "implement", "report": {"head_sha": "feedbee"}},
            ],
            current_tasks=[{"id": "1.1", "status": "pending"}],
            spec_rel=spec_rel,
        )

        self.assertIn("9.9", message)
        self.assertIn("9.10", message)

    def test_mixed_matching_and_foreign_task_ids_still_raises(self):
        """2.3: a journal with a mix of matching and foreign entries raises the
        same error -- a partial collision (some entries match the current task
        set) is not treated as safe, and nothing is reconciled from it."""
        spec_rel = "docs/specs/spec-path-task-crosscheck"
        message, journal_path = self._run_and_capture_error(
            journal_entries=[
                {"task": "1.1", "role": "implement", "report": {"head_sha": "aaa111"}},
                {"task": "9.9", "role": "implement", "report": {"head_sha": "deadbee"}},
            ],
            current_tasks=[{"id": "1.1", "status": "done"}],
            spec_rel=spec_rel,
        )

        self.assertIn("9.9", message)
        self.assertIn(journal_path, message)
        self.assertIn(spec_rel, message)
        self.assertIn("--fresh", message)


if __name__ == "__main__":
    unittest.main()
