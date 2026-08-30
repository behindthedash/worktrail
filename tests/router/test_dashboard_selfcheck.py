#!/usr/bin/env python3
"""Tests for dashboard_selfcheck.py. Run: python3 -m pytest test_dashboard_selfcheck.py -q"""

import json
import tempfile
import unittest
from pathlib import Path

from worktrail.router.dashboard_selfcheck import check_repo, main, sweep


def _spec_dir(repo: Path, spec_id: str, files: dict) -> Path:
    """Writes each {filename: content} pair under docs/specs/<spec_id>/."""
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (spec_dir / name).write_text(content)
    return spec_dir


def _git_repo(root: Path, name: str) -> Path:
    """A repo directory recognized by discover_repo_names() (has a `.git/`)."""
    repo = root / name
    (repo / ".git").mkdir(parents=True, exist_ok=True)
    return repo


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

    def test_recognized_name_candidate_among_no_signal_candidates_yields_no_finding(
        self,
    ):
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


class TestSweep(unittest.TestCase):
    def test_sweep_flags_only_the_flagged_repo(self):
        tmp = Path(tempfile.mkdtemp())
        clean_repo = _git_repo(tmp, "clean-repo")
        _spec_dir(clean_repo, "001-example", {"spec.md": "# example\n"})
        flagged_repo = _git_repo(tmp, "flagged-repo")
        _spec_dir(
            flagged_repo,
            "001-example",
            {
                "misc-notes.md": "# misc\n",
                "other-notes.md": "# other\n",
            },
        )

        results = sweep(tmp)

        self.assertEqual([r["repo"] for r in results], ["flagged-repo"])
        payload = json.loads(json.dumps({"results": results, "flagged": len(results)}))
        self.assertEqual(payload["flagged"], 1)


class TestCli(unittest.TestCase):
    def test_clean_repo_exits_zero(self):
        import io
        from contextlib import redirect_stdout

        tmp = Path(tempfile.mkdtemp())
        _spec_dir(tmp, "001-example", {"spec.md": "# example\n"})

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--repo", str(tmp)])
        self.assertEqual(rc, 0)
        self.assertIn("no ambiguous spec docs", buf.getvalue())

    def test_flagged_repo_exits_one(self):
        import io
        from contextlib import redirect_stdout

        tmp = Path(tempfile.mkdtemp())
        _spec_dir(
            tmp,
            "001-example",
            {
                "misc-notes.md": "# misc\n",
                "other-notes.md": "# other\n",
            },
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--repo", str(tmp)])
        self.assertEqual(rc, 1)
        self.assertIn("ambiguous-spec-doc", buf.getvalue())

    def test_json_output_matches_check_repo(self):
        import io
        from contextlib import redirect_stdout

        tmp = Path(tempfile.mkdtemp())
        _spec_dir(
            tmp,
            "001-example",
            {
                "misc-notes.md": "# misc\n",
                "other-notes.md": "# other\n",
            },
        )

        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = main(["--repo", str(tmp), "--json"])
        self.assertEqual(rc, 1)
        payload = json.loads(buf.getvalue())
        self.assertEqual(payload["flagged"], 1)
        self.assertEqual(payload["results"], [check_repo(tmp)])


if __name__ == "__main__":
    unittest.main()
