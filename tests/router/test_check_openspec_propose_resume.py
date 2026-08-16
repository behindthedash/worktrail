#!/usr/bin/env python3
"""Unit tests for the openspec-propose headless-spawn resumability pre-check.

Pure filesystem checks -- no live I/O boundary to mock, unlike
test_check_resumable_state.py's `gh` lookup.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from worktrail.router import check_openspec_propose_resume as cor


class TestMissingWorktree(unittest.TestCase):
    def test_nonexistent_worktree_is_unchecked(self):
        with tempfile.TemporaryDirectory() as t:
            res = cor.check(Path(t) / "does-not-exist", "some-change")
            self.assertFalse(res["checked"])
            self.assertFalse(res["resumable"])
            self.assertIsNotNone(res["warning"])


class TestNoChangeDirectory(unittest.TestCase):
    def test_change_never_started_is_not_resumable(self):
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            (wt / "openspec" / "changes").mkdir(parents=True)
            res = cor.check(wt, "fresh-change")
            self.assertTrue(res["checked"])
            self.assertFalse(res["exists"])
            self.assertFalse(res["resumable"])
            self.assertEqual(res["present"], [])
            self.assertEqual(sorted(res["missing"]), ["design.md", "proposal.md", "tasks.md"])


class TestEmptyChangeDirectory(unittest.TestCase):
    def test_empty_change_dir_is_not_resumable(self):
        # e.g. from `git worktree add` alone, before any authoring wrote a file.
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            change_dir = wt / "openspec" / "changes" / "empty-change"
            change_dir.mkdir(parents=True)
            res = cor.check(wt, "empty-change")
            self.assertTrue(res["checked"])
            self.assertTrue(res["exists"])
            self.assertFalse(res["resumable"])
            self.assertFalse(res["has_specs"])


class TestPartialArtifacts(unittest.TestCase):
    def test_partial_artifacts_are_resumable(self):
        # This is the exact failure mode the brief describes: a killed spawn
        # left proposal.md written but design.md/tasks.md never authored.
        # Re-dispatching a fresh /opsx:propose for this change-id would hit
        # OpenSpec's own change-name-collision guardrail instead of resuming.
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            change_dir = wt / "openspec" / "changes" / "killed-mid-generation"
            change_dir.mkdir(parents=True)
            (change_dir / "proposal.md").write_text("# Proposal\n", encoding="utf-8")
            res = cor.check(wt, "killed-mid-generation")
            self.assertTrue(res["checked"])
            self.assertTrue(res["exists"])
            self.assertTrue(res["resumable"])
            self.assertEqual(res["present"], ["proposal.md"])
            self.assertEqual(sorted(res["missing"]), ["design.md", "tasks.md"])
            self.assertFalse(res["has_specs"])


class TestPartialArtifactsWithSpecsOnly(unittest.TestCase):
    def test_specs_only_is_resumable(self):
        # A spawn that got as far as writing delta specs but not the three
        # top-level files is still real partial work worth resuming.
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            change_dir = wt / "openspec" / "changes" / "specs-only"
            specs_dir = change_dir / "specs" / "some-capability"
            specs_dir.mkdir(parents=True)
            (specs_dir / "spec.md").write_text("## ADDED Requirements\n", encoding="utf-8")
            res = cor.check(wt, "specs-only")
            self.assertTrue(res["checked"])
            self.assertTrue(res["exists"])
            self.assertTrue(res["resumable"])
            self.assertEqual(res["present"], [])
            self.assertTrue(res["has_specs"])


class TestCompleteArtifacts(unittest.TestCase):
    def test_complete_change_is_resumable_not_fresh(self):
        # Complete-but-unmerged is still "don't re-run propose" -- resumable
        # just means "don't blindly re-propose", not "incomplete".
        with tempfile.TemporaryDirectory() as t:
            wt = Path(t)
            change_dir = wt / "openspec" / "changes" / "complete-change"
            specs_dir = change_dir / "specs" / "some-capability"
            specs_dir.mkdir(parents=True)
            (change_dir / "proposal.md").write_text("# Proposal\n", encoding="utf-8")
            (change_dir / "design.md").write_text("# Design\n", encoding="utf-8")
            (change_dir / "tasks.md").write_text("- [ ] task\n", encoding="utf-8")
            (specs_dir / "spec.md").write_text("## ADDED Requirements\n", encoding="utf-8")
            res = cor.check(wt, "complete-change")
            self.assertTrue(res["checked"])
            self.assertTrue(res["exists"])
            self.assertTrue(res["resumable"])
            self.assertEqual(res["missing"], [])


if __name__ == "__main__":
    unittest.main()
