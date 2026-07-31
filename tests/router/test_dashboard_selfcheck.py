#!/usr/bin/env python3
"""Tests for dashboard_selfcheck.py. Run: python3 -m pytest test_dashboard_selfcheck.py -q"""
import tempfile
import unittest
from pathlib import Path

from worktrail.router.dashboard_selfcheck import sweep


def _git_repo(root: Path, name: str) -> Path:
    repo = root / name
    (repo / ".git").mkdir(parents=True)
    return repo


def _spec_dir(repo: Path, spec_id: str) -> Path:
    d = repo / "docs" / "specs" / spec_id
    d.mkdir(parents=True)
    return d


class TestSweep(unittest.TestCase):
    def test_sweep_flags_only_ambiguous_repo(self):
        tmp = Path(tempfile.mkdtemp())
        clean_repo = _git_repo(tmp, "clean-repo")
        spec = _spec_dir(clean_repo, "001-feature")
        (spec / "2024-01-01--feature.md").write_text("# feature spec\n")
        (spec / "tasks.md").write_text("- [ ] 1.1 do thing\n")

        flagged_repo = _git_repo(tmp, "flagged-repo")
        spec = _spec_dir(flagged_repo, "002-other")
        (spec / "overview.md").write_text("# overview\n")
        (spec / "notes-and-context.md").write_text("# notes\n")

        results = sweep(tmp)

        flagged_names = {r["repo"] for r in results}
        self.assertEqual(flagged_names, {"flagged-repo"})

        flagged_result = next(r for r in results if r["repo"] == "flagged-repo")
        self.assertEqual(len(flagged_result["findings"]), 1)
        self.assertEqual(flagged_result["findings"][0]["spec"], "002-other")

    def test_sweep_json_flagged_count_matches(self):
        tmp = Path(tempfile.mkdtemp())
        _git_repo(tmp, "clean-repo")
        flagged_repo = _git_repo(tmp, "flagged-repo")
        spec = _spec_dir(flagged_repo, "002-other")
        (spec / "overview.md").write_text("# overview\n")
        (spec / "notes-and-context.md").write_text("# notes\n")

        results = sweep(tmp)
        flagged = [r for r in results if r["findings"]]
        self.assertEqual(len(results), len(flagged))
        self.assertEqual(len(flagged), 1)


if __name__ == "__main__":
    unittest.main()
