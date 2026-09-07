"""End-to-end regression for the 2026-08-18 comma-joined `blocked-by` incident.

A brief whose single `blocked-by` item comma-joined three prerequisite IDs read
as an ordinary `blocked: True`, with no signal anywhere that the value could
never resolve. This drives the *real* `list_queue()` output (against a real
`$WORK_QUEUE_DIR` on disk) into the *real* `auto_pick_brief()` -- no
hand-shaped brief dicts -- and pins the whole chain: the malformed brief is
skipped with the dependency-qualified reason instead of being picked, the clean
brief is the pick, and diagnosing all of that never touches the stored file.

Run: python3 -m pytest tests/router/test_auto_pick_dependency_diagnostics_e2e.py -q
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router import dashboard
from worktrail.workqueue import work_queue as q

MALFORMED = "20260818-000002-malformed.md"
CLEAN = "20260818-000003-clean.md"
PREREQ = "20260818-000001-prereq.md"


class TestAutoPickDependencyDiagnosticsE2E(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repos" / "target-repo"
        self.repo.mkdir(parents=True)
        self.queue = self.base / "wq" / "queue"
        self.queue.mkdir(parents=True)
        self._env = patch.dict(os.environ, {"WORK_QUEUE_DIR": str(self.base / "wq")})
        self._env.start()
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)

        # The still-queued prerequisite: an untriaged intake brief (no
        # `seeded-from:`), so it blocks others without being auto-pickable
        # itself.
        self._write(
            PREREQ,
            f"id: {PREREQ.replace('.md', '')}\nrepo: {self.repo}\nstatus: queued\n",
        )
        self._write(
            CLEAN,
            f"id: {CLEAN.replace('.md', '')}\nrepo: {self.repo}\n"
            "seeded-from: docs/specs/001\nstatus: queued\n",
        )
        # The incident shape (ranked ahead of the clean brief by FIFO, so a
        # missed skip would show up as a wrong pick): one list item comma-joining
        # the still-queued
        # prerequisite's id with two others.
        self.malformed_path = self._write(
            MALFORMED,
            f"id: {MALFORMED.replace('.md', '')}\nrepo: {self.repo}\n"
            "seeded-from: docs/specs/002\nstatus: queued\n"
            "blocked-by:\n"
            f"  - {PREREQ.replace('.md', '')}, other-dep, third-dep\n",
        )
        self.malformed_bytes = self.malformed_path.read_bytes()

    def _write(self, name: str, fm: str) -> Path:
        p = self.queue / name
        p.write_text(f"---\n{fm}---\n\n## Focus\n\nwork\n", encoding="utf-8")
        return p

    def _briefs(self) -> list[dict]:
        return q.list_queue()["briefs"]

    def test_malformed_brief_is_skipped_and_clean_brief_is_picked(self):
        briefs = self._briefs()
        by_name = {b["filename"]: b for b in briefs}
        self.assertTrue(by_name[MALFORMED]["blocked"])
        self.assertEqual(
            [e["state"] for e in by_name[MALFORMED]["dependency_diagnostics"]],
            ["malformed"],
        )

        result = dashboard.auto_pick_brief(briefs, repos_root=self.repo.parent)

        self.assertEqual(result["pick"]["id"], CLEAN.replace(".md", ""))
        reasons = {s["id"]: s["reason"] for s in result["skipped"]}
        self.assertEqual(
            reasons[MALFORMED.replace(".md", "")], "blocked:malformed-dependency"
        )
        self.assertEqual(reasons[PREREQ.replace(".md", "")], "intake-untriaged")

    def test_human_list_output_names_the_brief_and_the_raw_value(self):
        out = io.StringIO()
        with patch("sys.stdout", out):
            self.assertEqual(q.main(["list"]), 0)
        text = out.getvalue()
        self.assertIn(MALFORMED, text)
        self.assertIn(f"{PREREQ.replace('.md', '')}, other-dep, third-dep", text)

    def test_stored_brief_is_untouched_by_listing_selection_and_output(self):
        briefs = self._briefs()
        dashboard.auto_pick_brief(briefs, repos_root=self.repo.parent)
        with patch("sys.stdout", io.StringIO()):
            self.assertEqual(q.main(["list"]), 0)

        self.assertTrue(self.malformed_path.is_file())
        self.assertEqual(self.malformed_path.read_bytes(), self.malformed_bytes)
        self.assertEqual(
            sorted(p.name for p in self.queue.iterdir()),
            sorted([PREREQ, CLEAN, MALFORMED]),
        )
        self.assertFalse((self.base / "wq" / "picked").exists())


if __name__ == "__main__":
    unittest.main()
