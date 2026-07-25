#!/usr/bin/env python3
"""Tests for stable run_id behavior (TASK-001 AC-001, AC-002, AC-014, REQ-019)."""
import json
import tempfile
import unittest
from pathlib import Path

from worktrail.orchestrator import live


class StableRunIdAcrossInvocations(unittest.TestCase):
    """AC-014: Two consecutive invocations yield identical run_id and branch names."""

    def test_second_invocation_reads_same_run_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "run-test.json"
            stored_run_id = "full-1717123456"
            journal = {"spec_id": "test-spec", "entries": [], "run_id": stored_run_id}
            journal_path.write_text(json.dumps(journal))

            read_run_id = live.read_or_create_run_id(journal_path)
            self.assertEqual(read_run_id, stored_run_id,
                             "should read stored run_id, not generate new one")


class FreshRunWritesRunId(unittest.TestCase):
    """AC-002: Fresh run generates and writes run_id to journal."""

    def test_fresh_journal_gets_run_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "run-test.json"

            run_id = live.read_or_create_run_id(journal_path)

            self.assertIsNotNone(run_id)
            self.assertTrue(run_id.startswith("full-"),
                            f"run_id should match pattern 'full-<digits>', got {run_id}")

            # Verify it's written to disk
            journal = json.loads(journal_path.read_text())
            self.assertEqual(journal.get("run_id"), run_id,
                             "run_id should be persisted to journal")


class LegacyJournalInheritance(unittest.TestCase):
    """REQ-019: Journal with no run_id gets one injected and entries preserved."""

    def test_legacy_journal_without_run_id(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "run-test.json"
            legacy_journal = {
                "spec_id": "test-spec",
                "entries": [
                    {"task": "TASK-001", "role": "implement", "report": {}}
                ]
            }
            journal_path.write_text(json.dumps(legacy_journal))

            run_id = live.read_or_create_run_id(journal_path)

            self.assertIsNotNone(run_id)
            self.assertTrue(run_id.startswith("full-"))

            # Verify entries are preserved
            updated = json.loads(journal_path.read_text())
            self.assertEqual(updated["spec_id"], "test-spec")
            self.assertEqual(len(updated["entries"]), 1)
            self.assertEqual(updated.get("run_id"), run_id)


if __name__ == "__main__":
    unittest.main()
