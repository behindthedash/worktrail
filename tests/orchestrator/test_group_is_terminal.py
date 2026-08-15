#!/usr/bin/env python3
"""`group_is_terminal` must fail CLOSED on group-membership drift.

Regression coverage for brief 20260815-115257. The pipeline scheduler hands a
group to the integrate/verify pool as soon as `group_is_terminal()` says every
member has reached a terminal status. The pre-fix form filtered the
comprehension with `if tid in by_id`, so a member absent from the run's task
table was silently skipped -- and `all()` over the surviving subset returns
True. A group could be declared terminal, and integrated, while tasks it
nominally owns were still running.

That drift is real. `plan_groups()` derives grouping from each task's
`deps`/`files`, and compiling the same spec twice can produce different values
for both: run full-1786812908 left two compiled plans 108s apart in
`runplans/` that disagreed on `deps`, `files`, and even `kind`, yielding
`base = [1.1, 1.2, 1.3, 1.4, 2.1, ...]` under one and `base = [1.1, 1.2, 1.3]`
under the other.

Run: python3 -m pytest tests/orchestrator/test_group_is_terminal.py
"""

from __future__ import annotations

import unittest

from worktrail.orchestrator import coordinator
from worktrail.orchestrator.live import group_is_terminal

TERMINAL = coordinator.DONE | coordinator.FAILED_STATUSES


def _group(name, tasks):
    return {"name": name, "tasks": tasks, "depends_on": []}


class GroupIsTerminal(unittest.TestCase):
    def test_all_members_terminal_is_terminal(self):
        by_id = {"1.1": {"status": "done"}, "1.2": {"status": "completed"}}
        self.assertTrue(group_is_terminal(_group("base", ["1.1", "1.2"]), by_id, TERMINAL))

    def test_failed_member_still_counts_as_terminal(self):
        """A failed task is terminal -- integrate_one's own SPLIT/quarantine
        path is what decides what to do about it. This guards against the
        fail-closed change accidentally stalling groups containing failures."""
        by_id = {"1.1": {"status": "done"}, "1.2": {"status": "failed"}}
        self.assertTrue(group_is_terminal(_group("base", ["1.1", "1.2"]), by_id, TERMINAL))

    def test_in_flight_member_is_not_terminal(self):
        by_id = {"1.1": {"status": "done"}, "1.2": {"status": "implementing"}}
        self.assertFalse(group_is_terminal(_group("base", ["1.1", "1.2"]), by_id, TERMINAL))

    def test_member_missing_from_by_id_is_not_terminal(self):
        """THE DEFECT: the pre-fix form skipped '1.3' entirely and returned
        True off the surviving {1.1, 1.2} subset -- the group was integrated
        without it, producing PR #419's 'base: 1.1, 1.2'."""
        by_id = {"1.1": {"status": "done"}, "1.2": {"status": "done"}}
        self.assertFalse(
            group_is_terminal(_group("base", ["1.1", "1.2", "1.3"]), by_id, TERMINAL)
        )

    def test_every_member_missing_is_not_terminal(self):
        """`all()` over an empty sequence is True -- the degenerate case of the
        same bug, where a group whose membership is entirely unknown would read
        as fully delivered."""
        self.assertFalse(group_is_terminal(_group("base", ["1.1", "1.2"]), {}, TERMINAL))

    def test_empty_group_is_terminal(self):
        """A group that genuinely claims no tasks is vacuously terminal --
        distinct from one whose claimed members are unresolvable."""
        self.assertTrue(group_is_terminal(_group("base", []), {}, TERMINAL))

    def test_missing_member_is_reported_not_silent(self):
        """Failing closed is only half the fix; the drift has to be visible.
        This was invisible enough that reconstructing it required noticing two
        files in `runplans/`."""
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            group_is_terminal(_group("base", ["1.1", "9.9"]), {"1.1": {"status": "done"}}, TERMINAL)
        out = buf.getvalue()
        self.assertIn("9.9", out)
        self.assertIn("base", out)


if __name__ == "__main__":
    unittest.main()
