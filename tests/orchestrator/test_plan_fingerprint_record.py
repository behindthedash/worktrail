#!/usr/bin/env python3
"""The run journal records which compiled RunPlan each run executed.

Regression coverage for brief 20260815-115257. Group membership is derived from
each task's `deps`/`files` (`coordinator.plan_groups`), so two compiles of the
same change that infer different values yield different groups for what is
nominally one run. Run full-1786812908 left two plans 108s apart in `runplans/`
disagreeing on `deps`, `files`, and `kind` -- `base` was
`[1.1, 1.2, 1.3, 1.4, 2.1, ...]` under one and `[1.1, 1.2, 1.3]` under the
other. Nothing recorded which plan any phase used, so the drift was only
reconstructable by noticing two files on disk after the fact.

Run: python3 -m pytest tests/orchestrator/test_plan_fingerprint_record.py
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from worktrail.orchestrator import live


class _Plan:
    def __init__(self, fingerprint, source="compiled"):
        self.fingerprint = fingerprint
        self.source = source


class PlanFingerprintRecord(unittest.TestCase):
    def _journal(self, repo, spec_rel):
        jp = live.journal_path_for(repo, spec_rel)
        return json.loads(jp.read_text()) if jp.exists() else {}

    def test_fingerprint_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            with contextlib.redirect_stdout(io.StringIO()):
                live._record_plan_fingerprint(repo, spec_rel, _Plan("a" * 64))
            j = self._journal(repo, spec_rel)
            self.assertEqual(j["plan_fingerprint"], "a" * 64)
            self.assertEqual(j["plan_fingerprints"], ["a" * 64])

    def test_same_fingerprint_twice_is_not_drift(self):
        """A resume or cache hit recompiles to the same plan -- not drift."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                live._record_plan_fingerprint(repo, spec_rel, _Plan("a" * 64))
                live._record_plan_fingerprint(repo, spec_rel, _Plan("a" * 64))
            self.assertEqual(
                self._journal(repo, spec_rel)["plan_fingerprints"], ["a" * 64]
            )
            self.assertNotIn("PLAN DRIFT", buf.getvalue())

    def test_second_distinct_fingerprint_is_reported_as_drift(self):
        """THE 20260815-115257 SHAPE: same spec, two distinct compiled plans."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                live._record_plan_fingerprint(
                    repo, spec_rel, _Plan("89f1bfcc" + "0" * 56)
                )
                live._record_plan_fingerprint(
                    repo, spec_rel, _Plan("92111846" + "0" * 56)
                )
            j = self._journal(repo, spec_rel)
            self.assertEqual(len(j["plan_fingerprints"]), 2)
            self.assertEqual(j["plan_fingerprint"], "92111846" + "0" * 56)
            out = buf.getvalue()
            self.assertIn("PLAN DRIFT", out)
            self.assertIn("89f1bfcc", out)
            self.assertIn("92111846", out)

    def test_existing_journal_content_is_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            jp = live.journal_path_for(repo, spec_rel)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps({"entries": [{"task": "1.1"}], "run_id": "r1"}))
            with contextlib.redirect_stdout(io.StringIO()):
                live._record_plan_fingerprint(repo, spec_rel, _Plan("a" * 64))
            j = self._journal(repo, spec_rel)
            self.assertEqual(j["run_id"], "r1")
            self.assertEqual(j["entries"], [{"task": "1.1"}])
            self.assertEqual(j["plan_fingerprint"], "a" * 64)

    def test_plan_without_fingerprint_is_a_noop(self):
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            with contextlib.redirect_stdout(io.StringIO()):
                live._record_plan_fingerprint(repo, spec_rel, _Plan(None))
            self.assertFalse(live.journal_path_for(repo, spec_rel).exists())

    def test_pinning_keeps_a_tail_bearing_run_at_one_fingerprint_no_drift(self):
        """The end-state this change exists to produce: two `apply_run_plan()`
        calls for the same (repo, spec) -- e.g. a `base` phase followed by a
        `[cleanup]` tail phase of the same run -- must reuse the pin the first
        call establishes, so `plan_fingerprints` never grows past one entry
        and the PLAN DRIFT warning (DEC-006's retained defense-in-depth) never
        fires for a single run's own phases."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "specs/001-x"
            tasks = [
                {
                    "id": "TASK-001",
                    "title": "t",
                    "status": "pending",
                    "deps": [],
                    "files": ["a.py"],
                    "kind": "impl",
                    "path": "tasks/TASK-001.md",
                }
            ]
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                live.apply_run_plan(repo, spec_rel, "001-x", tasks)
                live.apply_run_plan(repo, spec_rel, "001-x", tasks)
            j = self._journal(repo, spec_rel)
            self.assertEqual(len(j["plan_fingerprints"]), 1)
            self.assertNotIn("PLAN DRIFT", buf.getvalue())

    def test_unwritable_journal_never_raises(self):
        """Observability must never take a run down."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            jp = live.journal_path_for(repo, spec_rel)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text("{ this is not json")
            with contextlib.redirect_stdout(io.StringIO()):
                live._record_plan_fingerprint(repo, spec_rel, _Plan("a" * 64))

    def test_non_object_journal_top_level_is_treated_as_no_pin(self):
        """A journal whose top-level JSON is valid but not an object (e.g. a
        bare `null`) must resolve to no pin, not raise out of `.get` -- DEC-004:
        journal I/O never takes a run down."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            jp = live.journal_path_for(repo, spec_rel)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text("null")
            self.assertIsNone(live._pinned_plan_fingerprint(repo, spec_rel))

    def test_non_string_plan_fingerprint_is_treated_as_no_pin(self):
        """A truncated or hand-edited journal with a non-string
        `plan_fingerprint` must not flow into `[:12]` slicing or cache lookup."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            jp = live.journal_path_for(repo, spec_rel)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps({"plan_fingerprint": 12345}))
            self.assertIsNone(live._pinned_plan_fingerprint(repo, spec_rel))

    def test_non_utf8_journal_is_treated_as_no_pin(self):
        """A binary/non-UTF-8 journal raises `UnicodeDecodeError` (a `ValueError`
        subclass) from `read_text()` -- must resolve to no pin, not propagate."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            jp = live.journal_path_for(repo, spec_rel)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_bytes(b"\xff\xfe\x00\x01")
            self.assertIsNone(live._pinned_plan_fingerprint(repo, spec_rel))


class PlanPinSurvivesJournalRewrite(unittest.TestCase):
    """A wholesale journal rewrite must not destroy the run's plan pin.

    Both schedulers' `record()` rebuild the journal dict from scratch and write
    it with `atomic_write_text`, while `_record_plan_fingerprint` does its own
    read-modify-write of the pin keys. Before `_preserve_plan_pin`, the rebuild
    silently dropped them: run full-1786825958's journal ended with exactly
    `_record()`'s own key set and no `plan_fingerprint`, despite the run having
    logged `fingerprint=214b79dd6933` three times. That disabled both the PLAN
    DRIFT warning and `apply_run_plan`'s pin, so every phase recompiled.
    """

    def test_rebuilt_journal_keeps_the_pin(self):
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "run-x.json"
            jp.write_text(
                json.dumps(
                    {"plan_fingerprint": "a" * 64, "plan_fingerprints": ["a" * 64]}
                )
            )
            rebuilt = {"spec_id": "x", "entries": [], "run_id": "full-1"}
            live._preserve_plan_pin(jp, rebuilt)
            self.assertEqual(rebuilt["plan_fingerprint"], "a" * 64)
            self.assertEqual(rebuilt["plan_fingerprints"], ["a" * 64])

    def test_pin_survives_a_real_record_then_read_back_cycle(self):
        """End-to-end: stamp a pin, rebuild the journal the way `record()` does,
        and confirm `_pinned_plan_fingerprint` still resolves it."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            with contextlib.redirect_stdout(io.StringIO()):
                live._record_plan_fingerprint(repo, spec_rel, _Plan("b" * 64))
            jp = live.journal_path_for(repo, spec_rel)
            rebuilt = {"spec_id": "x", "entries": [], "run_id": "full-1"}
            live._preserve_plan_pin(jp, rebuilt)
            jp.write_text(json.dumps(rebuilt))
            self.assertEqual(live._pinned_plan_fingerprint(repo, spec_rel), "b" * 64)

    def test_rebuilt_journal_wins_when_it_sets_the_key_itself(self):
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "run-x.json"
            jp.write_text(json.dumps({"plan_fingerprint": "a" * 64}))
            rebuilt = {"plan_fingerprint": "c" * 64}
            live._preserve_plan_pin(jp, rebuilt)
            self.assertEqual(rebuilt["plan_fingerprint"], "c" * 64)

    def test_missing_or_unreadable_journal_is_a_no_op(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "absent.json"
            rebuilt: dict = {"spec_id": "x"}
            live._preserve_plan_pin(missing, rebuilt)
            self.assertEqual(rebuilt, {"spec_id": "x"})

            bad = Path(td) / "bad.json"
            bad.write_text("{not json")
            rebuilt2: dict = {"spec_id": "x"}
            live._preserve_plan_pin(bad, rebuilt2)
            self.assertEqual(rebuilt2, {"spec_id": "x"})

            nonobj = Path(td) / "list.json"
            nonobj.write_text("[1, 2, 3]")
            rebuilt3: dict = {"spec_id": "x"}
            live._preserve_plan_pin(nonobj, rebuilt3)
            self.assertEqual(rebuilt3, {"spec_id": "x"})

    def test_no_unrelated_stale_keys_are_resurrected(self):
        """Only the pin keys carry over -- a general merge would resurrect state
        the rebuilding writer deliberately dropped (e.g. cleared group records)."""
        with tempfile.TemporaryDirectory() as td:
            jp = Path(td) / "run-x.json"
            jp.write_text(
                json.dumps(
                    {
                        "plan_fingerprint": "a" * 64,
                        "groups": {"stale": {}},
                        "integrate_complete": True,
                    }
                )
            )
            rebuilt = {"spec_id": "x", "entries": []}
            live._preserve_plan_pin(jp, rebuilt)
            self.assertEqual(rebuilt["plan_fingerprint"], "a" * 64)
            self.assertNotIn("groups", rebuilt)
            self.assertNotIn("integrate_complete", rebuilt)


if __name__ == "__main__":
    unittest.main()
