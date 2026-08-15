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
            self.assertEqual(self._journal(repo, spec_rel)["plan_fingerprints"], ["a" * 64])
            self.assertNotIn("PLAN DRIFT", buf.getvalue())

    def test_second_distinct_fingerprint_is_reported_as_drift(self):
        """THE 20260815-115257 SHAPE: same spec, two distinct compiled plans."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                live._record_plan_fingerprint(repo, spec_rel, _Plan("89f1bfcc" + "0" * 56))
                live._record_plan_fingerprint(repo, spec_rel, _Plan("92111846" + "0" * 56))
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

    def test_unwritable_journal_never_raises(self):
        """Observability must never take a run down."""
        with tempfile.TemporaryDirectory() as td:
            repo, spec_rel = Path(td), "openspec/changes/x"
            jp = live.journal_path_for(repo, spec_rel)
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text("{ this is not json")
            with contextlib.redirect_stdout(io.StringIO()):
                live._record_plan_fingerprint(repo, spec_rel, _Plan("a" * 64))


if __name__ == "__main__":
    unittest.main()
