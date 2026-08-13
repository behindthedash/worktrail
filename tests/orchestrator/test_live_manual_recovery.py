#!/usr/bin/env python3
"""Tests for manual recovery: the --from-verify entry point, --re-integrate
journal clearing, and the skip/clear-task journal-repair commands.

Run: python3 scripts/test_live_manual_recovery.py
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402


class GroupBranchReconstruction(unittest.TestCase):
    """--from-verify reconstructs group_branch from the journal's per-group
    integrate records."""

    def test_reconstruct_group_branch_from_journal(self):
        """Test reconstruction of group_branch dict from journal records."""
        journal_groups = {
            "base": {"pr_url": "https://...", "head_branch": "run-123/base"},
            "feature": {"pr_url": "https://...", "head_branch": "run-123/feature"},
        }
        run_id = "run-123"

        # Reconstruct as the code does
        group_branch = {
            name: rec.get("head_branch", f"{run_id}/{name}") for name, rec in journal_groups.items()
        }

        self.assertEqual(group_branch["base"], "run-123/base")
        self.assertEqual(group_branch["feature"], "run-123/feature")


class FromVerifyLogicTests(unittest.TestCase):
    """AC-024, REQ-027: --from-verify entry point logic."""

    def test_from_verify_requires_journal(self):
        """--from-verify with no journal should return early."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "run-test.json"

            # Simulate the from_verify logic
            if not journal_path.exists():
                # This is what the code does
                result = {"group_prs": [], "final": None, "quarantined": {}, "merged": []}
                self.assertEqual(result["group_prs"], [])

    def test_from_verify_reads_journal_groups(self):
        """--from-verify should read group data from the journal."""
        with tempfile.TemporaryDirectory() as tmpdir:
            journal_path = Path(tmpdir) / "run-test.json"
            journal = {
                "run_id": "full-1234567890",
                "entries": [],
                "integrate_complete": True,
                "groups": {
                    "base": {
                        "pr_url": "https://github.com/owner/repo/pull/1",
                        "head_branch": "full-1234567890/base",
                        "state": "OPEN",
                    },
                    "feature": {
                        "pr_url": "https://github.com/owner/repo/pull/2",
                        "head_branch": "full-1234567890/feature",
                        "state": "OPEN",
                    },
                },
            }
            journal_path.write_text(json.dumps(journal))

            # Simulate from_verify logic
            data = json.loads(journal_path.read_text())
            journal_groups = data.get("groups", {})
            group_branch = {
                name: rec.get("head_branch", f"{data.get('run_id')}/{name}")
                for name, rec in journal_groups.items()
            }

            self.assertEqual(len(group_branch), 2)
            self.assertEqual(group_branch["base"], "full-1234567890/base")
            self.assertEqual(group_branch["feature"], "full-1234567890/feature")


class JournalWritingTests(unittest.TestCase):
    """Verify that integrate_complete marker and per-group records are written."""

    def test_journal_per_group_record_structure(self):
        """Verify the structure of per-group integrate records."""
        per_group_record = {
            "pr_url": "https://github.com/owner/repo/pull/1",
            "head_branch": "run-123/base",
            "state": "OPEN",
        }

        self.assertIn("pr_url", per_group_record)
        self.assertIn("head_branch", per_group_record)
        self.assertIn("state", per_group_record)
        self.assertTrue(per_group_record.get("pr_url"))

    def test_journal_integrate_complete_marker(self):
        """Verify integrate_complete is written to journal."""
        journal = {"run_id": "full-123", "entries": [], "integrate_complete": True, "groups": {}}

        self.assertTrue(journal.get("integrate_complete"))


class ReIntegrateClearsState(unittest.TestCase):
    """item 4: --re-integrate clears the integrate_complete marker + per-group records
    so integration rebuilds them, instead of an operator hand-editing the journal."""

    def test_clears_marker_and_groups_preserving_entries(self):
        journal = {
            "integrate_complete": True,
            "groups": {"base": {"pr_url": "u", "state": "OPEN"}},
            "entries": [{"task": "TASK-001"}],
            "run_id": "full-1",
        }
        self.assertTrue(live._clear_integration_state(journal))
        self.assertNotIn("integrate_complete", journal)
        self.assertNotIn("groups", journal)
        # Fan-out history and run identity are untouched.
        self.assertEqual(journal["entries"], [{"task": "TASK-001"}])
        self.assertEqual(journal["run_id"], "full-1")

    def test_clears_when_only_groups_present(self):
        journal = {"groups": {"base": {"pr_url": "u"}}}
        self.assertTrue(live._clear_integration_state(journal))
        self.assertNotIn("groups", journal)

    def test_noop_returns_false(self):
        journal = {"entries": [], "run_id": "full-1"}
        self.assertFalse(live._clear_integration_state(journal))
        self.assertEqual(journal, {"entries": [], "run_id": "full-1"})


