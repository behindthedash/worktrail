#!/usr/bin/env python3
"""A relaunch must never fan a second worker into an occupied worktree.

Brief 20260918-224345. Run go-20260918-182950 (2026-09-18): a
`worktrail-detach`-launched `full-real` exited rc=-15 fifteen minutes in while
its `claude -p` implement workers kept running, reparented to /init, and went
on to commit d36185f5 and efbdcea2 after the orchestrator was gone. The
RunLock releases on process exit, and the run journal had 0 entries, so an
immediate relaunch would have replayed tick 1 straight into worktrees those
live workers still held. The operator had to notice the orphans with `ps` and
wait.

The signal source of the SIGTERM is separately undiagnosed and is NOT what
this covers; this is the second ask -- making the relaunch safe.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from worktrail.orchestrator import live


class RefuseRelaunchOverLiveWorkersTests(unittest.TestCase):
    SPEC_ID = "intake-triage-evaluate-claim-guard"
    TASKS: ClassVar[list[dict]] = [{"id": "1.1"}, {"id": "3.1"}]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name) / "worktrail"
        self.repo.mkdir(parents=True)
        self.base = self.repo.parent / "worktrail-worktrees"

    def _make_worktrees(self) -> list[Path]:
        made = []
        for task in self.TASKS:
            wt = self.base / f"{self.SPEC_ID}-{task['id']}"
            wt.mkdir(parents=True)
            made.append(wt)
        return made

    def test_no_worktrees_on_disk_is_a_clean_launch(self):
        live._refuse_relaunch_over_live_workers(self.repo, self.SPEC_ID, self.TASKS)

    def test_worktrees_with_no_live_workers_is_a_clean_relaunch(self):
        self._make_worktrees()
        with patch(
            "worktrail.router.live_status.agent_workers_in_worktrees", return_value=[]
        ):
            live._refuse_relaunch_over_live_workers(self.repo, self.SPEC_ID, self.TASKS)

    def test_a_live_worker_refuses_the_relaunch_and_names_it(self):
        worktrees = self._make_worktrees()
        held = [
            {"pid": 3541999, "argv": ["claude", "-p"], "worktree": str(worktrees[0])}
        ]
        with (
            patch(
                "worktrail.router.live_status.agent_workers_in_worktrees",
                return_value=held,
            ),
            self.assertRaises(live.OrphanedWorkersHoldWorktreesError) as ctx,
        ):
            live._refuse_relaunch_over_live_workers(self.repo, self.SPEC_ID, self.TASKS)
        message = str(ctx.exception)
        self.assertIn("3541999", message)
        self.assertIn(worktrees[0].name, message)
        self.assertIn("second worker on the same checkout", message)

    def test_only_worktrees_that_exist_are_probed(self):
        """Probing a path that was never created would be a wasted /proc scan
        on every clean launch."""
        (self.base / f"{self.SPEC_ID}-1.1").mkdir(parents=True)
        seen: dict[str, list] = {}

        def _spy(worktrees, *args, **kwargs):
            seen["worktrees"] = list(worktrees)
            return []

        with patch("worktrail.router.live_status.agent_workers_in_worktrees", _spy):
            live._refuse_relaunch_over_live_workers(self.repo, self.SPEC_ID, self.TASKS)
        self.assertEqual([p.name for p in seen["worktrees"]], [f"{self.SPEC_ID}-1.1"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
