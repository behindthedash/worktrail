#!/usr/bin/env python3
"""Tests for dashboard_selfcheck.py. Run: python3 -m pytest test_dashboard_selfcheck.py -q"""
import tempfile
import unittest
from pathlib import Path

from worktrail.router.dashboard_selfcheck import check_repo


def _spec_dir(repo: Path, spec_id: str, files: dict) -> Path:
    """Writes each {filename: content} pair under docs/specs/<spec_id>/."""
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (spec_dir / name).write_text(content)
    return spec_dir


class TestCheckRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_zero_candidates_yields_no_finding(self):
        # Only an auxiliary artifact, no spec-doc candidate at all.
        _spec_dir(self.tmp, "001-example", {"user-request.md": "# request\n"})
        result = check_repo(self.tmp)
        self.assertEqual(result["findings"], [])

    def test_single_no_signal_candidate_yields_no_finding(self):
        # One rank-3 (no naming-convention evidence) file is still trusted --
        # find_spec_file() resolves it cleanly since it's the only candidate.
        _spec_dir(self.tmp, "001-example", {"architecture-notes.md": "# notes\n"})
        result = check_repo(self.tmp)
        self.assertEqual(result["findings"], [])

    def test_dated_candidate_among_no_signal_candidates_yields_no_finding(self):
        # A dated spec doc wins outright over any number of no-signal siblings.
        _spec_dir(
            self.tmp,
            "001-example",
            {
                "2026-01-01--example.md": "# example\n",
                "misc-notes.md": "# misc\n",
                "other-notes.md": "# other\n",
            },
        )
        result = check_repo(self.tmp)
        self.assertEqual(result["findings"], [])

    def test_recognized_name_candidate_among_no_signal_candidates_yields_no_finding(self):
        # spec.md (rank 0) is picked over the tied rank-3 no-signal candidates.
        _spec_dir(
            self.tmp,
            "001-example",
            {
                "spec.md": "# example\n",
                "misc-notes.md": "# misc\n",
                "other-notes.md": "# other\n",
            },
        )
        result = check_repo(self.tmp)
        self.assertEqual(result["findings"], [])

    def test_tied_no_signal_candidates_yields_finding_naming_the_files(self):
        _spec_dir(
            self.tmp,
            "001-example",
            {
                "misc-notes.md": "# misc\n",
                "other-notes.md": "# other\n",
            },
        )
        result = check_repo(self.tmp)
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(finding["signal"], "ambiguous-spec-doc")
        self.assertEqual(finding["spec"], "001-example")
        self.assertIn("misc-notes.md", finding["detail"])
        self.assertIn("other-notes.md", finding["detail"])


if __name__ == "__main__":
    unittest.main()