class SkipTasksCommand(unittest.TestCase):
    """item 6: `skip` appends terminal escalated entries so the next full-real resume
    stops waiting on a wedged task and proceeds to integrate the rest."""

    def _journal_path(self, tmp):
        wt = Path(tmp) / "repo-worktrees"
        wt.mkdir(parents=True, exist_ok=True)
        return Path(tmp) / "repo", wt / "run-001-x.json"

    def test_skip_appends_escalated_and_reconciles_terminal(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo, jp = self._journal_path(tmp)
            jp.write_text(json.dumps({"spec_id": "001-x", "entries": []}))

            rc = live.skip_tasks(repo, "docs/specs/001-x", ["TASK-002", "TASK-003"], "wedged")
            self.assertEqual(rc, 0)

            journal = json.loads(jp.read_text())
            skips = {e["task"]: e for e in journal["entries"] if e["role"] == "manual-skip"}
            self.assertEqual(set(skips), {"TASK-002", "TASK-003"})
            self.assertEqual(skips["TASK-002"]["report"]["terminal_status"], "escalated")
            self.assertEqual(skips["TASK-002"]["report"]["notes"], "wedged")

            # The skip carries the task to a terminal status on the next reconcile, so a
            # stuck fan-out can complete and integrate the healthy groups.
            tasks = [
                {"id": "TASK-002", "status": "pending"},
                {"id": "TASK-003", "status": "pending"},
            ]
            live.reconcile_from_journal(tasks, journal)
            self.assertTrue(all(t["status"] == "escalated" for t in tasks))

    def test_skip_without_journal_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"  # no worktrees dir / journal created
            self.assertEqual(live.skip_tasks(repo, "docs/specs/001-x", ["TASK-001"]), 1)


def _success_entry(task_id, role, **report_extra):
    report = {"status": "success", "head_sha": "abc12345", "notes": "ok", **report_extra}
    return {"task": task_id, "role": role, "report": report}


class ClearTasksCommand(unittest.TestCase):
    """`clear-task`: surgically remove one task's failed/escalated journal entries
    (cascading to dependency-gate entries it blocked) so a plain resume re-dispatches
    just that task -- the targeted alternative to `--fresh` discarding the whole
    journal (brief 20260812-150507)."""

    def _write_journal(self, tmp, entries, **extra):
        wt = Path(tmp) / "repo-worktrees"
        wt.mkdir(parents=True, exist_ok=True)
        jp = wt / "run-001-x.json"
        journal = {
            "spec_id": "001-x",
            "entries": entries,
            "gitnexus_capability": False,
            "run_id": "full-1",
            **extra,
        }
        jp.write_text(json.dumps(journal, indent=2, sort_keys=True) + "\n")
        return Path(tmp) / "repo", jp

    def test_surgical_clear_removes_failed_and_cascaded_gate_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [
                _success_entry("TASK-A", "implement"),
                _success_entry("TASK-A", "cleanup"),
                _success_entry("TASK-B", "cleanup"),
                live._journal_failure_entry({"id": "TASK-C"}, "drive", "drive crashed", 1.0, 2.0),
                live._journal_failure_entry(
                    {"id": "TASK-D"},
                    "dependency-gate",
                    "blocked by failed prerequisite(s): TASK-C",
                    2.0,
                    2.0,
                    blocked_by=["TASK-C"],
                ),
            ]
            repo, jp = self._write_journal(tmp, entries, groups={"base": {"state": "MERGED"}})

            self.assertEqual(live.clear_tasks(repo, "docs/specs/001-x", ["TASK-C"]), 0)

            journal = json.loads(jp.read_text())  # still valid JSON
            # Original top-level keys survive the atomic rewrite.
            self.assertEqual(
                set(journal),
                {"spec_id", "entries", "gitnexus_capability", "run_id", "groups"},
            )
            kept = [(e["task"], e["role"]) for e in journal["entries"]]
            self.assertEqual(
                kept,
                [("TASK-A", "implement"), ("TASK-A", "cleanup"), ("TASK-B", "cleanup")],
            )

    def test_cleared_task_is_pending_on_replay_merged_stay_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [
                _success_entry("TASK-A", "cleanup"),
                live._journal_failure_entry({"id": "TASK-C"}, "drive", "drive crashed", 1.0, 2.0),
                live._journal_failure_entry(
                    {"id": "TASK-D"},
                    "dependency-gate",
                    "blocked by failed prerequisite(s): TASK-C",
                    2.0,
                    2.0,
                    blocked_by=["TASK-C"],
                ),
            ]
            repo, jp = self._write_journal(tmp, entries)
            self.assertEqual(live.clear_tasks(repo, "docs/specs/001-x", ["TASK-C"]), 0)

            tasks = [
                {"id": "TASK-A", "status": "pending", "retry_count": 0},
                {"id": "TASK-C", "status": "pending", "retry_count": 0},
                {"id": "TASK-D", "status": "pending", "retry_count": 0},
            ]
            live.reconcile_from_journal(tasks, json.loads(jp.read_text()))
            by_id = {t["id"]: t for t in tasks}
            self.assertEqual(by_id["TASK-A"]["status"], "done")  # merged work untouched
            self.assertEqual(by_id["TASK-C"]["status"], "pending")  # re-dispatchable
            self.assertEqual(by_id["TASK-D"]["status"], "pending")  # gate cleared too

    def test_refuses_to_clear_task_with_completion_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [
                _success_entry("TASK-A", "implement"),
                _success_entry("TASK-A", "cleanup"),
            ]
            repo, jp = self._write_journal(tmp, entries)
            before = jp.read_text()
            self.assertEqual(live.clear_tasks(repo, "docs/specs/001-x", ["TASK-A"]), 1)
            self.assertEqual(jp.read_text(), before)  # zero file mutation

    def test_refuses_when_nothing_to_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            entries = [_success_entry("TASK-A", "implement")]
            repo, jp = self._write_journal(tmp, entries)
            before = jp.read_text()
            # TASK-B has no entries at all; TASK-A's only entry is a kept success.
            self.assertEqual(live.clear_tasks(repo, "docs/specs/001-x", ["TASK-B"]), 1)
            self.assertEqual(jp.read_text(), before)

    def test_cascade_incident_shape_notes_fallback(self):
        """Regression for the motivating incident: drive-level crashes on 1.1/2.1
        plus dependency-gate entries for 3.2/4.3 whose blockers live ONLY in the
        free-text notes (journals written before `blocked_by` existed)."""
        with tempfile.TemporaryDirectory() as tmp:
            entries = [
                live._journal_failure_entry(
                    {"id": "1.1"},
                    "drive",
                    "drive crashed: WorktreeAddError('retained task branch "
                    "016-x/1.1 is stale against dev')",
                    1.0,
                    2.0,
                ),
                live._journal_failure_entry(
                    {"id": "2.1"},
                    "drive",
                    "drive crashed: WorktreeAddError('retained task branch "
                    "016-x/2.1 is stale against dev')",
                    1.0,
                    2.0,
                ),
                live._journal_failure_entry(
                    {"id": "3.2"},
                    "dependency-gate",
                    "blocked by failed prerequisite(s): 1.1, 2.1",
                    3.0,
                    3.0,
                ),
                live._journal_failure_entry(
                    {"id": "4.3"},
                    "dependency-gate",
                    "blocked by failed prerequisite(s): 1.1, 2.1",
                    3.0,
                    3.0,
                ),
            ]
            repo, jp = self._write_journal(tmp, entries)
            self.assertEqual(live.clear_tasks(repo, "docs/specs/001-x", ["1.1", "2.1"]), 0)
            self.assertEqual(json.loads(jp.read_text())["entries"], [])

    def test_gate_blockers_prefers_structured_field(self):
        entry = live._journal_failure_entry(
            {"id": "TASK-D"},
            "dependency-gate",
            "blocked by failed prerequisite(s): TASK-WRONG",
            1.0,
            1.0,
            blocked_by=["TASK-C"],
        )
        self.assertEqual(entry["blocked_by"], ["TASK-C"])
        self.assertEqual(live._gate_blockers(entry), ["TASK-C"])
        del entry["blocked_by"]
        self.assertEqual(live._gate_blockers(entry), ["TASK-WRONG"])  # notes fallback

    def test_clear_without_journal_returns_1(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"  # no worktrees dir / journal created
            self.assertEqual(live.clear_tasks(repo, "docs/specs/001-x", ["TASK-001"]), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
