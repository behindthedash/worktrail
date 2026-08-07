#!/usr/bin/env python3
"""Unit coverage for `live.journal_foreign_task_ids`.

Guards `full_real`/`_full_real_inner` resume against replaying a journal that
belongs to a different spec/change whose trailing path name collides with the
current one -- see `journal_foreign_task_ids`'s docstring in
`src/worktrail/orchestrator/live.py`.
"""

import os
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
