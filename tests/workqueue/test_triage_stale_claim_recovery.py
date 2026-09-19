#!/usr/bin/env python3
"""An interrupted `queue_triage apply` must not wedge its brief.

Brief 20260918-180921: an apply of 20260918-154644 was interrupted right after
`openspec new change`. The brief stayed in picked/ with `claimed-by:
queue-triage`, no live process, no PR and a scaffold-only worktree -- and every
retry answered "brief already actioned by a concurrent triage run" though no
concurrent run existed. Recovery took three manual commands
(`worktrail-work-queue release --by queue-triage`, `git worktree remove
--force`, `git branch -D`).

The worktree/branch teardown half already landed (`_worktree_pr_close`'s
`finally:`). This covers the other half: telling a live owner from a dead one,
reclaiming only on positive evidence, and never asserting a concurrent run
that cannot be confirmed.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from worktrail.workqueue import queue_triage
from worktrail.workqueue import work_queue as q


def _brief(brief_id: str, focus: str = "something to do") -> str:
    return (
        "---\n"
        f"id: {brief_id}\n"
        f"focus: {focus}\n"
        "status: queued\n"
        "---\n\n"
        f"## Focus\n\n{focus}\n"
    )


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    pid = proc.pid
    proc.wait()
    return pid


class StaleClaimRecoveryTests(unittest.TestCase):
    BRIEF_ID = "20260918-154644-interrupted-apply"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        os.environ["WORK_QUEUE_DIR"] = str(self.base)
        self.addCleanup(lambda: os.environ.pop("WORK_QUEUE_DIR", None))
        importlib.reload(q)
        importlib.reload(queue_triage)
        self.addCleanup(importlib.reload, queue_triage)
        (self.base / "queue").mkdir(parents=True)
        (self.base / "queue" / f"{self.BRIEF_ID}.md").write_text(
            _brief(self.BRIEF_ID), encoding="utf-8"
        )

    def _interrupt_after_claim(self) -> Path:
        """Reproduce the abandoned state: claimed, then the process is gone."""
        res = q.claim(self.BRIEF_ID, by="queue-triage")
        self.assertEqual(res["status"], "claimed")
        path = Path(res["path"])
        q._set_fm_fields(path, {"claimed-by-pid": str(_dead_pid())})
        return path

    def test_stale_claim_is_reclaimed_not_reported_as_concurrent(self):
        self._interrupt_after_claim()
        res = queue_triage._claim_or_reclaim_stale(self.BRIEF_ID, by="queue-triage")
        self.assertEqual(res["status"], "claimed")
        self.assertTrue(res["reclaimed_stale"])
        fm = q._read_frontmatter(Path(res["path"]))
        self.assertEqual(fm["status"], "picked")
        self.assertEqual(int(fm["claimed-by-pid"]), os.getpid())

    def test_live_owner_is_still_refused_and_says_so(self):
        """The duplicate-PR guard this claim exists for must not weaken: a
        claim held by a running process is still refused."""
        q.claim(self.BRIEF_ID, by="queue-triage")  # stamped with THIS live pid
        res = queue_triage._claim_or_reclaim_stale(self.BRIEF_ID, by="queue-triage")
        self.assertEqual(res["status"], "already-claimed")
        self.assertIn("concurrent triage run", res["detail"])

    def test_undeterminable_owner_names_the_recovery_command(self):
        """A brief claimed before the pid stamp existed, or on another host:
        never silently reclaimed, and never described as concurrent either."""
        res = q.claim(self.BRIEF_ID, by="queue-triage")
        q._set_fm_fields(
            Path(res["path"]),
            {"claimed-by-host": "some-other-box", "claimed-by-pid": "424242"},
        )
        out = queue_triage._claim_or_reclaim_stale(self.BRIEF_ID, by="queue-triage")
        self.assertEqual(out["status"], "already-claimed")
        self.assertNotIn("concurrent triage run", out["detail"])
        self.assertIn("worktrail-work-queue release --by queue-triage", out["detail"])
        # and it stayed claimed -- nothing was reclaimed on a guess
        self.assertTrue((self.base / "picked" / f"{self.BRIEF_ID}.md").exists())

    def test_unclaimed_brief_claims_normally(self):
        res = queue_triage._claim_or_reclaim_stale(self.BRIEF_ID, by="queue-triage")
        self.assertEqual(res["status"], "claimed")
        self.assertNotIn("reclaimed_stale", res)


if __name__ == "__main__":
    unittest.main(verbosity=2)
