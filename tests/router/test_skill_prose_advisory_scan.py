#!/usr/bin/env python3
"""Tests for the WARN-only skill-prose advisory scanner.

Brief `20260810-111717`: a non-blocking advisory scan surfacing mandate-cue +
named-action prose pairs outside the closed go:risk-*/go:no-automerge
vocabulary, for human triage during Route J review -- deliberately not a hard
CI gate. See docs/specs/research/skill-prose-enforcement-coverage-design.md
for why the same generic pairing was rejected as a *hard-gating* primary
mechanism (near-zero recall at safe precision, one confirmed false pairing).
"""
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router import skill_prose_advisory_scan as scan_mod
from worktrail.router.label_family_markers import LABEL_FAMILY_MARKERS


class TestScan(unittest.TestCase):

    def _write(self, root: Path, relpath: str, text: str) -> None:
        p = root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)

    def test_detects_mandate_cue_paired_with_named_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "example/SKILL.md", (
                "Before merging, it is **mandatory** to run "
                "`worktrail-prevent-destructive-commands.py` against the diff.\n"
            ))
            res = scan_mod.scan(root)
            self.assertEqual(res["files_scanned"], 1)
            self.assertEqual(len(res["candidates"]), 1)
            c = res["candidates"][0]
            self.assertEqual(c["file"], "example/SKILL.md")
            self.assertEqual(c["cue"], "mandatory")
            self.assertIn("prevent-destructive-commands.py", c["action"])

    def test_excludes_paragraphs_already_covered_by_label_family_markers(self):
        marker = LABEL_FAMILY_MARKERS[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "example/SKILL.md", (
                f"It is mandatory to run `some_corrective_action.py`, which also "
                f"self-heals {marker} labels.\n"
            ))
            res = scan_mod.scan(root)
            self.assertEqual(res["candidates"], [])

    def test_no_mandate_cue_yields_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "example/SKILL.md", "Run `some_helper.py` whenever convenient.\n")
            res = scan_mod.scan(root)
            self.assertEqual(res["candidates"], [])

    def test_no_named_action_yields_no_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "example/SKILL.md",
                        "It is mandatory to review this section carefully before proceeding.\n")
            res = scan_mod.scan(root)
            self.assertEqual(res["candidates"], [])

    def test_cli_always_exits_zero_even_with_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write(root, "example/SKILL.md",
                        "It is **mandatory** to run `dangerous_thing.py` first.\n")
            out = StringIO()
            with patch("sys.stdout", out):
                rc = scan_mod.main(["--skills-root", str(root), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(len(payload["candidates"]), 1)

    def test_cli_json_output_shape_on_empty_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = StringIO()
            with patch("sys.stdout", out):
                rc = scan_mod.main(["--skills-root", str(root), "--json"])
            self.assertEqual(rc, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload, {"candidates": [], "files_scanned": 0})

    def test_cli_human_output_on_empty_corpus(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = StringIO()
            with patch("sys.stdout", out):
                rc = scan_mod.main(["--skills-root", str(root)])
            self.assertEqual(rc, 0)
            self.assertIn("no candidates", out.getvalue())


if __name__ == "__main__":
    unittest.main()
