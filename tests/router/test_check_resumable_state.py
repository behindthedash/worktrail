#!/usr/bin/env python3
"""Unit tests for the Route E mechanical resumable-state pre-check.

Real throwaway files on disk for the brief + run-record scan (the logic under
test *is* the file scan); the `gh` open-PR lookup is monkeypatched since it's
the one live-I/O boundary, mirroring test_classify.py's TestCitedPrStates.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.router import check_resumable_state as crs


def _write_brief(tmpdir: Path, brief_id: str, repo: str) -> Path:
    path = tmpdir / f"{brief_id}.md"
    path.write_text(
        f"---\nid: {brief_id}\nrepo: {repo}\nstatus: picked\n---\n\n## Focus\n\ntest\n",
        encoding="utf-8",
    )
    return path


def _write_run_record(runs_dir: Path, repo: str, run_id: str, brief_id: str,
                       final_status, worktree) -> Path:
    repo_dir = runs_dir / Path(repo).name
    repo_dir.mkdir(parents=True, exist_ok=True)
    path = repo_dir / f"{run_id}.yaml"
    final_status_line = "null" if final_status is None else final_status
    worktree_line = "null" if worktree is None else worktree
    path.write_text(
        f"run_id: {run_id}\n"
        f"repository: {repo}\n"
        f"worktree: {worktree_line}\n"
        f"request_summary: \"references {brief_id} in its request\"\n"
        f"final_status: {final_status_line}\n",
        encoding="utf-8",
    )
    return path


class TestUnreadableBrief(unittest.TestCase):
    def test_missing_brief_is_unchecked(self):
        with tempfile.TemporaryDirectory() as t:
            res = crs.check(Path(t) / "does-not-exist.md")
            self.assertFalse(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNotNone(res["warning"])


class TestNoRepoFrontmatter(unittest.TestCase):
    def test_missing_repo_skips_check_but_stays_checked(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            brief = tmp / "b.md"
            brief.write_text("---\nid: b\nrepo: null\n---\n\n## Focus\n\ntest\n",
                              encoding="utf-8")
            res = crs.check(brief, do_gh=False)
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIn("no repo:", res["warning"])


class TestRunRecordScan(unittest.TestCase):
    def test_no_run_records_directory_is_not_resumable(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            brief = _write_brief(tmp, "20260812-163747-fresh-claim", repo)
            res = crs.check(brief, runs_dir=tmp / "no-such-runs-dir", do_gh=False)
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNone(res["evidence"]["run_record"])

    def test_inflight_record_with_existing_worktree_is_resumable(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            runs_dir = tmp / "runs"
            worktree = tmp / "myrepo-worktrees" / "some-branch"
            worktree.mkdir(parents=True)
            brief_id = "20260812-999999-real-resume"
            brief = _write_brief(tmp, brief_id, repo)
            _write_run_record(runs_dir, repo, "go-1", brief_id,
                               final_status=None, worktree=str(worktree))

            res = crs.check(brief, runs_dir=runs_dir, do_gh=False)
            self.assertTrue(res["checked"])
            self.assertTrue(res["resumable"])
            self.assertIsNotNone(res["evidence"]["run_record"])
            self.assertEqual(res["evidence"]["worktree"], str(worktree))

    def test_inflight_record_with_missing_worktree_is_not_resumable(self):
        # Worktree already cleaned up (e.g. torn down post-merge) -- the run
        # record alone is surfaced as evidence but doesn't make this resumable.
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            runs_dir = tmp / "runs"
            gone_worktree = tmp / "myrepo-worktrees" / "deleted-branch"
            brief_id = "20260812-888888-stale-worktree"
            brief = _write_brief(tmp, brief_id, repo)
            _write_run_record(runs_dir, repo, "go-1", brief_id,
                               final_status=None, worktree=str(gone_worktree))

            res = crs.check(brief, runs_dir=runs_dir, do_gh=False)
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNotNone(res["evidence"]["run_record"])
            self.assertIsNone(res["evidence"]["worktree"])

    def test_finished_record_is_not_resumable(self):
        # A completed run referencing this brief id is not "resumable work in
        # flight" -- exactly the case that must not be mistaken for an active
        # resume (the completed run's own worktree may still exist on disk).
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            runs_dir = tmp / "runs"
            worktree = tmp / "myrepo-worktrees" / "some-branch"
            worktree.mkdir(parents=True)
            brief_id = "20260812-777777-already-done"
            brief = _write_brief(tmp, brief_id, repo)
            _write_run_record(runs_dir, repo, "go-1", brief_id,
                               final_status="completed_and_merged", worktree=str(worktree))

            res = crs.check(brief, runs_dir=runs_dir, do_gh=False)
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNone(res["evidence"]["run_record"])

    def test_unrelated_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            runs_dir = tmp / "runs"
            brief_id = "20260812-163747-classify-handoff-s-route-hint"
            brief = _write_brief(tmp, brief_id, repo)
            _write_run_record(runs_dir, repo, "go-1", "some-other-brief-entirely",
                               final_status=None, worktree=str(tmp / "wt"))

            res = crs.check(brief, runs_dir=runs_dir, do_gh=False)
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNone(res["evidence"]["run_record"])


class TestOpenPrLookup(unittest.TestCase):
    def test_open_pr_found_is_resumable(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            brief = _write_brief(tmp, "20260812-163747-classify-handoff-s-route-hint", repo)

            with patch.object(crs, "_find_open_pr",
                               return_value={"number": 42, "url": "https://example/42"}):
                res = crs.check(brief, runs_dir=tmp / "no-runs", do_gh=True)
            self.assertTrue(res["checked"])
            self.assertTrue(res["resumable"])
            self.assertEqual(res["evidence"]["open_pr"]["number"], 42)

    def test_no_open_pr_is_not_resumable(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            brief = _write_brief(tmp, "20260812-163747-classify-handoff-s-route-hint", repo)

            with patch.object(crs, "_find_open_pr", return_value=None):
                res = crs.check(brief, runs_dir=tmp / "no-runs", do_gh=True)
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNone(res["evidence"]["open_pr"])

    def test_gh_unavailable_degrades_to_not_resumable_not_unchecked(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            brief = _write_brief(tmp, "20260812-163747-classify-handoff-s-route-hint", repo)

            with patch("shutil.which", return_value=None):
                res = crs.check(brief, runs_dir=tmp / "no-runs", do_gh=True)
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])

    def test_do_gh_false_skips_lookup_entirely(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            brief = _write_brief(tmp, "20260812-163747-classify-handoff-s-route-hint", repo)

            with patch.object(crs, "_find_open_pr") as mock_find:
                res = crs.check(brief, runs_dir=tmp / "no-runs", do_gh=False)
            mock_find.assert_not_called()
            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])


class TestIncidentReproduction(unittest.TestCase):
    """The exact fresh-claim scenario from brief 20260812-163747: no run
    record, no worktree, no open PR -- must report checked=True, resumable=False."""

    def test_fresh_claim_is_not_resumable(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "worktrail")
            brief = _write_brief(tmp, "20260812-163747-classify-handoff-s-route-hint", repo)

            with patch.object(crs, "_find_open_pr", return_value=None):
                res = crs.check(brief, runs_dir=tmp / "no-runs", do_gh=True)

            self.assertTrue(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNone(res["evidence"]["run_record"])
            self.assertIsNone(res["evidence"]["worktree"])
            self.assertIsNone(res["evidence"]["open_pr"])


class TestCli(unittest.TestCase):
    def test_json_output_matches_check_result(self):
        import io
        import json
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            brief = _write_brief(tmp, "b", repo)
            runs_dir = tmp / "runs"

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = crs.main(["--brief", str(brief), "--dir", str(runs_dir), "--no-gh", "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(buf.getvalue()),
                              crs.check(brief, runs_dir=runs_dir, do_gh=False))

    def test_no_gh_flag_is_wired(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t)
            repo = str(tmp / "myrepo")
            brief = _write_brief(tmp, "b", repo)

            with patch.object(crs, "_find_open_pr") as mock_find:
                rc = crs.main(["--brief", str(brief), "--dir", str(tmp / "runs"),
                                "--no-gh", "--json"])
            self.assertEqual(rc, 0)
            mock_find.assert_not_called()


if __name__ == "__main__":
    unittest.main()
