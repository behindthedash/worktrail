#!/usr/bin/env python3
"""Offline replay of the recorded live evaluator run.

`tests/fixtures/triage_evaluator_answers.json` is produced by hand with
`python3 -m worktrail.workqueue.triage_smoke` (see that module's docstring).
This file replays it: the recorded `raw_text` goes through `parse_verdicts()`
and `apply_verdicts()` against a real brief carrying the focus the evaluator
was actually shown. No network, no credential, no agent spawn -- the only
live thing here was the recording, and that already happened.

Run: python3 -m pytest tests/workqueue/test_queue_triage_live_replay.py -q
"""

from __future__ import annotations

import datetime
import json
from dataclasses import replace
from pathlib import Path

from worktrail.shared.brief_frontmatter import read_frontmatter
from worktrail.workqueue import queue_triage as qt

from .test_work_queue import QueueTestBase, _brief

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "triage_evaluator_answers.json"
)


def _recording() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class LiveReplayBase(QueueTestBase):
    def setUp(self):
        super().setUp()
        self.rec = _recording()
        self.brief_id = self.rec["brief_id"]
        self.focus = self.rec["focus"]
        # JSON-quote the focus into the frontmatter: it opens with a backtick,
        # a reserved YAML indicator, exactly as `triage_smoke` writes it.
        self.path = self.queue / f"{self.brief_id}.md"
        self.path.write_text(_brief(focus=json.dumps(self.focus)), encoding="utf-8")
        self.assertEqual(qt._brief_focus(self.path), self.focus)

    def verdict(self) -> qt.Verdict:
        verdicts = qt.parse_verdicts(self.rec["raw_text"], [self.brief_id])
        self.assertEqual(len(verdicts), 1)
        return verdicts[0]


class TestRecordingProvenance(LiveReplayBase):
    def test_meta_names_an_agent_and_a_capture_date(self):
        meta = self.rec["_meta"]
        self.assertTrue(meta.get("agent"))
        # `fromisoformat` raises on anything that is not a real date, so a
        # hand-authored fixture with a missing or placeholder `captured` fails.
        datetime.date.fromisoformat(meta["captured"])


class TestRecordedVerdictReplay(LiveReplayBase):
    def test_recorded_run_is_a_span_correcting_work_directly(self):
        v = self.verdict()
        self.assertEqual(v.verdict, "work-directly")
        self.assertTrue(v.refuted_span)
        self.assertIn(v.refuted_span, self.focus)

    def test_apply_rewrites_the_refuted_span_and_appends_a_triage_section(self):
        v = self.verdict()
        entries = qt.apply_verdicts([v], confirm=True)
        self.assertEqual(entries[0]["status"], "executed")
        self.assertEqual(entries[0]["action"], "stamp-frontmatter")

        new_focus = qt._brief_focus(self.path)
        self.assertNotIn(v.refuted_span, new_focus)
        if v.corrected_span:
            self.assertIn(v.corrected_span, new_focus)
        self.assertTrue(new_focus.strip())

        content = self.path.read_text(encoding="utf-8")
        self.assertRegex(content, qt._TRIAGE_HEADING_RE)
        self.assertIn(qt._focus_rewrite_summary(v), content)
        self.assertEqual(
            read_frontmatter(self.path).get("recommended-route"),
            "F",
        )


class TestWholeFocusDowngrade(LiveReplayBase):
    def test_a_span_covering_the_whole_focus_downgrades_to_keep(self):
        # Same recorded verdict, widened: the evaluator refutes all of it, so
        # there is nothing left to work on directly.
        v = replace(self.verdict(), refuted_span=self.focus, corrected_span=None)
        entries = qt.apply_verdicts([v], confirm=True)
        self.assertEqual(entries[0]["status"], "downgraded-to-keep")
        self.assertEqual(entries[0]["action"], "noop")
        self.assertEqual(entries[0]["note"], qt._work_directly_whole_focus_note(v))
        self.assertEqual(qt._brief_focus(self.path), self.focus)
        self.assertIsNone(read_frontmatter(self.path).get("seeded-from"))
