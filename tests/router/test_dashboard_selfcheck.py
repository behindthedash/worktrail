#!/usr/bin/env python3
"""Tests for dashboard_selfcheck.py. Run: python3 -m pytest test_dashboard_selfcheck.py -q"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from worktrail.router.dashboard_selfcheck import check_repo, main, sweep

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def _git_repo(root: Path, name: str) -> Path:
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    return repo


def _spec_dir(repo: Path, spec_id: str, files: dict) -> Path:
    spec_dir = repo / "docs" / "specs" / spec_id
    spec_dir.mkdir(parents=True)
    for fname, text in files.items():
        (spec_dir / fname).write_text(text)
    return spec_dir


class TestCheckRepo(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_zero_candidates_no_finding(self):
        repo = _git_repo(self.tmp, "aperi")
        _spec_dir(repo, "001-foo", {"tasks.md": "tasks"})
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])

    def test_one_no_signal_candidate_no_finding(self):
        repo = _git_repo(self.tmp, "aperi")
        _spec_dir(repo, "001-foo", {"random-notes.md": "prose"})
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])

    def test_dated_candidate_with_no_signal_siblings_no_finding(self):
        repo = _git_repo(self.tmp, "aperi")
        _spec_dir(repo, "001-foo", {
            "2026-01-01--foo.md": "spec",
            "random-notes.md": "prose",
        })
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])

    def test_recognized_name_candidate_with_no_signal_siblings_no_finding(self):
        repo = _git_repo(self.tmp, "aperi")
        _spec_dir(repo, "001-foo", {
            "spec.md": "spec",
            "random-notes.md": "prose",
        })
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])

    def test_two_tied_no_signal_candidates_flagged(self):
        repo = _git_repo(self.tmp, "aperi")
        _spec_dir(repo, "001-foo", {
            "misc-notes.md": "prose",
            "investigation-summary.md": "prose",
        })
        result = check_repo(repo)
        self.assertEqual(len(result["findings"]), 1)
        finding = result["findings"][0]
        self.assertEqual(finding["signal"], "ambiguous-spec-doc")
        self.assertEqual(finding["spec"], "001-foo")
        self.assertIn("misc-notes.md", finding["detail"])
        self.assertIn("investigation-summary.md", finding["detail"])

    def test_missing_specs_dir_no_finding(self):
        repo = _git_repo(self.tmp, "aperi")
        result = check_repo(repo)
        self.assertEqual(result["findings"], [])


class TestSweep(unittest.TestCase):
    def test_sweep_flags_only_ambiguous_repo(self):
        tmp = Path(tempfile.mkdtemp())
        clean = _git_repo(tmp, "aperi")
        _spec_dir(clean, "001-foo", {"spec.md": "spec"})
        flagged = _git_repo(tmp, "datalena")
        _spec_dir(flagged, "001-bar", {
            "misc-notes.md": "prose",
            "investigation-summary.md": "prose",
        })
        results = sweep(tmp)
        self.assertEqual({r["repo"] for r in results}, {"datalena"})


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_clean_repo_exits_zero(self):
        repo = _git_repo(self.tmp, "aperi")
        _spec_dir(repo, "001-foo", {"spec.md": "spec"})
        rc = main(["--repo", str(repo)])
        self.assertEqual(rc, 0)

    def test_flagged_repo_exits_one(self):
        repo = _git_repo(self.tmp, "datalena")
        _spec_dir(repo, "001-bar", {
            "misc-notes.md": "prose",
            "investigation-summary.md": "prose",
        })
        rc = main(["--repo", str(repo)])
        self.assertEqual(rc, 1)

    def test_json_output_shape(self):
        repo = _git_repo(self.tmp, "datalena")
        _spec_dir(repo, "001-bar", {
            "misc-notes.md": "prose",
            "investigation-summary.md": "prose",
        })
        env = {**os.environ, "PYTHONPATH": _SRC}
        proc = subprocess.run(
            [sys.executable, "-m", "worktrail.router.dashboard_selfcheck", "--repo", str(repo), "--json"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(proc.returncode, 1)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["flagged"], 1)
        self.assertEqual(len(payload["results"]), 1)
        self.assertEqual(payload["results"][0]["repo"], "datalena")
        self.assertEqual(len(payload["results"][0]["findings"]), 1)


if __name__ == "__main__":
    unittest.main()
