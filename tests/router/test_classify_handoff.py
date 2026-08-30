#!/usr/bin/env python3
"""Tests for classify_handoff.py."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router import classify_handoff as ch


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestClassifyHandoff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.specs = self.root / "docs" / "specs"
        _write(
            self.specs / "003-handoff-go-input" / "spec.md",
            "# Functional Specification: Handoff as an Input to the SDD Conductor\n\n"
            "The handoff queue seeds the conductor through work_queue.py and handoff_seed.py.\n",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def brief(self, body: str, fm: str = "") -> Path:
        text = (
            "---\nfocus: Update handoff conductor routing\nstatus: picked\n"
            + fm
            + "---\n\n"
            + body
        )
        return _write(self.root / "brief.md", text)

    def test_change_kind_bugfix_sets_route_f(self):
        brief = self.brief("Fix the handoff seed route.", "change-kind: bugfix\n")
        result = ch.classify_handoff(brief, self.specs)
        self.assertEqual(result["change_kind"], "bugfix")
        self.assertEqual(result["hint"], "F")
        self.assertIn("change-kind:bugfix", result["signals"])

    def test_target_spec_boosts_candidate(self):
        brief = self.brief(
            "Modify handoff_seed.py so it can route existing specs.",
            "change-kind: delta\ntarget-spec: 003-handoff-go-input\n",
        )
        result = ch.classify_handoff(brief, self.specs)
        self.assertEqual(result["hint"], "G")
        self.assertEqual(
            result["candidate_specs"][0]["spec_id"], "003-handoff-go-input"
        )
        self.assertIn("target-spec", result["candidate_specs"][0]["signals"])

    def test_existing_spec_change_words_infer_delta(self):
        brief = self.brief(
            "Change the handoff queue integration to delegate through sdd-workflow."
        )
        result = ch.classify_handoff(brief, self.specs)
        self.assertEqual(result["hint"], "G")
        self.assertEqual(
            result["candidate_specs"][0]["spec_id"], "003-handoff-go-input"
        )

    def test_recommended_route_used_when_no_stronger_hint(self):
        brief = self.brief(
            "Investigate how handoff route selection behaves.", "recommended-route: I\n"
        )
        result = ch.classify_handoff(brief, self.specs)
        self.assertEqual(result["hint"], "I")
        self.assertEqual(result["recommended_route"], "I")

    def test_missing_trailing_newline_after_frontmatter_is_still_classified(self):
        brief = _write(
            self.root / "brief-no-trailing-newline.md",
            "---\nfocus: Update handoff conductor routing\nchange-kind: bugfix\n---",
        )
        result = ch.classify_handoff(brief, self.specs)
        self.assertEqual(result["change_kind"], "bugfix")
        self.assertEqual(result["hint"], "F")


if __name__ == "__main__":
    unittest.main(verbosity=2)
