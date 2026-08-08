#!/usr/bin/env python3
"""Unit tests for the sdd-workflow conductor state detector (dashboard.detect_stage).

Builds synthetic spec folders in a tmp dir to pin every row of the §4.3 state
machine, plus the real fixture spec for an end-to-end check. Stdlib unittest --
run with: python3 scripts/test_dashboard.py
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from worktrail.router import dashboard
from worktrail.router.policy import load_policy, resolve_routing

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _spec(
    dirpath: Path,
    *,
    spec_body: str | None = None,
    tasks: list[tuple[str, ...]] | None = None,
    technical_plan: bool = False,
    synced_kg: bool = False,
) -> Path:
    """Materialize a spec folder. `tasks` = list of (id, status) or (id, status, kind)
    -- kind defaults to impl. `synced_kg` writes a knowledge-graph.json carrying a
    spec-sync analysis source (i.e. sync has run)."""
    dirpath.mkdir(parents=True, exist_ok=True)
    if spec_body is not None:
        (dirpath / "2026-05-29--feature.md").write_text(spec_body)
    if technical_plan:
        (dirpath / "technical-plan.md").write_text("# Technical Plan\n")
    if tasks:
        td = dirpath / "tasks"
        td.mkdir(exist_ok=True)
        for row in tasks:
            tid, status = row[0], row[1]
            kind = row[2] if len(row) > 2 else "impl"
            (td / f"{tid}.md").write_text(
                f"---\nid: {tid}\nstatus: {status}\nkind: {kind}\ndependencies: []\n---\n# {tid}\n"
            )
    if synced_kg:
        (dirpath / "knowledge-graph.json").write_text(
            '{"metadata": {"spec_id": "x", "analysis_sources": '
            '[{"agent": "spec-sync", "timestamp": "2026-05-31T10:05:00Z", "mode": "full"}]}}'
        )
    return dirpath


def _add_change_tasks(spec_dir: Path, slug: str, tasks: list[tuple[str, ...]]) -> Path:
    """Add a changes/<slug>/tasks/ dir with TASK-*.md files under an existing spec_dir
    (mirrors _spec()'s tasks= writing, for the change-spec subtree)."""
    cd = spec_dir / "changes" / slug / "tasks"
    cd.mkdir(parents=True, exist_ok=True)
    for row in tasks:
        tid, status = row[0], row[1]
        kind = row[2] if len(row) > 2 else "impl"
        (cd / f"{tid}.md").write_text(
            f"---\nid: {tid}\nstatus: {status}\nkind: {kind}\ndependencies: []\n---\n# {tid}\n"
        )
    return cd


SPEC_MIN = "# Feature Specification: X\n\n**ID**: 001-x\n\n## Summary\nstuff\n"
SPEC_CLARIFIED = SPEC_MIN + "\n## Clarifications\n### Session 2026-05-29\n- Q/A\n"
SPEC_MARKERS = SPEC_MIN + "\n## Requirements\n- [NEEDS CLARIFICATION: which auth flow?]\n"
SPEC_BACKFILL = "# Feature: X\n\n**Status**: Backfill\n\n## Summary\ndocumented after.\n"
SPEC_IMPLEMENTED = "# Feature: X\n\n**Status**: Implemented\n\n## Summary\nall merged.\n"


class StateMachine(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def stage(self, **kw) -> dict:
        return dashboard.detect_stage(_spec(self.root / "001-x", **kw))

    def test_empty_no_spec(self):
        # bare folder (e.g. only a tasks dir was scaffolded), no spec, no request
        d = self.root / "001-x"
        d.mkdir()
        r = dashboard.detect_stage(d)
        self.assertEqual(r["stage"], "empty")
        self.assertEqual(r["next_action"], "brainstorm")

    def test_user_request_only_is_unspecd_backlog(self):
        # The ~44 datalena folders: user-request.md seeded, spec never produced.
        d = self.root / "001-x"
        d.mkdir()
        (d / "user-request.md").write_text("# User Request\n\n**Feature**: X\n")
        r = dashboard.detect_stage(d)
        self.assertEqual(r["stage"], "unspecd")
        self.assertIn("brainstorm", r["next_action"])
        self.assertTrue(r["has_user_request"])

    def test_user_request_with_reference_docs_is_unspecd_not_needs_tasks(self):
        # Real bug (datalena docs/specs/038-authentication): user-request.md plus
        # several colocated research/reference docs (architecture diagram,
        # investigation summaries) was misrouted to needs-tasks/spec-to-tasks
        # because find_spec_file() picked one of the reference docs as "the
        # spec". Must resolve to unspecd like any other spec-less backlog stub.
        d = self.root / "038-authentication"
        d.mkdir()
        (d / "user-request.md").write_text("# User Request\n\n**Feature**: Auth\n")
        (d / "architecture-diagram.md").write_text("# Architecture\n")
        (d / "implementation-checklist.md").write_text("# Checklist\n")
        (d / "world_id_executive_summary.md").write_text("# Summary\n")
        r = dashboard.detect_stage(d)
        self.assertEqual(r["stage"], "unspecd")
        self.assertIn("brainstorm", r["next_action"])

    def test_backfill_is_done_even_without_tasks(self):
        r = self.stage(spec_body=SPEC_BACKFILL)
        self.assertEqual(r["stage"], "done")
        self.assertIn("backfill", r["next_action"])

    def test_implemented_is_done_even_with_stale_journal(self):
        # Status: Implemented overrides a stale run journal that has
        # integrate_complete:True but groups still at state:OPEN (pre-MERGED-stamp runs).
        r = self.stage(spec_body=SPEC_IMPLEMENTED, tasks=[("TASK-001-01", "completed")])
        self.assertEqual(r["stage"], "done")

    def test_markers_present_needs_spec_check(self):
        # Unresolved [NEEDS CLARIFICATION] markers are the real spec-check signal.
        r = self.stage(spec_body=SPEC_MARKERS)
        self.assertEqual(r["stage"], "needs-clarification")
        self.assertEqual(r["next_action"], "spec-check")
        self.assertTrue(r["clarification_markers"])

    def test_no_markers_no_heading_is_ready_to_task(self):
        # A spec with no markers and no `## Clarifications` heading must NOT bounce
        # back to spec-check (small-scope specs legitimately skip it).
        r = self.stage(spec_body=SPEC_MIN)
        self.assertEqual(r["stage"], "needs-tasks")
        self.assertEqual(r["next_action"], "spec-to-tasks")
        self.assertFalse(r["clarification_markers"])

    def test_clarified_no_tasks_needs_tasks(self):
        r = self.stage(spec_body=SPEC_CLARIFIED)
        self.assertEqual(r["stage"], "needs-tasks")
        self.assertEqual(r["next_action"], "spec-to-tasks")
        self.assertEqual(r["technical_plan"], "missing")

    def test_technical_plan_detected_but_not_blocking(self):
        r = self.stage(spec_body=SPEC_CLARIFIED, technical_plan=True)
        self.assertEqual(r["stage"], "needs-tasks")  # plan is optional, not a gate
        self.assertEqual(r["technical_plan"], "present")

    def test_pending_tasks_route_to_orchestrator(self):
        r = self.stage(
            spec_body=SPEC_CLARIFIED, tasks=[("TASK-001", "completed"), ("TASK-002", "pending")]
        )
        self.assertEqual(r["stage"], "ready-to-implement")
        self.assertEqual(r["next_action"], "orchestrator")
        self.assertEqual(
            r["tasks"],
            {"total": 2, "completed": 1, "pending": 1, "pending_impl": 1, "pending_tail": 0},
        )

    def test_tasks_dominate_unrecognized_spec(self):
        # A task DAG present but no recognizable spec doc (only user-request.md) must
        # still be staged from its tasks, never mislabeled unspecd/empty.
        d = self.root / "001-x"
        d.mkdir()
        (d / "user-request.md").write_text("# User Request\n")
        td = d / "tasks"
        td.mkdir()
        (td / "TASK-001.md").write_text("---\nid: TASK-001\nstatus: pending\nkind: impl\n---\n")
        r = dashboard.detect_stage(d)
        self.assertEqual(r["stage"], "ready-to-implement")

    def test_tasks_dominate_missing_clarifications(self):
        # small-scope spec: tasks but spec-check skipped -> must NOT bounce to spec-check
        r = self.stage(spec_body=SPEC_MIN, tasks=[("TASK-001", "pending")])
        self.assertEqual(r["next_action"], "orchestrator")

    def test_all_tasks_completed_and_synced_is_complete(self):
        r = self.stage(
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed"), ("TASK-002", "completed")],
            synced_kg=True,
        )
        self.assertEqual(r["stage"], "complete")
        self.assertIn("PR", r["next_action"])

    def test_verify_pending_when_integrate_complete_and_open_pr(self):
        # AC-029: All tasks completed, but journal has integrate_complete: true
        # and at least one group with state != "MERGED"
        spec_dir = _spec(
            self.root / "001-verify-pending" / "docs" / "specs" / "001-verify-pending",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed"), ("TASK-002", "completed")],
        )
        # Create the journal file at the expected path
        repo = spec_dir.parent.parent.parent
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"run-{spec_dir.name}.json"
        journal_path.write_text("""
{
  "run_id": "full-1234567890",
  "integrate_complete": true,
  "groups": {
    "base": {
      "pr_url": "https://github.com/test/repo/pull/1",
      "head_branch": "full-1234567890/base",
      "state": "OPEN"
    },
    "feature-a": {
      "pr_url": "https://github.com/test/repo/pull/2",
      "head_branch": "full-1234567890/feature-a",
      "state": "MERGED"
    }
  }
}
""")
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "verify-pending")
        self.assertIn("full-real", r["next_action"])
        self.assertIn("verify", r["next_action"])

    def test_complete_when_all_groups_merged(self):
        # When journal exists with integrate_complete: true but all groups are MERGED,
        # should return stage="complete" (not "verify-pending")
        spec_dir = _spec(
            self.root / "002-all-merged" / "docs" / "specs" / "002-all-merged",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
            synced_kg=True,
        )
        repo = spec_dir.parent.parent.parent
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"run-{spec_dir.name}.json"
        journal_path.write_text("""
{
  "run_id": "full-9999999999",
  "integrate_complete": true,
  "groups": {
    "base": {
      "pr_url": "https://github.com/test/repo/pull/10",
      "head_branch": "full-9999999999/base",
      "state": "MERGED"
    }
  }
}
""")
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "complete")
        self.assertIn("PR", r["next_action"])

    def test_verify_pending_false_when_group_merged_on_base_despite_stale_state(self):
        # Regression for the spec-076 closeout drift: a group's PR actually merged
        # (its squash-merge commit landed on the base branch) but the run journal's
        # per-group `state` was never re-stamped MERGED (e.g. an out-of-band merge).
        # The base-branch git log already carries the "(#N)" merge commit, so the
        # spec must not be stuck in verify-pending forever.
        spec_dir = _spec(
            self.root / "008-git-merged" / "docs" / "specs" / "008-git-merged",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
            synced_kg=True,
        )
        repo = spec_dir.parent.parent.parent
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "feat(spec-008): ship feature (#1573)"],
            cwd=repo,
            check=True,
        )
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"run-{spec_dir.name}.json"
        journal_path.write_text(
            json.dumps(
                {
                    "run_id": "full-1573",
                    "integrate_complete": True,
                    "groups": {
                        "base": {
                            "pr_url": "https://github.com/test/repo/pull/1573",
                            "head_branch": "full-1573/base",
                            "state": "OPEN",
                        }
                    },
                }
            )
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "complete")

    def test_complete_when_no_journal_exists(self):
        # No journal file → unchanged behavior
        spec_dir = _spec(
            self.root / "003-no-journal",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
            synced_kg=True,
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "complete")
        self.assertIn("PR", r["next_action"])

    def test_complete_when_journal_lacks_integrate_complete(self):
        # Journal exists but integrate_complete not set → unchanged behavior
        spec_dir = _spec(
            self.root / "004-journal-no-integrate-complete" / "docs" / "specs" / "004-journal-no-integrate-complete",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
            synced_kg=True,
        )
        repo = spec_dir.parent.parent.parent
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"run-{spec_dir.name}.json"
        journal_path.write_text('{"run_id": "full-1111111111", "entries": []}')
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "complete")
        self.assertIn("PR", r["next_action"])

    def test_complete_when_journal_invalid_json(self):
        # Journal file exists but is not valid JSON → should not raise, treated as no journal
        spec_dir = _spec(
            self.root / "005-invalid-json" / "docs" / "specs" / "005-invalid-json",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
            synced_kg=True,
        )
        repo = spec_dir.parent.parent.parent
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"run-{spec_dir.name}.json"
        journal_path.write_text("not valid json {")
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "complete")  # Falls through to complete
        self.assertIn("PR", r["next_action"])

    def test_complete_when_journal_has_no_groups(self):
        # Journal has integrate_complete but no groups key → treated as 0 OPEN groups
        spec_dir = _spec(
            self.root / "006-no-groups" / "docs" / "specs" / "006-no-groups",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
            synced_kg=True,
        )
        repo = spec_dir.parent.parent.parent
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_path = journal_dir / f"run-{spec_dir.name}.json"
        journal_path.write_text('{"run_id": "full-2222222222", "integrate_complete": true}')
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "complete")
        self.assertIn("PR", r["next_action"])

    def _spec_with_journal(self, name: str, tasks: list, journal: str) -> Path:
        """Materialize a nested spec folder + its run journal at the path the
        dashboard derives (<repo>-worktrees/run-<spec>.json)."""
        spec_dir = _spec(
            self.root / name / "docs" / "specs" / name,
            spec_body=SPEC_CLARIFIED,
            tasks=tasks,
        )
        repo = spec_dir.parent.parent.parent
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / f"run-{spec_dir.name}.json").write_text(journal)
        return spec_dir

    def _write_status(self, spec_dir: Path, payload: dict) -> None:
        repo = spec_dir.parent.parent.parent
        status_dir = repo.parent / f"{repo.name}-worktrees"
        status_dir.mkdir(parents=True, exist_ok=True)
        (status_dir / f"run-{spec_dir.name}.status.json").write_text(json.dumps(payload))

    # All groups merged, but the journal records held-out tail (e2e/cleanup) tasks.
    _JOURNAL_TAIL = """
{
  "run_id": "full-3333333333",
  "integrate_complete": true,
  "pending_tail_tasks": ["TASK-022", "TASK-023"],
  "pending_tail_reason": "tail-kind tasks (e2e/cleanup) ... run after the group PRs merge",
  "groups": {
    "base": {"pr_url": "https://github.com/t/r/pull/1", "head_branch": "x/base", "state": "MERGED"}
  }
}
"""

    def test_tail_only_pending_is_tail_pending(self):
        # Impl tasks merged + tail tasks (e2e/cleanup) still pending → tail-pending,
        # NOT ready-to-implement (the old loop bug), and surfaces the recorded ids.
        spec_dir = self._spec_with_journal(
            "010-tail",
            [("TASK-001", "completed"), ("TASK-022", "pending", "e2e"),
             ("TASK-023", "pending", "cleanup")],
            self._JOURNAL_TAIL,
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "tail-pending")
        self.assertNotEqual(r["next_action"], "orchestrator")
        self.assertIn("TASK-022", r["next_action"])
        self.assertIn("TASK-023", r["next_action"])
        self.assertEqual(r["tasks"]["pending_impl"], 0)
        self.assertEqual(r["tasks"]["pending_tail"], 2)

    def test_impl_pending_outranks_tail(self):
        # A real impl task still pending keeps the spec on the orchestrator path even
        # when tail tasks are also pending (pending_impl > 0 dominates).
        spec_dir = self._spec_with_journal(
            "011-impl-and-tail",
            [("TASK-001", "completed"), ("TASK-002", "pending"),
             ("TASK-022", "pending", "e2e")],
            self._JOURNAL_TAIL,
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "ready-to-implement")
        self.assertEqual(r["next_action"], "orchestrator")

    def test_fanout_failed_pending_impl_is_orchestrator_stuck(self):
        spec_dir = self._spec_with_journal(
            "013-fanout-failed",
            [("TASK-001", "completed"), ("TASK-002", "pending")],
            '{"run_id": "full-5555555555", "groups": {}}',
        )
        self._write_status(spec_dir, {"phase": "fanout_failed"})
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "orchestrator-stuck")
        self.assertIn("fanout_failed", r["next_action"])

    def test_tail_pending_yields_to_verify(self):
        # Tail pending but a group PR is still OPEN → verify-pending wins (merge first,
        # tail after), so the run is resumed rather than declared tail-ready early.
        journal_open = """
{
  "run_id": "full-4444444444",
  "integrate_complete": true,
  "pending_tail_tasks": ["TASK-022"],
  "groups": {
    "base": {"pr_url": "https://github.com/t/r/pull/2", "head_branch": "x/base", "state": "OPEN"}
  }
}
"""
        spec_dir = self._spec_with_journal(
            "012-tail-open",
            [("TASK-001", "completed"), ("TASK-022", "pending", "e2e")],
            journal_open,
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "verify-pending")

    def test_change_spec_tail_pending_not_shadowed_by_completed_parent_tasks(self):
        # Parent spec's own tasks/ is fully completed; a later change-spec adds its
        # own tail (e2e/cleanup) tasks that are still pending. The union must surface
        # them as tail-pending, not silently drop them because tasks/ already "won"
        # (brief 20260713-201900 / live GGB spec 026-authenticated-feedback-capture).
        spec_dir = _spec(
            self.root / "001-x",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
        )
        _add_change_tasks(
            spec_dir,
            "feedback-send-to-dev-action",
            [("TASK-CHG-001", "completed"), ("TASK-CHG-002", "pending", "e2e")],
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "tail-pending")
        self.assertIn("TASK-CHG-002", r["next_action"])
        self.assertEqual(
            r["tasks"],
            {"total": 3, "completed": 2, "pending": 1, "pending_impl": 0, "pending_tail": 1},
        )

    def test_implemented_header_defers_to_pending_change_tail(self):
        # Same shape as above, but the parent spec doc carries Status: Implemented --
        # the fast-path "done" must not win when the (unioned) counts still have
        # pending work from a later change. This is the exact live GGB 026 shape:
        # the header predates the change-spec and must not permanently mask it.
        spec_dir = _spec(
            self.root / "001-x",
            spec_body=SPEC_IMPLEMENTED,
            tasks=[("TASK-001", "completed")],
        )
        _add_change_tasks(
            spec_dir,
            "feedback-send-to-dev-action",
            [("TASK-CHG-001", "completed"), ("TASK-CHG-002", "pending", "e2e")],
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "tail-pending")

    def test_completed_but_unsynced_is_sync_pending(self):
        # All tasks completed, no knowledge-graph.json -> sync never ran -> surface sync
        # as the next action before the spec is closed out (else the KG silently drifts).
        r = self.stage(
            spec_body=SPEC_CLARIFIED, tasks=[("TASK-001", "completed"), ("TASK-002", "completed")]
        )
        self.assertEqual(r["stage"], "sync-pending")
        self.assertIn("sync", r["next_action"])

    def test_completed_with_unsynced_kg_is_sync_pending(self):
        # A KG exists but carries no spec-sync analysis source -> sync hasn't run.
        spec = _spec(
            self.root / "001-x",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
        )
        (spec / "knowledge-graph.json").write_text(
            '{"metadata": {"spec_id": "x", "analysis_sources": []}}'
        )
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "sync-pending")

    def test_sync_pending_yields_to_verify_pending(self):
        # verify-pending (unmerged PRs) outranks sync-pending: don't ask for sync before
        # the PRs are even merged.
        spec_dir = _spec(
            self.root / "007-verify-first" / "docs" / "specs" / "007-verify-first",
            spec_body=SPEC_CLARIFIED,
            tasks=[("TASK-001", "completed")],
        )  # no KG -> would be sync-pending, but an open-PR journal must win
        repo = spec_dir.parent.parent.parent
        journal_dir = repo.parent / f"{repo.name}-worktrees"
        journal_dir.mkdir(parents=True, exist_ok=True)
        (journal_dir / f"run-{spec_dir.name}.json").write_text(
            '{"integrate_complete": true, "groups": {"base": {"pr_url": "u", "state": "OPEN"}}}'
        )
        r = dashboard.detect_stage(spec_dir)
        self.assertEqual(r["stage"], "verify-pending")


class ScanFiltering(unittest.TestCase):
    def test_scan_skips_shared_docs_and_sorts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _spec(root / "002-b", spec_body=SPEC_MIN)
            _spec(root / "001-a", spec_body=SPEC_CLARIFIED)
            (root / "architecture.md").write_text("# arch\n")  # shared, not a spec
            (root / "knowledge-graph.json").write_text("{}")
            rows = dashboard.scan(root)
            self.assertEqual([r["id"] for r in rows], ["001-a", "002-b"])

    def test_scan_skips_non_spec_dirs(self):
        # addenda/research hold loose .md that must NOT be read as a spec doc.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _spec(root / "001-real", spec_body=SPEC_MIN)
            (root / "addenda").mkdir()
            (root / "addenda" / "architecture-x.md").write_text("# extra\n")
            (root / "research").mkdir()
            (root / "research" / "001-spike.md").write_text("# spike\n")
            rows = dashboard.scan(root)
            self.assertEqual([r["id"] for r in rows], ["001-real"])

    def test_scan_includes_user_request_only_backlog(self):
        # Regression: a folder with only user-request.md (no spec doc, no tasks) must
        # be scanned and classified `unspecd`, not silently dropped by _is_spec_folder.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "005-backlog").mkdir()
            (root / "005-backlog" / "user-request.md").write_text("# User Request\n")
            rows = dashboard.scan(root)
            self.assertEqual([r["id"] for r in rows], ["005-backlog"])
            self.assertEqual(rows[0]["stage"], "unspecd")

    def test_empty_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dashboard.scan(Path(tmp)), [])

    def test_scan_includes_openspec_changes_next_to_legacy_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            root = repo / "docs" / "specs"
            _spec(root / "001-legacy", spec_body=SPEC_MIN)
            change = repo / "openspec" / "changes" / "add-export"
            change.mkdir(parents=True)
            (change / "tasks.md").write_text(
                "## 1. Export\n\n- [ ] 1.1 Add exporter\n"
            )
            rows = dashboard.scan(root)
            assert [row["id"] for row in rows] == ["001-legacy", "add-export"]
            openspec = rows[1]
            assert openspec["format"] == "openspec"
            assert openspec["path"] == str(change)
            assert openspec["stage"] == "ready-to-implement"

    def test_scan_finds_openspec_when_docs_specs_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            change = repo / "openspec" / "changes" / "first-change"
            change.mkdir(parents=True)
            (change / "tasks.md").write_text(
                "## 1. Start\n\n- [ ] 1.1 Create the first task\n"
            )
            rows = dashboard.scan(repo / "docs" / "specs")
            assert [row["id"] for row in rows] == ["first-change"]


class NonSpecDirsGitignoreSync(unittest.TestCase):
    """artifact-policy.md documents which _NON_SPEC_DIRS entries are gitignored
    SDD scratch (PR #195: _ralph_loop, reviews). A rename on either side would
    silently desync them again with nothing to catch it."""

    def test_gitignored_scratch_dirs_are_non_spec_dirs(self):
        policy_text = (
            REPO_ROOT / "skills" / "worktrail-go" / "references" / "artifact-policy.md"
        ).read_text()
        gitignore_paragraph = policy_text.split("**Gitignore", 1)[1].split(
            "Rationale:", 1
        )[0]
        scratch_dirs = set(re.findall(r"`([\w.-]+)/", gitignore_paragraph))
        self.assertEqual(scratch_dirs, {"reviews", "_ralph_loop"})
        self.assertTrue(
            scratch_dirs.issubset(dashboard._NON_SPEC_DIRS),
            f"artifact-policy.md's gitignored scratch dirs {scratch_dirs} are not all "
            f"in dashboard._NON_SPEC_DIRS ({dashboard._NON_SPEC_DIRS}) -- "
            "update whichever side was renamed",
        )


class SpecFileDiscovery(unittest.TestCase):
    """find_spec_file recognizes every naming era and excludes auxiliaries."""

    def _dir(self, names):
        d = Path(self._tmp.name) / "spec"
        d.mkdir(exist_ok=True)
        for n in names:
            (d / n).write_text("# x\n")
        return d

    def _found(self, names) -> str:
        f = dashboard.find_spec_file(self._dir(names))
        assert f is not None, "expected a spec doc to be found"
        return f.name

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_dated_wins_newest(self):
        self.assertEqual(self._found(["2026-05-29--x.md", "2026-06-14--x.md", "spec.md"]),
                         "2026-06-14--x.md")

    def test_plain_spec_md_recognized(self):
        # The live bug: spec.md folders were mislabeled empty/brainstorm.
        self.assertEqual(self._found(["spec.md"]), "spec.md")

    def test_uppercase_and_brainstorm_legacy(self):
        # SPEC.md preferred over brainstorm.md
        self.assertEqual(self._found(["SPEC.md", "brainstorm.md"]), "SPEC.md")

    def test_review_substring_midname_not_excluded(self):
        # Regression: a dated spec whose name contains "-review-" mid-string must be
        # recognized (this mislabeled spec 004 as empty under substring matching).
        self.assertEqual(self._found(["2026-05-31--orchestrator-review-writer-path.md"]),
                         "2026-05-31--orchestrator-review-writer-path.md")

    def test_auxiliaries_never_chosen(self):
        # Only auxiliary files present -> no spec doc.
        d = self._dir(["user-request.md", "data-model.md", "decision-log.md",
                       "traceability-matrix.md", "2026-06-09--x--tasks.md",
                       "TASK-001-review.md", "2026-05-30--technical-plan.md",
                       "brainstorming-notes.md", "spec-check.md", "TASKS.md"])
        self.assertIsNone(dashboard.find_spec_file(d))

    def test_ambiguous_reference_docs_not_guessed_as_spec(self):
        # Real bug (datalena docs/specs/038-authentication): a backlog stub with
        # user-request.md plus several colocated research/reference docs (none
        # dated, none matching the spec.md/-specs.md/brainstorm.md legacy names)
        # had "architecture-diagram.md" picked as "the spec" by alphabetical
        # tie-break, misrouting the folder to spec-to-tasks instead of unspecd.
        # With 2+ candidates tied at the lowest rank, none of them carries any
        # naming-convention evidence of being the real spec -- refuse to guess.
        d = self._dir(["user-request.md", "architecture-diagram.md",
                       "implementation-checklist.md", "world_id_executive_summary.md",
                       "world_id_integration_assessment.md",
                       "world_id_technical_implementation.md"])
        self.assertIsNone(dashboard.find_spec_file(d))

    def test_single_unconventional_name_still_recognized(self):
        # A lone non-dated, non-legacy-named .md file is still trusted as the
        # spec doc -- the ambiguity guard only fires when 2+ candidates tie.
        self.assertEqual(self._found(["auth-redesign.md"]), "auth-redesign.md")


class Constitution(unittest.TestCase):
    def test_missing_constitution_produces_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            con = dashboard.constitution_status(root)
            self.assertEqual(con, {"architecture": False, "ontology": False})
            out = dashboard.render_dashboard(None, [], [], [], None, con)
            self.assertIn("Constitution: missing", out)

    def test_partial_constitution_lists_only_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture.md").write_text("# arch\n")
            con = dashboard.constitution_status(root)
            hint = dashboard._constitution_hint(con)
            assert hint is not None
            self.assertIn("ontology.md", hint)
            self.assertNotIn("architecture.md", hint)

    def test_complete_constitution_is_silent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "architecture.md").write_text("# arch\n")
            (root / "ontology.md").write_text("# ontology\n")
            con = dashboard.constitution_status(root)
            self.assertIsNone(dashboard._constitution_hint(con))


class ReposScan(unittest.TestCase):
    """Multi-repo overview: scan_repos over a parent holding several git repos."""

    @staticmethod
    def _repo(parent: Path, name: str) -> Path:
        repo = parent / name
        (repo / ".git").mkdir(parents=True)  # makes is_git_repo() true
        return repo

    def test_orders_active_first_then_by_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            # repo-a: a spec needing spec-check (active)
            a = self._repo(parent, "repo-a")
            _spec(a / "docs" / "specs" / "001-x", spec_body=SPEC_MIN)
            # repo-b: only a backfill spec (done, not active)
            b = self._repo(parent, "repo-b")
            _spec(b / "docs" / "specs" / "001-y", spec_body=SPEC_BACKFILL)
            # repo-c: no docs/specs at all
            self._repo(parent, "repo-c")
            rows = dashboard.scan_repos(parent)
            self.assertEqual([r["repo"] for r in rows], ["repo-a", "repo-b", "repo-c"])
            ra = next(r for r in rows if r["repo"] == "repo-a")
            self.assertEqual(ra["active"], 1)
            self.assertEqual(ra["active_ids"], ["001-x"])
            rb = next(r for r in rows if r["repo"] == "repo-b")
            self.assertTrue(rb["has_specs"])
            self.assertEqual(rb["active"], 0)
            self.assertEqual(rb["total"], 1)
            rc = next(r for r in rows if r["repo"] == "repo-c")
            self.assertFalse(rc["has_specs"])

    def test_render_dashboard_groups_active_by_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            a = self._repo(parent, "repo-a")
            _spec(a / "docs" / "specs" / "001-x", spec_body=SPEC_MIN)  # needs-tasks
            self._repo(parent, "repo-c")
            out = dashboard.render_dashboard(dashboard.scan_repos(parent),
                                             None, [], [])
            self.assertIn("📋 Active work", out)
            self.assertIn("Needs tasking", out)
            self.assertIn("repo-a 001-x", out)

    def test_no_repos_under_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(dashboard.scan_repos(Path(tmp)), [])
            out = dashboard.render_dashboard([], None, [], [])
            self.assertIn("No active specs", out)

    def test_render_dashboard_surfaces_all_provider_capacity_gate(self):
        out = dashboard.render_dashboard(
            [], None, [], [], capacity={
                "configured": ["claude:sonnet", "opencode:safe/model"],
                "gated": [
                    {"provider": "claude:sonnet", "failure_class": "transport",
                     "retry_after": "2026-07-20T21:00:00+00:00"},
                    {"provider": "opencode:safe/model", "failure_class": "auth",
                     "retry_after": "2026-07-20T22:00:00+00:00"},
                ],
                "all_gated": True,
                "retry_after": "2026-07-20T21:00:00+00:00",
            }
        )
        self.assertIn("Headless capacity blocked", out)
        self.assertIn("transport", out)
        self.assertIn("retry after 2026-07-20T21:00:00+00:00", out)

    def test_render_dashboard_surfaces_postmerge_check_failures(self):
        out = dashboard.render_dashboard(
            [], None, [], [], postmerge_check_failures={
                "repos_flagged": 1,
                "prs_flagged": 1,
                "flagged": [
                    {
                        "repo": "repo-a",
                        "url": "https://github.com/org/repo-a/pull/42",
                        "failing_checks": ["ci/build"],
                        "merged_at": "2026-08-01T00:00:00Z",
                    },
                ],
            }
        )
        self.assertIn("Post-merge check failures", out)
        self.assertIn("repo-a#42", out)

    def test_render_dashboard_omits_postmerge_line_when_empty(self):
        out = dashboard.render_dashboard(
            [], None, [], [],
            postmerge_check_failures={"repos_flagged": 0, "prs_flagged": 0, "flagged": []},
        )
        self.assertNotIn("Post-merge check failures", out)
        out_none = dashboard.render_dashboard([], None, [], [], postmerge_check_failures=None)
        self.assertNotIn("Post-merge check failures", out_none)

    def test_worktrees_reported_not_overlaid(self):
        # A worktree's docs/specs must NOT resurface a spec as active (overlay
        # removed); the worktree is reported by name for the cleanup action.
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            a = self._repo(parent, "repo-a")
            _spec(a / "docs" / "specs" / "001-x", spec_body=SPEC_BACKFILL)  # base: done
            wt = parent / "repo-a-worktrees" / "feature-x"
            (wt / ".git").mkdir(parents=True)
            _spec(wt / "docs" / "specs" / "001-x", spec_body=SPEC_MIN)  # would be active
            rows = dashboard.scan_repos(parent)
            ra = next(r for r in rows if r["repo"] == "repo-a")
            self.assertEqual(ra["active"], 0)  # worktree state NOT overlaid
            self.assertIn("feature-x", ra["worktrees"])

    def test_policy_contamination_surfaced_in_repos_and_rendered(self):
        # route:J go-policy-integrity-guards audit — a repo whose go-policy.yaml
        # is a copy-paste of a sibling's must show up as a flagged repo row AND
        # a one-line dashboard nudge, without touching the clean sibling.
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            contaminated = self._repo(parent, "repo-a")
            clean_sibling = self._repo(parent, "repo-b")
            policy_dir = contaminated / "docs" / "specs"
            policy_dir.mkdir(parents=True)
            (policy_dir / "go-policy.yaml").write_text(
                '# go conductor / parallel-orchestrator policy for repo-b.\n'
                'pre_pr_cmd: "${REPO_B_VENV:-/home/x/projects/repo-b/.venv}/bin/pytest"\n'
            )
            (clean_sibling / "docs" / "specs").mkdir(parents=True)
            (clean_sibling / "docs" / "specs" / "go-policy.yaml").write_text(
                '# go conductor / parallel-orchestrator policy for repo-b.\n'
                'pre_pr_cmd: "${REPO_B_VENV:-/home/x/projects/repo-b/.venv}/bin/pytest"\n'
            )
            rows = dashboard.scan_repos(parent)
            ra = next(r for r in rows if r["repo"] == "repo-a")
            rb = next(r for r in rows if r["repo"] == "repo-b")
            self.assertTrue(ra["policy_findings"])
            self.assertEqual(rb["policy_findings"], [])
            out = dashboard.render_dashboard(rows, None, [], [])
            self.assertIn("🚩 Policy contamination (3): repo-a", out)
            self.assertNotIn("repo-b (", out)

    def test_policy_drift_surfaced_in_repos_and_rendered(self):
        # route:A go-policy-drift-guard — a repo whose go-policy.yaml no longer
        # describes reality (tests exist that no runner reaches) must show up as
        # a flagged row AND a one-line nudge, leaving the honest sibling alone.
        # Needs real git repos: the detector's file source is `git ls-files`.
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            for name, cmd in (("repo-a", "npm run lint"), ("repo-b", "pytest -q")):
                repo = parent / name
                repo.mkdir(parents=True)
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
                specs = repo / "docs" / "specs"
                specs.mkdir(parents=True)
                (specs / "go-policy.yaml").write_text(
                    f'# go conductor policy for {name}.\npre_pr_cmd: "{cmd}"\n'
                )
                (repo / "tests").mkdir()
                (repo / "tests" / "test_thing.py").write_text("# test\n")
                subprocess.run(["git", "add", "-A"], cwd=repo, check=True)

            rows = dashboard.scan_repos(parent)
            ra = next(r for r in rows if r["repo"] == "repo-a")
            rb = next(r for r in rows if r["repo"] == "repo-b")
            self.assertEqual(
                {f["signal"] for f in ra["drift_findings"]}, {"orphaned-tests"}
            )
            self.assertEqual(rb["drift_findings"], [])
            out = dashboard.render_dashboard(rows, None, [], [])
            self.assertIn("🚩 Policy drift (1): repo-a (orphaned-tests)", out)

    def test_quarantine_findings_surfaced_in_repos_and_rendered(self):
        # A QUARANTINED group in a repo's run journal must show up as a
        # flagged row AND a one-line dashboard nudge, leaving a clean sibling
        # (no journal at all) alone.
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            quarantined = self._repo(parent, "repo-a")
            self._repo(parent, "repo-b")
            worktrees_dir = parent / "repo-a-worktrees"
            worktrees_dir.mkdir(parents=True)
            (worktrees_dir / "run-001-x.json").write_text(
                json.dumps(
                    {
                        "groups": {
                            "group-1": {
                                "state": "QUARANTINED",
                                "pr_url": "https://example.com/pull/1",
                            }
                        }
                    }
                )
            )
            rows = dashboard.scan_repos(parent)
            ra = next(r for r in rows if r["repo"] == "repo-a")
            rb = next(r for r in rows if r["repo"] == "repo-b")
            self.assertEqual(len(ra["quarantine_findings"]), 1)
            finding = ra["quarantine_findings"][0]
            self.assertEqual(finding["spec_id"], "001-x")
            self.assertEqual(finding["group"], "group-1")
            self.assertEqual(rb["quarantine_findings"], [])
            out = dashboard.render_dashboard(rows, None, [], [])
            self.assertIn("🚩 Quarantined groups (1): repo-a (001-x/group-1", out)
            self.assertIn("→ review", out)

    def test_quarantine_flags_line_omitted_when_no_findings(self):
        # No repo has a QUARANTINED group anywhere -- the rendered dashboard
        # must not gain a "Quarantined groups" line at all.
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            self._repo(parent, "repo-a")
            self._repo(parent, "repo-b")
            rows = dashboard.scan_repos(parent)
            for r in rows:
                self.assertEqual(r["quarantine_findings"], [])
            out = dashboard.render_dashboard(rows, None, [], [])
            self.assertNotIn("Quarantined groups", out)


class CategoryPickerAndRender(unittest.TestCase):
    """The deterministic two-level category picker and compact render."""

    @staticmethod
    def _spec_row(spec_id, stage, next_action="x"):
        return {"id": spec_id, "stage": stage, "next_action": next_action,
                "feature_summary": None, "tasks": None}

    # --- build_category_actions tests ---

    def test_category_actions_ready_and_tasks(self):
        rows = [
            self._spec_row("001", "ready-to-implement", "orchestrator"),
            self._spec_row("002", "needs-tasks", "spec-to-tasks"),
        ]
        cats = dashboard.build_category_actions(None, rows, inflight=[], queue_briefs=[])
        labels = [c["label"] for c in cats]
        self.assertTrue(any("Ready" in l for l in labels))
        self.assertTrue(any("Needs tasking" in l for l in labels))
        self.assertTrue(any("New work" in l for l in labels))
        # numbered sequentially
        self.assertEqual([c["n"] for c in cats], list(range(1, len(cats) + 1)))

    def test_category_actions_omits_empty_categories(self):
        # No active specs → only New work
        cats = dashboard.build_category_actions(None, [], inflight=[], queue_briefs=[])
        self.assertEqual(len(cats), 1)
        self.assertEqual(cats[0]["category"], "new-work")

    def test_category_actions_workqueue_included_when_present(self):
        brief = {"filename": "20260617-001.md", "focus": "fix login", "claimed_at": "2026-06-17T00:00:00"}
        cats = dashboard.build_category_actions(None, [], inflight=[brief], queue_briefs=[])
        self.assertTrue(any(c["category"] == "workqueue" for c in cats))

    def test_category_actions_caps_at_four(self):
        # All four categories populated
        rows = [
            self._spec_row("001", "ready-to-implement", "orchestrator"),
            self._spec_row("002", "needs-tasks", "spec-to-tasks"),
        ]
        brief = {"filename": "20260617-001.md", "focus": "fix login", "claimed_at": "2026-06-17T00:00:00"}
        cats = dashboard.build_category_actions(None, rows, inflight=[brief], queue_briefs=[])
        self.assertLessEqual(len(cats), 4)

    # --- build_category_items tests ---

    def test_category_items_ready_contains_actionable_specs(self):
        rows = [
            self._spec_row("000", "orchestrator-stuck", "manual recovery"),
            self._spec_row("001", "ready-to-implement", "orchestrator"),
            self._spec_row("002", "verify-pending", "resume full-real"),
            self._spec_row("003", "needs-tasks", "spec-to-tasks"),
        ]
        items = dashboard.build_category_items(None, rows, inflight=[], queue_briefs=[])
        ready = items.get("ready", [])
        self.assertEqual(len(ready), 3)
        self.assertTrue(all(i["stage"] in dashboard._READY_STAGES for i in ready))

    def test_category_items_needs_tasks_separated(self):
        rows = [self._spec_row(f"00{i}", "needs-tasks", "spec-to-tasks") for i in range(6)]
        items = dashboard.build_category_items(None, rows, inflight=[], queue_briefs=[])
        self.assertLessEqual(len(items["needs-tasks"]), 4)
        self.assertTrue(all(i["stage"] in dashboard._TASK_STAGES for i in items["needs-tasks"]))

    def test_category_items_numbered_per_category(self):
        rows = [
            self._spec_row("001", "ready-to-implement", "orchestrator"),
            self._spec_row("002", "needs-tasks", "spec-to-tasks"),
        ]
        items = dashboard.build_category_items(None, rows, inflight=[], queue_briefs=[])
        self.assertEqual(items["ready"][0]["n"], 1)
        self.assertEqual(items["needs-tasks"][0]["n"], 1)

    def test_category_items_new_work_always_present(self):
        items = dashboard.build_category_items(None, [], inflight=[], queue_briefs=[], backlog_total=5)
        self.assertTrue(len(items["new-work"]) >= 1)
        self.assertTrue(any(i["action"] == "brainstorm" for i in items["new-work"]))
        self.assertTrue(any(i["action"] == "see-backlog" for i in items["new-work"]))

    def test_category_items_carry_dispatch_data(self):
        rows = [self._spec_row("001", "ready-to-implement", "orchestrator")]
        items = dashboard.build_category_items(None, rows, inflight=[], queue_briefs=[])
        item = items["ready"][0]
        self.assertEqual(item["action"], "implement")
        self.assertEqual(item["spec_id"], "001")
        self.assertIn("next_action", item)
        self.assertIn("stage", item)

    def test_category_items_queue_item_planned_agent_matches_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            specs = repo / "docs" / "specs"
            specs.mkdir(parents=True)
            (specs / "go-policy.yaml").write_text(
                "agent_cli: codex\n"
                "routing:\n"
                "  defaults:\n"
                "    B:\n"
                "      medium:\n"
                "        agent_cli: claude\n"
            )
            briefs = [{
                "filename": "queued.md",
                "focus": "claim me",
                "repo": str(repo),
                "route": "B",
                "risk": "medium",
            }]
            items = dashboard.build_category_items(None, [], [], briefs)
            policy = load_policy(repo)
            self.assertEqual(
                items["workqueue"][0]["planned-agent"],
                resolve_routing(policy, "B", "medium")["agent_cli"],
            )

    def test_category_items_spec_item_planned_agent_matches_routing(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            specs = repo / "docs" / "specs"
            specs.mkdir(parents=True)
            (specs / "go-policy.yaml").write_text(
                "agent_cli: codex\n"
                "routing:\n"
                "  defaults:\n"
                "    C:\n"
                "      low:\n"
                "        agent_cli: opencode\n"
            )
            rows = [{
                "repo": "repo-a",
                "path": str(repo),
                "active_specs": [{
                    "id": "001",
                    "stage": "ready-to-implement",
                    "next_action": "orchestrator",
                    "route": "C",
                    "risk": "low",
                }],
            }]
            items = dashboard.build_category_items(rows, None, inflight=[], queue_briefs=[])
            policy = load_policy(repo)
            self.assertEqual(
                items["ready"][0]["planned-agent"],
                resolve_routing(policy, "C", "low")["agent_cli"],
            )

    def test_rendered_text_unchanged_with_routing_inputs(self):
        base_rows = [self._spec_row("001", "ready-to-implement", "orchestrator")]
        routed_rows = [dict(base_rows[0], route="B", risk="medium")]
        baseline = dashboard.render_dashboard(None, base_rows, [], [])
        self.assertEqual(baseline, dashboard.render_dashboard(None, routed_rows, [], []))

    def test_planned_agent_load_failure_falls_back_without_raising(self):
        brief = {"filename": "queued.md", "focus": "claim me", "route": "B", "risk": "medium"}
        if hasattr(dashboard._load_dashboard_policy, "cache_clear"):
            dashboard._load_dashboard_policy.cache_clear()
        with mock.patch.object(dashboard, "_load_policy", side_effect=RuntimeError("boom")):
            items = dashboard.build_category_items(None, [], [], [brief])
        self.assertIsNone(items["workqueue"][0]["planned-agent"])

    def test_render_collapses_large_backlog(self):
        repo_rows = [{
            "repo": "r", "path": "/r", "has_specs": True, "total": 5, "active": 0,
            "active_ids": [], "active_specs": [],
            "backlog": 44, "backlog_ids": [f"{i:03d}-x" for i in range(44)],
            "worktrees": [],
        }]
        out = dashboard.render_dashboard(repo_rows, None, [], [])
        self.assertIn("Unspec'd backlog (44)", out)
        self.assertIn("… +42", out)  # count + first two, rest collapsed

    def test_render_shows_worktree_and_queue_sections(self):
        out = dashboard.render_dashboard(
            None, [], inflight=[],
            queue_briefs=[{"filename": "b.md", "focus": "fix the thing"}],
            worktrees=["feature-a", "feature-b"])
        self.assertIn("📥 Queued handoffs (1)", out)
        self.assertIn("fix the thing", out)
        self.assertIn("⚠️  Worktrees (2)", out)

    def test_render_shows_stuck_run_header(self):
        out = dashboard.render_dashboard(
            None,
            [self._spec_row(
                "001",
                "orchestrator-stuck",
                "manual recovery — prior orchestrator run is stuck (fanout_failed)",
            )],
            inflight=[],
            queue_briefs=[],
        )
        # The action string is hoisted into the category header (once per
        # group), so rows carry only the spec id.
        self.assertIn("Needs stuck-run recovery (1) → manual recovery", out)
        self.assertIn("    001", out)

    def test_render_action_hoisted_to_header_once(self):
        out = dashboard.render_dashboard(
            None,
            [self._spec_row("001", "ready-to-implement", "orchestrator"),
             self._spec_row("002", "ready-to-implement", "orchestrator")],
            inflight=[],
            queue_briefs=[],
        )
        self.assertIn("Ready to implement (2) → orchestrator", out)
        self.assertEqual(out.count("orchestrator"), 1)

    def test_render_marks_blocked_queue_briefs(self):
        out = dashboard.render_dashboard(
            None, [], inflight=[],
            queue_briefs=[
                {"filename": "a.md", "focus": "blocked one", "blocked": True},
                {"filename": "b.md", "focus": "ready one"},
            ])
        self.assertIn("📥 Queued handoffs (2)", out)
        # Claimable briefs list first; blocked ones are flagged.
        self.assertLess(out.index("ready one"), out.index("blocked one"))
        self.assertIn("blocked one [blocked]", out)

    def test_blocked_only_queue_has_no_workqueue_category(self):
        """A blocked-only queue must not produce a Level-1 category whose
        Level-2 option list is empty (AskUserQuestion rejects <2 options)."""
        briefs = [{"filename": "a.md", "focus": "x", "blocked": True}]
        cats = dashboard.build_category_actions(None, [], [], briefs)
        self.assertNotIn("workqueue", [c["category"] for c in cats])
        items = dashboard.build_category_items(None, [], [], briefs)
        self.assertEqual(items["workqueue"], [])

    def test_render_marks_not_yet_due_queue_briefs(self):
        """A not-yet-due brief is listed (still visible) but tagged distinctly
        from [blocked], and sorts after claimable briefs (AC-004, REQ-CHG-005)."""
        out = dashboard.render_dashboard(
            None, [], inflight=[],
            queue_briefs=[
                {"filename": "a.md", "focus": "watching one", "not_yet_due": True},
                {"filename": "b.md", "focus": "ready one"},
            ])
        self.assertIn("📥 Queued handoffs (2)", out)
        self.assertLess(out.index("ready one"), out.index("watching one"))
        self.assertIn("watching one [watching]", out)

    def test_not_yet_due_only_queue_has_no_workqueue_category(self):
        """A not-yet-due-only queue must not produce a Level-1 category whose
        Level-2 option list is empty, same guard as the blocked-only case."""
        briefs = [{"filename": "a.md", "focus": "x", "not_yet_due": True}]
        cats = dashboard.build_category_actions(None, [], [], briefs)
        self.assertNotIn("workqueue", [c["category"] for c in cats])
        items = dashboard.build_category_items(None, [], [], briefs)
        self.assertEqual(items["workqueue"], [])

    def test_not_yet_due_excluded_from_workqueue_items_alongside_claimable(self):
        """A not-yet-due brief is excluded from claimable Level-2 options even
        when other claimable briefs exist (AC-003)."""
        briefs = [
            {"filename": "a.md", "focus": "watching", "not_yet_due": True},
            {"filename": "b.md", "focus": "claimable"},
        ]
        items = dashboard.build_category_items(None, [], [], briefs)
        labels = [i.get("label") for i in items["workqueue"]]
        self.assertNotIn("watching", labels)
        self.assertIn("claimable", labels)

    def test_picker_orders_by_stage_priority_before_id(self):
        """A stuck run must survive the ≤4 cap even with lexically-earlier ids."""
        rows = [self._spec_row(f"{i:03d}-low", "sync-pending", "sync") for i in range(1, 5)]
        rows.append(self._spec_row(
            "050-stuck", "orchestrator-stuck",
            "manual recovery — prior orchestrator run is stuck (fanout_failed)"))
        items = dashboard.build_category_items(None, rows, [], [])
        ready_ids = [i["spec_id"] for i in items["ready"]]
        self.assertIn("050-stuck", ready_ids)
        self.assertEqual(ready_ids[0], "050-stuck")

    # --- build_category_items cluster tests (TASK-CHG-001) ---

    @staticmethod
    def _queue_brief(n):
        return {"filename": f"b{n}.md", "focus": f"brief {n}"}

    def test_category_items_cluster_surfaced_in_workqueue(self):
        """AC-001: a cluster whose members overlap queue_briefs produces a
        type: cluster item distinguishable from type: queue items."""
        briefs = [self._queue_brief(i) for i in range(3)]
        clusters = [{"members": ["b0", "b1"], "signals": ["related-link"], "size": 2}]
        items = dashboard.build_category_items(None, [], [], briefs, clusters=clusters)
        cluster_items = [i for i in items["workqueue"] if i["type"] == "cluster"]
        self.assertEqual(len(cluster_items), 1)
        item = cluster_items[0]
        self.assertEqual(item["action"], "consolidate-cluster")
        self.assertEqual(item["members"], ["b0", "b1"])
        queue_items = [i for i in items["workqueue"] if i["type"] == "queue"]
        self.assertTrue(all(i["type"] != "cluster" for i in queue_items))

    def test_category_items_cluster_ranked_before_queue_and_excludes_members(self):
        """AC-006: 1 cluster (3 members) + 5 unclustered briefs (>4 total)
        produces a cluster item first, then ≤3 unclustered queue items, none
        of which are cluster members."""
        cluster_members = ["b0", "b1", "b2"]
        briefs = [{"filename": f"{m}.md", "focus": m} for m in cluster_members]
        briefs += [self._queue_brief(i) for i in range(3, 8)]  # 5 unclustered
        clusters = [{"members": cluster_members, "signals": [], "size": 3}]
        items = dashboard.build_category_items(None, [], [], briefs, clusters=clusters)
        wq = items["workqueue"]
        self.assertLessEqual(len(wq), 4)
        self.assertEqual(wq[0]["type"], "cluster")
        rest = wq[1:]
        self.assertLessEqual(len(rest), 3)
        # 5 unclustered briefs don't fit in the 3 remaining slots, so the
        # pre-existing overflow ("see-more") logic still applies on top of
        # the cluster item — only the queue-typed entries must be unclustered.
        queue_rest = [i for i in rest if i["type"] == "queue"]
        rest_ids = {i["id"] for i in queue_rest}
        self.assertFalse(rest_ids & set(cluster_members))
        overflow = [i for i in rest if i["type"] == "see-more"]
        if overflow:
            self.assertFalse(set(overflow[0]["overflow_ids"]) & set(cluster_members))

    def test_category_items_cluster_below_two_members_dropped(self):
        """A cluster whose members, filtered against queue_briefs, drop below
        2 produces no cluster item."""
        briefs = [self._queue_brief(0)]  # only b0 present
        clusters = [{"members": ["b0", "b1", "b2"], "signals": [], "size": 3}]
        items = dashboard.build_category_items(None, [], [], briefs, clusters=clusters)
        self.assertFalse(any(i["type"] == "cluster" for i in items["workqueue"]))

    def test_category_items_no_clusters_unchanged(self):
        """Regression: empty/None clusters leaves workqueue_items unchanged."""
        briefs = [self._queue_brief(i) for i in range(2)]
        items_none = dashboard.build_category_items(None, [], [], briefs, clusters=None)
        items_empty = dashboard.build_category_items(None, [], [], briefs, clusters=[])
        items_omitted = dashboard.build_category_items(None, [], [], briefs)
        self.assertEqual(items_none["workqueue"], items_empty["workqueue"])
        self.assertEqual(items_none["workqueue"], items_omitted["workqueue"])
        self.assertTrue(all(i["type"] == "queue" for i in items_none["workqueue"]))

    def test_category_items_inflight_before_cluster(self):
        """Inflight (resume) items keep priority over cluster items."""
        inflight = [{"filename": "resume-me.md", "claimed_at": "2026-06-01T00:00:00"}]
        briefs = [{"filename": "b0.md", "focus": "x"}, {"filename": "b1.md", "focus": "y"}]
        clusters = [{"members": ["b0", "b1"], "signals": [], "size": 2}]
        items = dashboard.build_category_items(None, [], inflight, briefs, clusters=clusters)
        wq = items["workqueue"]
        self.assertEqual(wq[0]["type"], "inflight")
        self.assertEqual(wq[1]["type"], "cluster")

    def test_category_actions_unchanged_by_clusters(self):
        """AC-007: category_actions is unaffected by whether clusters are
        supplied to build_category_items — it takes no clusters argument."""
        rows = [self._spec_row("001", "ready-to-implement", "orchestrator")]
        briefs = [{"filename": f"b{i}.md", "focus": f"x{i}"} for i in range(5)]
        clusters = [{"members": ["b0", "b1"], "signals": [], "size": 2}]
        cats_without = dashboard.build_category_actions(None, rows, [], briefs)
        dashboard.build_category_items(None, rows, [], briefs, clusters=clusters)
        cats_with = dashboard.build_category_actions(None, rows, [], briefs)
        self.assertEqual(cats_without, cats_with)
        self.assertLessEqual(len(cats_with), 4)
        self.assertEqual(
            [c["category"] for c in cats_with], ["ready", "workqueue", "new-work"]
        )

    def test_category_items_multiple_clusters_respect_cap(self):
        """Multiple clusters where only some fit within remaining ≤4 slots
        after inflight items are placed — the cap truncates the excess
        cluster(s), not just trivially fits them all."""
        inflight = [{"filename": "r0.md", "claimed_at": "2026-06-01T00:00:00"}]
        briefs = [{"filename": f"b{i}.md", "focus": f"x{i}"} for i in range(8)]
        clusters = [
            {"members": ["b0", "b1"], "signals": [], "size": 2},
            {"members": ["b2", "b3"], "signals": [], "size": 2},
            {"members": ["b4", "b5"], "signals": [], "size": 2},
            {"members": ["b6", "b7"], "signals": [], "size": 2},
        ]
        items = dashboard.build_category_items(None, [], inflight, briefs, clusters=clusters)
        wq = items["workqueue"]
        self.assertLessEqual(len(wq), 4)
        cluster_items = [i for i in wq if i["type"] == "cluster"]
        # 1 inflight item leaves 3 slots — only 3 of the 4 clusters can fit.
        self.assertEqual(len(cluster_items), 3)
        shown_members = {m for i in cluster_items for m in i["members"]}
        self.assertNotIn("b6", shown_members)
        self.assertNotIn("b7", shown_members)

    def test_category_items_cluster_only_queue_produces_no_queue_items(self):
        """A cluster whose members fully overlap an otherwise-empty
        queue_briefs list produces no individual type: queue item."""
        briefs = [{"filename": "b0.md", "focus": "x"}, {"filename": "b1.md", "focus": "y"}]
        clusters = [{"members": ["b0", "b1"], "signals": [], "size": 2}]
        items = dashboard.build_category_items(None, [], [], briefs, clusters=clusters)
        wq = items["workqueue"]
        self.assertEqual(len(wq), 1)
        self.assertEqual(wq[0]["type"], "cluster")
        self.assertFalse(any(i["type"] == "queue" for i in wq))


class ErrorIsolation(unittest.TestCase):
    """One unreadable spec folder degrades to an `error` row, never a crash."""

    def test_scan_survives_non_utf8_spec_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "docs" / "specs"
            bad = root / "001-bad"
            bad.mkdir(parents=True)
            (bad / "2026-01-01--bad.md").write_bytes(b"\xff\xfe broken \xff")
            good = root / "002-good"
            good.mkdir()
            (good / "2026-01-01--good.md").write_text("# ok\n[NEEDS CLARIFICATION: x]")
            rows = dashboard.scan(root)
            stages = {r["id"]: r["stage"] for r in rows}
            # errors="ignore" lets the bad file parse as text; whatever the
            # stage, the scan returns a row per folder without raising.
            self.assertEqual(len(rows), 2)
            self.assertEqual(stages["002-good"], "needs-clarification")

    def test_safe_detect_stage_degrades_to_error_row(self):
        with mock.patch.object(dashboard, "detect_stage", side_effect=OSError("boom")):
            row = dashboard._safe_detect_stage(Path("/nonexistent/001-x"))
        self.assertEqual(row["stage"], "error")
        self.assertEqual(row["id"], "001-x")
        self.assertIn("OSError", row["next_action"])
        # error rows are surfaced as active work, not silently dropped
        self.assertIn("error", dashboard._ACTIVE)


class RealFixture(unittest.TestCase):
    def test_url_shortener_fixture(self):
        assert dashboard.__file__ is not None
        # the sample-spec fixture ships inside the worktrail package itself
        # (src/worktrail/.fixtures/), reused across orchestrator and router tests.
        fx = (
            Path(dashboard.__file__).resolve().parents[1]
            / ".fixtures" / "sample-spec" / "docs" / "specs" / "001-url-shortener"
        )
        if not fx.is_dir():
            self.skipTest("fixture not present")
        r = dashboard.detect_stage(fx)
        self.assertEqual(r["next_action"], "orchestrator")
        self.assertEqual(r["tasks"]["total"], 7)


class InflightBriefs(unittest.TestCase):
    """Tests for the in-flight briefs (picked, not done) scanning."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _brief(self, filename: str, status: str, claimed_at: str | None = None,
               repo: str | None = None, focus_body: str | None = None,
               batch_primary: str | None = None) -> None:
        """Materialize a brief file with the given frontmatter and body."""
        fm_lines = [
            "---",
            f"id: {filename.replace('.md', '')}",
            f"status: {status}",
        ]
        if claimed_at:
            fm_lines.append(f"claimed-at: {claimed_at}")
        if repo:
            fm_lines.append(f"repo: {repo}")
        if batch_primary:
            fm_lines.append(f"batch-primary: {batch_primary}")
        fm_lines.append("---")

        body = focus_body or ""
        content = "\n".join(fm_lines) + "\n\n" + body
        (self.root / filename).write_text(content)

    def test_empty_picked_dir(self):
        # No files in picked dir -> empty list
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(result, [])

    def test_all_done_briefs_excluded(self):
        # Only done briefs -> empty list (all excluded)
        self._brief("brief-1.md", status="done", claimed_at="2026-06-13T10:00:00Z")
        self._brief("brief-2.md", status="done", claimed_at="2026-06-13T11:00:00Z")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(result, [])

    def test_mixed_picked_and_done(self):
        # Mixed -> only picked returned
        self._brief("brief-done.md", status="done", claimed_at="2026-06-13T10:00:00Z",
                    focus_body="This is done")
        self._brief("brief-picked.md", status="picked", claimed_at="2026-06-13T11:00:00Z",
                    repo="/home/test/repo", focus_body="This is in progress")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "brief-picked.md")
        self.assertEqual(result[0]["status"], "picked")
        self.assertEqual(result[0]["focus"], "This is in progress")
        self.assertEqual(result[0]["claimed_at"], "2026-06-13T11:00:00Z")
        self.assertEqual(result[0]["repo"], "/home/test/repo")

    def test_picked_brief_with_missing_claimed_at(self):
        # Picked brief without claimed-at field -> claimed_at: null
        self._brief("brief-no-claimed.md", status="picked",
                    focus_body="In progress but not claimed-at")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["claimed_at"])

    def test_focus_extracted_from_first_non_blank_line(self):
        # focus is the first non-blank line after --- delimiter
        body = "\n\n\nFirst non-blank line\n\nSecond line"
        self._brief("brief-focus.md", status="picked", claimed_at="2026-06-13T12:00:00Z",
                    focus_body=body)
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["focus"], "First non-blank line")

    def test_focus_skips_markdown_headers(self):
        # focus skips header lines starting with #
        body = "# Header line\n\nActual content line"
        self._brief("brief-header-skip.md", status="picked",
                    focus_body=body)
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["focus"], "Actual content line")

    def test_empty_body_no_focus(self):
        # Brief with no body after frontmatter -> focus is empty string
        self._brief("brief-empty-body.md", status="picked")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["focus"], "")

    def test_nonexistent_picked_dir(self):
        # Passed a path that doesn't exist -> empty list, no error
        result = dashboard.inflight_briefs(Path("/nonexistent/path"))
        self.assertEqual(result, [])

    def test_none_picked_dir(self):
        # Passed None -> empty list
        result = dashboard.inflight_briefs(None)
        self.assertEqual(result, [])

    def test_multiple_picked_sorted_by_filename(self):
        # Multiple picked briefs -> sorted by filename
        self._brief("z-brief.md", status="picked", focus_body="Z")
        self._brief("a-brief.md", status="picked", focus_body="A")
        self._brief("m-brief.md", status="picked", focus_body="M")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 3)
        self.assertEqual([r["filename"] for r in result],
                         ["a-brief.md", "m-brief.md", "z-brief.md"])

    def test_repo_field_optional(self):
        # Picked brief without repo field -> repo: null
        self._brief("brief-no-repo.md", status="picked", claimed_at="2026-06-13T10:00:00Z",
                    focus_body="No repo field")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["repo"])

    def test_batch_companions_folded_into_primary(self):
        # Companions of a still-picked primary disappear as rows; the primary
        # carries batched: N instead.
        self._brief("primary.md", status="picked", focus_body="Primary work")
        self._brief("comp-a.md", status="picked", batch_primary="primary",
                    focus_body="Companion A")
        self._brief("comp-b.md", status="picked", batch_primary="primary",
                    focus_body="Companion B")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "primary.md")
        self.assertEqual(result[0]["batched"], 2)

    def test_companion_of_done_primary_listed_standalone(self):
        # Primary already done -> its companion is real standalone in-flight work.
        self._brief("primary.md", status="done", focus_body="Primary work")
        self._brief("comp-a.md", status="picked", batch_primary="primary",
                    focus_body="Companion A")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "comp-a.md")
        self.assertEqual(result[0]["batched"], 0)

    def test_stale_batch_primary_listed_standalone(self):
        # batch-primary pointing at a brief that isn't in picked/ at all is
        # ignored -- the companion is listed normally.
        self._brief("comp-a.md", status="picked", batch_primary="gone-primary",
                    focus_body="Companion A")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "comp-a.md")
        self.assertEqual(result[0]["batched"], 0)

    def test_unbatched_brief_has_batched_zero(self):
        self._brief("solo.md", status="picked", focus_body="Solo work")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(result[0]["batched"], 0)

    # --- freshness filter: fresh claims are actively owned, only stalled show ---

    @staticmethod
    def _iso_hours_ago(hours: float) -> str:
        dt = datetime.datetime.now().astimezone() - datetime.timedelta(hours=hours)
        return dt.isoformat(timespec="seconds")

    def test_fresh_claim_hidden(self):
        # Claimed 1h ago -> presumed actively owned by another session -> hidden.
        self._brief("fresh.md", status="picked",
                    claimed_at=self._iso_hours_ago(1), focus_body="Fresh work")
        self.assertEqual(dashboard.inflight_briefs(self.root), [])

    def test_stale_claim_shown_with_hours(self):
        # Claimed 72h ago -> past the 48h default -> shown, hours surfaced.
        self._brief("stale.md", status="picked",
                    claimed_at=self._iso_hours_ago(72), focus_body="Stalled work")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "stale.md")
        self.assertGreaterEqual(result[0]["hours_since_claim"], 71.9)

    def test_missing_claimed_at_shown(self):
        # No claimed-at -> ownership can't be verified -> shown (suspect).
        self._brief("no-stamp.md", status="picked", focus_body="Unstamped")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertIsNone(result[0]["hours_since_claim"])

    def test_unparseable_claimed_at_shown(self):
        self._brief("bad-stamp.md", status="picked",
                    claimed_at="not-a-timestamp", focus_body="Bad stamp")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)

    def test_stale_hours_zero_disables_filter(self):
        self._brief("fresh.md", status="picked",
                    claimed_at=self._iso_hours_ago(1), focus_body="Fresh work")
        result = dashboard.inflight_briefs(self.root, stale_hours=0)
        self.assertEqual(len(result), 1)

    def test_fresh_batch_hidden_entirely(self):
        # A freshly-claimed batch (primary + companion) produces no rows at all.
        self._brief("primary.md", status="picked",
                    claimed_at=self._iso_hours_ago(2), focus_body="Primary")
        self._brief("comp-a.md", status="picked", batch_primary="primary",
                    claimed_at=self._iso_hours_ago(2), focus_body="Companion")
        self.assertEqual(dashboard.inflight_briefs(self.root), [])

    def test_stale_batch_shown_as_one_row(self):
        self._brief("primary.md", status="picked",
                    claimed_at=self._iso_hours_ago(96), focus_body="Primary")
        self._brief("comp-a.md", status="picked", batch_primary="primary",
                    claimed_at=self._iso_hours_ago(96), focus_body="Companion")
        result = dashboard.inflight_briefs(self.root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["filename"], "primary.md")
        self.assertEqual(result[0]["batched"], 1)


class JSONOutput(unittest.TestCase):
    """Tests for the integration of inflight briefs into JSON output."""

    def test_json_output_includes_inflight_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs_dir = root / "specs"
            specs_dir.mkdir()
            picked_dir = root / "picked"
            picked_dir.mkdir()

            # Create a spec
            spec_dir = specs_dir / "001-test"
            spec_dir.mkdir()
            (spec_dir / "2026-06-13--test.md").write_text(
                "# Test\n\n## Clarifications\n- Test"
            )

            # Create a picked brief
            (picked_dir / "brief.md").write_text(
                "---\nid: brief\nstatus: picked\nclaimed-at: 2026-06-13T10:00:00Z\n---\n\n"
                "Focus line"
            )

            # Run main with --json
            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                dashboard.main([
                    "--root", str(specs_dir),
                    "--picked-dir", str(picked_dir),
                    "--json"
                ])
            output = json.loads(f.getvalue())

            # Verify structure
            self.assertIn("constitution", output)
            self.assertIn("specs", output)
            self.assertIn("inflight", output)
            self.assertIsInstance(output["inflight"], list)
            self.assertEqual(len(output["inflight"]), 1)
            self.assertEqual(output["inflight"][0]["filename"], "brief.md")

    def test_json_output_no_inflight_when_no_picked_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs_dir = root / "specs"
            specs_dir.mkdir()
            # Use a picked-dir that doesn't exist
            picked_dir = root / "nonexistent"

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                dashboard.main([
                    "--root", str(specs_dir),
                    "--picked-dir", str(picked_dir),
                    "--json"
                ])
            output = json.loads(f.getvalue())

            # Verify inflight is present but empty
            self.assertIn("inflight", output)
            self.assertEqual(output["inflight"], [])


class StaleBookkeeping(unittest.TestCase):
    """A pending `impl` task whose files are ALL already merged on the base branch
    is stale status bookkeeping, not real work. It must surface as
    `stale-bookkeeping → confirm & close`, NOT `ready-to-implement → orchestrator`
    (which would re-implement shipped code). Regression for the false "ready" signal
    seen on datalena spec 068 (TASK-068-19 shipped in PR #1376, status left pending)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        for cmd in (
            ["init", "-q"],
            ["config", "user.email", "t@t"],
            ["config", "user.name", "t"],
        ):
            subprocess.run(["git", "-C", str(self.repo), *cmd], check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _spec_dir(self) -> Path:
        d = self.repo / "docs" / "specs" / "068-x"
        d.mkdir(parents=True)
        (d / "2026-05-29--feature.md").write_text(SPEC_CLARIFIED)
        return d

    def _task(self, spec_dir: Path, tid: str, status: str, files, kind: str = "impl") -> None:
        td = spec_dir / "tasks"
        td.mkdir(exist_ok=True)
        files_str = "[" + ", ".join(files) + "]"
        (td / f"{tid}.md").write_text(
            f"---\nid: {tid}\nstatus: {status}\nkind: {kind}\n"
            f"files: {files_str}\ndependencies: []\n---\n# {tid}\n"
        )

    def _commit(self, files) -> None:
        for f in files:
            p = self.repo / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("shipped\n")
            subprocess.run(["git", "-C", str(self.repo), "add", f], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "ship"], check=True)

    def test_merged_files_pending_status_is_stale_bookkeeping(self):
        spec = self._spec_dir()
        files = ["app/src/page.tsx", "app/src/Comp.tsx"]
        self._task(spec, "TASK-068-19", "pending", files)
        self._commit(files)
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "stale-bookkeeping")
        self.assertNotEqual(r["next_action"], "orchestrator")
        self.assertIn("TASK-068-19", r["next_action"])
        self.assertIn("confirm & close", r["next_action"])

    def test_missing_file_still_routes_to_orchestrator(self):
        # One file shipped, one never created -> genuinely unimplemented -> orchestrator.
        spec = self._spec_dir()
        self._task(spec, "TASK-068-19", "pending", ["app/src/page.tsx", "app/src/missing.tsx"])
        self._commit(["app/src/page.tsx"])
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "ready-to-implement")
        self.assertEqual(r["next_action"], "orchestrator")

    def test_untracked_file_still_routes_to_orchestrator(self):
        # File exists on disk but is NOT git-tracked (uncommitted) -> not merged.
        spec = self._spec_dir()
        self._task(spec, "TASK-068-19", "pending", ["app/src/page.tsx"])
        p = self.repo / "app/src/page.tsx"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("uncommitted\n")
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "ready-to-implement")
        self.assertEqual(r["next_action"], "orchestrator")

    def test_git_moved_file_is_stale_bookkeeping(self):
        spec = self._spec_dir()
        old_path = "ci/scripts/check.sh"
        new_path = "scripts/ci/check.sh"
        self._task(spec, "TASK-068-19", "pending", [old_path])
        self._commit([old_path])
        (self.repo / new_path).parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "mv", old_path, new_path], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "move check"], check=True
        )
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "stale-bookkeeping")

    def test_empty_cleanup_with_completed_dependencies_is_stale_tail(self):
        spec = self._spec_dir()
        td = spec / "tasks"
        td.mkdir(exist_ok=True)
        (td / "TASK-001.md").write_text(
            "---\nid: TASK-001\nstatus: completed\nkind: impl\n"
            "files: [src/feature.py]\ndependencies: []\n---\n"
        )
        (td / "TASK-002.md").write_text(
            "---\nid: TASK-002\nstatus: pending\nkind: cleanup\n"
            "files: []\ndependencies: [TASK-001]\n---\n"
        )
        self._commit(["src/feature.py"])
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "stale-bookkeeping")
        self.assertIn("TASK-002", r["next_action"])

    def test_mixed_real_and_stale_keeps_orchestrator(self):
        # One stale (merged) + one real pending (no files to verify) -> real work remains.
        spec = self._spec_dir()
        self._task(spec, "TASK-068-01", "pending", ["app/src/shipped.tsx"])
        self._task(spec, "TASK-068-02", "pending", [])
        self._commit(["app/src/shipped.tsx"])
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "ready-to-implement")

    def test_completed_plus_stale_pending_is_stale_bookkeeping(self):
        # The completed task + the stale pending one are both merged -> closeout only.
        spec = self._spec_dir()
        self._task(spec, "TASK-068-01", "completed", ["app/src/a.tsx"])
        self._task(spec, "TASK-068-19", "pending", ["app/src/b.tsx"])
        self._commit(["app/src/a.tsx", "app/src/b.tsx"])
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "stale-bookkeeping")
        self.assertIn("TASK-068-19", r["next_action"])

    def test_repo_qualified_sibling_files_are_stale_bookkeeping(self):
        """Resolve task paths copied from a multi-repository change spec."""
        workspace = self.repo / "workspace"
        workspace.mkdir()
        primary = workspace / "primary-repo"
        sibling = workspace / "sibling-repo"
        for repo in (primary, sibling):
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)

        spec = primary / "docs" / "specs" / "001-cross-repo"
        spec.mkdir(parents=True)
        (spec / "2026-05-29--feature.md").write_text(SPEC_CLARIFIED)
        tasks = spec / "changes" / "2026-07-20--persistence" / "tasks"
        tasks.mkdir(parents=True)
        (tasks / "TASK-001.md").write_text(
            "---\nid: TASK-001\nstatus: pending\nkind: modification\n"
            "files:\n  - primary-repo/.github/workflows/release_notes_draft.yml\n"
            "dependencies: []\n---\n"
        )
        (tasks / "TASK-002.md").write_text(
            "---\nid: TASK-002\nstatus: pending\nkind: modification\n"
            "files:\n  - sibling-repo/.github/workflows/release_notes_draft.yml\n"
            "dependencies: []\n---\n"
        )

        for repo, relative in (
            (primary, ".github/workflows/release_notes_draft.yml"),
            (sibling, ".github/workflows/release_notes_draft.yml"),
        ):
            path = repo / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("shipped\n")
            subprocess.run(["git", "-C", str(repo), "add", relative], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "ship"], check=True)

        result = dashboard.detect_stage(spec)
        self.assertEqual(result["stage"], "stale-bookkeeping")
        self.assertIn("TASK-001", result["next_action"])
        self.assertIn("TASK-002", result["next_action"])

    def test_stale_pending_tail_triggers_closeout(self):
        # Regression for the same drift PR #245 fixed for _journal_verify_pending:
        # a merged e2e/cleanup TAIL task's files are already git-tracked on base but
        # its own `status:` frontmatter was never flipped. This must resolve to
        # stale-bookkeeping, not get stuck reporting tail-pending forever.
        spec = self._spec_dir()
        self._task(spec, "TASK-068-01", "completed", ["app/src/a.tsx"])
        self._task(spec, "TASK-068-90", "pending", ["e2e/x.spec.ts"], kind="e2e")
        self._commit(["app/src/a.tsx", "e2e/x.spec.ts"])
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "stale-bookkeeping")
        self.assertIn("TASK-068-90", r["next_action"])

    def test_mixed_real_and_stale_tail_keeps_tail_pending(self):
        # One tail task already merged (stale) + one tail task genuinely still
        # pending -> real tail work remains, so the spec stays tail-pending and
        # surfaces only the genuinely-pending id.
        spec = self._spec_dir()
        self._task(spec, "TASK-068-01", "completed", ["app/src/a.tsx"])
        self._task(spec, "TASK-068-90", "pending", ["e2e/x.spec.ts"], kind="e2e")
        self._task(spec, "TASK-068-91", "pending", ["e2e/y.spec.ts"], kind="cleanup")
        self._commit(["app/src/a.tsx", "e2e/x.spec.ts"])  # y.spec.ts left uncommitted
        r = dashboard.detect_stage(spec)
        self.assertEqual(r["stage"], "tail-pending")
        self.assertIn("TASK-068-91", r["next_action"])
        self.assertNotIn("TASK-068-90", r["next_action"])


class StaleBookkeepingPicker(unittest.TestCase):
    """The two-level picker surfaces a stale-bookkeeping spec under the 'ready'
    category but with a distinct `close-stale` action so it never dispatches the
    orchestrator (route E)."""

    def test_close_stale_action_not_implement(self):
        spec_rows = [
            {
                "id": "068-x",
                "stage": "stale-bookkeeping",
                "next_action": "confirm & close (TASK-068-19) (files already merged on base; "
                "flip task status → completed, no orchestrator)",
                "feature_summary": None,
            }
        ]
        items = dashboard.build_category_items(None, spec_rows, [], [], 0)
        ready = items["ready"]
        self.assertEqual(len(ready), 1)
        self.assertEqual(ready[0]["action"], "close-stale")
        self.assertEqual(ready[0]["stage"], "stale-bookkeeping")


class AutoPick(unittest.TestCase):
    """Spec 017: deterministic /go auto pick with collision guards."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.queue = self.root / "queue"
        self.queue.mkdir()
        # A real repo checkout with a sibling worktrees dir for lock tests.
        self.repo = self.root / "projects" / "myapp"
        self.repo.mkdir(parents=True)
        self.worktrees = self.root / "projects" / "myapp-worktrees"
        self.worktrees.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def _brief(
        self,
        filename: str,
        repo: "str | None",
        blocked: bool = False,
        not_yet_due: bool = False,
        recently_released: bool = False,
        target_spec: str | None = None,
    ) -> dict:
        """Write a queue brief file and return its work_queue-list-shaped dict."""
        repo_line = f"repo: {repo}\n" if repo else ""
        path = self.queue / filename
        target_line = f"target-spec: {target_spec}\n" if target_spec else ""
        path.write_text(
            f"---\nid: {filename.replace('.md', '')}\n{repo_line}{target_line}"
            "status: queued\n---\n\n## Focus\n\nwork\n"
        )
        return {
            "filename": filename,
            "path": str(path),
            "focus": "work",
            "blocked": blocked,
            "not_yet_due": not_yet_due,
            "recently_released": recently_released,
            "related": [],
        }

    def test_fifo_oldest_first(self):
        briefs = [
            self._brief("20260710-000000-newer.md", str(self.repo)),
            self._brief("20260701-000000-older.md", str(self.repo)),
        ]
        result = dashboard.auto_pick_brief(briefs)
        self.assertEqual(result["pick"]["id"], "20260701-000000-older")
        self.assertEqual(result["skipped"], [])

    def test_blocked_skipped_with_reason(self):
        briefs = [
            self._brief("20260701-000000-blocked.md", str(self.repo), blocked=True),
            self._brief("20260710-000000-free.md", str(self.repo)),
        ]
        result = dashboard.auto_pick_brief(briefs)
        self.assertEqual(result["pick"]["id"], "20260710-000000-free")
        self.assertEqual(
            result["skipped"], [{"id": "20260701-000000-blocked", "reason": "blocked"}]
        )

    def test_not_yet_due_skipped_with_reason(self):
        """A not-yet-due brief (next-check-after hasn't arrived) is skipped by
        auto-pick with a distinct reason, parallel to `blocked` (AC-002)."""
        briefs = [
            self._brief("20260701-000000-watching.md", str(self.repo), not_yet_due=True),
            self._brief("20260710-000000-free.md", str(self.repo)),
        ]
        result = dashboard.auto_pick_brief(briefs)
        self.assertEqual(result["pick"]["id"], "20260710-000000-free")
        self.assertEqual(
            result["skipped"], [{"id": "20260701-000000-watching", "reason": "not-yet-due"}]
        )

    def test_recently_released_skipped_with_reason(self):
        """A brief released within the guard window is skipped by auto-pick
        rather than immediately re-picked -- the claim/release race the field
        exists for (brief 20260714-120015)."""
        briefs = [
            self._brief("20260701-000000-justfreed.md", str(self.repo), recently_released=True),
            self._brief("20260710-000000-free.md", str(self.repo)),
        ]
        result = dashboard.auto_pick_brief(briefs)
        self.assertEqual(result["pick"]["id"], "20260710-000000-free")
        self.assertEqual(
            result["skipped"],
            [{"id": "20260701-000000-justfreed", "reason": "recently-released"}],
        )

    def test_no_repo_and_missing_repo_skipped(self):
        briefs = [
            self._brief("20260701-000000-norepo.md", None),
            self._brief("20260702-000000-gone.md", str(self.root / "projects" / "gone")),
            self._brief("20260710-000000-ok.md", str(self.repo)),
        ]
        result = dashboard.auto_pick_brief(briefs)
        self.assertEqual(result["pick"]["id"], "20260710-000000-ok")
        reasons = {s["id"]: s["reason"] for s in result["skipped"]}
        self.assertEqual(reasons["20260701-000000-norepo"], "no-repo")
        self.assertEqual(reasons["20260702-000000-gone"], "repo-missing")

    def test_held_runlock_skips_repo_stale_lock_does_not(self):
        import fcntl

        stale = self.worktrees / "run-old.lock"
        stale.write_text("12345\n")  # file exists, flock NOT held -> free
        briefs = [self._brief("20260701-000000-brief.md", str(self.repo))]
        result = dashboard.auto_pick_brief(briefs)
        self.assertIsNotNone(result["pick"])  # stale lock file alone never blocks

        held = self.worktrees / "run-live.lock"
        fh = open(held, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            result = dashboard.auto_pick_brief(briefs)
            self.assertIsNone(result["pick"])
            self.assertEqual(
                result["skipped"],
                [{"id": "20260701-000000-brief",
                  "reason": "orchestrator-run-active:run-live.lock"}],
            )
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def test_held_runlock_in_nested_worktree_dir_skips_repo(self):
        """A lock one level deeper (<repo>-worktrees/<spec-worktree>-worktrees/
        run-<spec>.lock -- worktree dependency stacking, live.py's
        journal_path_for derives the lock's parent from whatever path was
        passed as `repo`) must still be detected; the glob used to miss this
        (one level deep only)."""
        import fcntl

        nested = self.worktrees / "081-spec-worktrees"
        nested.mkdir()
        held = nested / "run-081-spec.lock"
        fh = open(held, "w")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            briefs = [self._brief("20260701-000000-brief.md", str(self.repo))]
            result = dashboard.auto_pick_brief(briefs)
            self.assertIsNone(result["pick"])
            self.assertEqual(
                result["skipped"],
                [{"id": "20260701-000000-brief",
                  "reason": "orchestrator-run-active:run-081-spec.lock"}],
            )
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def test_repo_filter_by_basename(self):
        other = self.root / "projects" / "otherapp"
        other.mkdir()
        briefs = [
            self._brief("20260701-000000-other.md", str(other)),
            self._brief("20260710-000000-mine.md", str(self.repo)),
        ]
        result = dashboard.auto_pick_brief(briefs, repo_filter="myapp")
        self.assertEqual(result["pick"]["id"], "20260710-000000-mine")
        self.assertEqual(result["skipped"][0]["reason"], "repo-filter")

    def test_empty_queue_returns_null_pick(self):
        result = dashboard.auto_pick_brief([])
        self.assertIsNone(result["pick"])
        self.assertEqual(result["skipped"], [])

    def test_remote_same_spec_branch_is_skipped(self):
        briefs = [
            self._brief(
                "20260701-000000-colliding.md", str(self.repo), target_spec="017-go-auto-mode"
            ),
            self._brief("20260710-000000-free.md", str(self.repo)),
        ]
        with mock.patch(
            "worktrail.router.dashboard._remote_spec_branch",
            side_effect=["refs/heads/chg/017-go-auto-mode-other", None],
        ):
            result = dashboard.auto_pick_brief(briefs)
        self.assertEqual(result["pick"]["id"], "20260710-000000-free")
        self.assertEqual(
            result["skipped"][0],
            {
                "id": "20260701-000000-colliding",
                "reason": "remote-spec-branch:refs/heads/chg/017-go-auto-mode-other",
            },
        )


class AutoPickMissLogging(unittest.TestCase):
    """log_auto_pick_miss(): why `--auto` found nothing, recorded for later.

    Regression for a live incident (2026-08-03): a nightly drain iteration
    reported `no_pick` with 62 of 63 queue briefs nominally ready, and there
    was no way to reconstruct which auto_pick_brief() skip reason actually
    applied at that moment -- only the outcome was ever logged, never why.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmp.name) / "auto-pick-misses.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def _lines(self) -> list[dict]:
        if not self.log_path.is_file():
            return []
        return [json.loads(line) for line in self.log_path.read_text().splitlines() if line.strip()]

    def test_a_real_pick_logs_nothing(self):
        dashboard.log_auto_pick_miss(
            {"pick": {"id": "x"}, "skipped": []}, total_briefs=1, path=self.log_path
        )
        self.assertFalse(self.log_path.exists())

    def test_a_miss_logs_reason_tally_and_full_skip_list(self):
        auto_pick = {
            "pick": None,
            "skipped": [
                {"id": "a", "reason": "orchestrator-run-active:run-x.lock"},
                {"id": "b", "reason": "orchestrator-run-active:run-y.lock"},
                {"id": "c", "reason": "not-yet-due"},
            ],
        }
        dashboard.log_auto_pick_miss(auto_pick, total_briefs=62, repo_filter="myapp",
                                     path=self.log_path)
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        record = lines[0]
        self.assertEqual(record["total_briefs"], 62)
        self.assertEqual(record["repo_filter"], "myapp")
        self.assertEqual(record["skipped_count"], 3)
        self.assertEqual(record["reasons"], {"orchestrator-run-active": 2, "not-yet-due": 1})
        self.assertEqual(record["skipped"], auto_pick["skipped"])
        datetime.datetime.fromisoformat(record["at"])  # parses; raises if malformed

    def test_an_empty_queue_miss_still_logs(self):
        dashboard.log_auto_pick_miss({"pick": None, "skipped": []}, total_briefs=0,
                                     path=self.log_path)
        lines = self._lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["total_briefs"], 0)
        self.assertEqual(lines[0]["skipped_count"], 0)

    def test_log_is_bounded_and_fifo(self):
        for i in range(dashboard.MAX_AUTO_PICK_MISS_ENTRIES + 5):
            dashboard.log_auto_pick_miss(
                {"pick": None, "skipped": [{"id": str(i), "reason": "not-yet-due"}]},
                total_briefs=1, path=self.log_path,
            )
        lines = self._lines()
        self.assertEqual(len(lines), dashboard.MAX_AUTO_PICK_MISS_ENTRIES)
        # Oldest entries were dropped; the newest survive.
        self.assertEqual(lines[-1]["skipped"][0]["id"], str(dashboard.MAX_AUTO_PICK_MISS_ENTRIES + 4))
        self.assertEqual(lines[0]["skipped"][0]["id"], "5")

    def test_env_var_override_resolves_path(self):
        with mock.patch.dict(os.environ, {dashboard.AUTO_PICK_MISS_LOG_ENV: str(self.log_path)}):
            self.assertEqual(dashboard.auto_pick_miss_log_path(), self.log_path)

    def test_write_failure_is_swallowed_not_raised(self):
        # A path whose parent cannot be created (a file occupying that name)
        # must degrade silently -- this is a side effect of a JSON render,
        # never the reason a dashboard call fails.
        blocker = Path(self._tmp.name) / "blocker"
        blocker.write_text("x")
        bad_path = blocker / "misses.jsonl"
        dashboard.log_auto_pick_miss({"pick": None, "skipped": []}, total_briefs=1, path=bad_path)


class ClusterRendering(unittest.TestCase):
    """render_dashboard's new `clusters` parameter (TASK-004, AC-011/AC-012)."""

    CLUSTERS = [
        {
            "members": ["brief-a", "brief-b", "brief-c"],
            "signals": ["related-link", "same-target-spec"],
            "size": 3,
        },
    ]

    @staticmethod
    def _cluster_section(out: str) -> str:
        idx = out.index("🔗 Consolidatable briefs")
        return out[idx:]

    def test_clusters_render_labeled_section_with_next_step(self):
        out = dashboard.render_dashboard(
            None, [], [], [{"filename": "b.md", "focus": "x"}], clusters=self.CLUSTERS
        )
        section = self._cluster_section(out)
        self.assertTrue(section.startswith("🔗 Consolidatable briefs (1)"))
        self.assertIn("brief-a, brief-b, brief-c", section)
        self.assertIn("related-link, same-target-spec", section)
        # Exactly one trailing suggested-next-step line for the section.
        self.assertEqual(section.count("→"), 1)

    def test_multiple_clusters_one_line_each_one_trailing_next_step(self):
        clusters = self.CLUSTERS + [
            {"members": ["brief-d", "brief-e"], "signals": ["duplicate-slug"], "size": 2},
        ]
        out = dashboard.render_dashboard(
            None, [], [], [{"filename": "b.md", "focus": "x"}], clusters=clusters
        )
        section = self._cluster_section(out)
        self.assertTrue(section.startswith("🔗 Consolidatable briefs (2)"))
        self.assertIn("brief-a, brief-b, brief-c", section)
        self.assertIn("brief-d, brief-e", section)
        self.assertEqual(section.count("→"), 1)

    def test_empty_clusters_omits_section_and_matches_pre_existing_render(self):
        baseline = dashboard.render_dashboard(None, [], [], [])
        with_empty = dashboard.render_dashboard(None, [], [], [], clusters=[])
        with_none = dashboard.render_dashboard(None, [], [], [], clusters=None)
        self.assertNotIn("🔗", baseline)
        self.assertEqual(with_empty, baseline)
        self.assertEqual(with_none, baseline)

    def test_clusters_appended_after_existing_sections(self):
        out = dashboard.render_dashboard(
            None,
            [],
            inflight=[],
            queue_briefs=[{"filename": "b.md", "focus": "fix the thing"}],
            clusters=self.CLUSTERS,
        )
        self.assertLess(out.index("📥 Queued handoffs"), out.index("🔗 Consolidatable briefs"))

    def test_precision_line_shown_at_threshold(self):
        """consolidated + declined >= CLUSTER_PRECISION_MIN_DECIDED (5):
        precision line appears, computed correctly."""
        precision = {"shown": 14, "consolidated": 3, "declined": 2}  # 5 decided
        out = dashboard.render_dashboard(
            None,
            [],
            [],
            [{"filename": "b.md", "focus": "x"}],
            clusters=self.CLUSTERS,
            cluster_precision=precision,
        )
        section = self._cluster_section(out)
        self.assertIn("Precision so far: 60% (14 shown, 3 consolidated, 2 declined)", section)

    def test_precision_line_omitted_below_threshold(self):
        """consolidated + declined < CLUSTER_PRECISION_MIN_DECIDED: no
        precision line -- not enough decided outcomes for the ratio to mean
        anything yet."""
        precision = {"shown": 3, "consolidated": 2, "declined": 1}  # 3 decided
        out = dashboard.render_dashboard(
            None,
            [],
            [],
            [{"filename": "b.md", "focus": "x"}],
            clusters=self.CLUSTERS,
            cluster_precision=precision,
        )
        self.assertNotIn("Precision so far", out)

    def test_precision_line_omitted_with_no_clusters(self):
        """No clusters shown -> no precision line, and output is unchanged
        by cluster_precision regardless of its value (rendered's
        byte-for-byte-unchanged guarantee when there's no cluster section)."""
        precision = {"shown": 20, "consolidated": 10, "declined": 5}
        baseline = dashboard.render_dashboard(None, [], [], [])
        with_precision = dashboard.render_dashboard(
            None, [], [], [], cluster_precision=precision
        )
        self.assertEqual(with_precision, baseline)
        self.assertNotIn("Precision so far", with_precision)

    def test_precision_line_omitted_when_no_data_yet(self):
        """worktrail.router.cluster_telemetry.summarize()'s 'no data yet' shape (precision:
        None, all counts 0) never renders a precision line."""
        precision = {"shown": 0, "consolidated": 0, "declined": 0, "precision": None}
        out = dashboard.render_dashboard(
            None,
            [],
            [],
            [{"filename": "b.md", "focus": "x"}],
            clusters=self.CLUSTERS,
            cluster_precision=precision,
        )
        self.assertNotIn("Precision so far", out)


class ClusterDetectionIntegration(unittest.TestCase):
    """main()'s wiring of cluster_detect.compute_clusters() into --json output
    (TASK-004, AC-011/AC-012/AC-013)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.work_queue_dir = Path(self._tmp.name) / "work-queue"
        self.queue_dir = self.work_queue_dir / "queue"
        self.queue_dir.mkdir(parents=True)
        self._env_patch = mock.patch.dict(os.environ, {"WORK_QUEUE_DIR": str(self.work_queue_dir)})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tmp.cleanup()

    @staticmethod
    def _write_brief(
        dirpath: Path, filename: str, *, repo=None, target_spec=None, related=None, focus: str = ""
    ) -> str:
        lines = ["---"]
        if repo is not None:
            lines.append(f"repo: {repo}")
        if target_spec is not None:
            lines.append(f"target-spec: {target_spec}")
        if related is not None:
            lines.append("related:")
            lines.extend(f"  - {r}" for r in related)
        lines.append(f'focus: "{focus}"')
        lines.append("---")
        lines.append("")
        lines.append("Body text.")
        (dirpath / filename).write_text("\n".join(lines), encoding="utf-8")
        return Path(filename).stem

    def _write_clustered_briefs(self) -> dict:
        """Two briefs sharing a target-spec plus a third connected via related --
        forms one 3-member surfaced cluster (always surfaced, size >= 3)."""
        repo = "/repos/myapp"
        stem_a = self._write_brief(
            self.queue_dir,
            "20260714-090000-brief-a.md",
            repo=repo,
            target_spec="020-thing",
            focus="alpha",
        )
        self._write_brief(
            self.queue_dir,
            "20260714-090100-brief-b.md",
            repo=repo,
            target_spec="020-thing",
            focus="beta",
        )
        self._write_brief(
            self.queue_dir,
            "20260714-090200-brief-c.md",
            repo=repo,
            related=[stem_a],
            focus="gamma",
        )
        return {
            "members": sorted(
                ["20260714-090000-brief-a", "20260714-090100-brief-b", "20260714-090200-brief-c"]
            ),
            "signals": ["related-link", "same-target-spec"],
            "size": 3,
        }

    def _run_json(self, extra_argv=None) -> dict:
        specs_dir = Path(self._tmp.name) / "specs"
        specs_dir.mkdir(exist_ok=True)
        import io
        from contextlib import redirect_stdout

        argv = ["--root", str(specs_dir), "--json"] + (extra_argv or [])
        f = io.StringIO()
        with redirect_stdout(f):
            dashboard.main(argv)
        return json.loads(f.getvalue())

    def test_json_includes_clusters_field_matching_compute_clusters(self):
        expected = self._write_clustered_briefs()
        output = self._run_json()
        self.assertIn("clusters", output)
        self.assertEqual(output["clusters"], [expected])

    def test_empty_queue_clusters_empty_and_existing_fields_unchanged(self):
        output = self._run_json()
        self.assertEqual(output["clusters"], [])
        for key in (
            "rendered",
            "category_actions",
            "category_items",
            "specs",
            "active_specs",
            "handoff_queue",
            "inflight",
            "worktrees",
            "auto_pick",
        ):
            self.assertIn(key, output)

    def test_repos_json_output_includes_clusters_field(self):
        self._write_clustered_briefs()
        parent = Path(self._tmp.name) / "parent"
        parent.mkdir()
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            dashboard.main(["--repos", str(parent), "--json"])
        output = json.loads(f.getvalue())
        self.assertIn("clusters", output)
        self.assertEqual(len(output["clusters"]), 1)

    def test_end_to_end_main_invocation_surfaces_expected_cluster(self):
        expected = self._write_clustered_briefs()
        output = self._run_json()
        self.assertEqual(output["clusters"], [expected])
        self.assertIn("🔗 Consolidatable briefs (1)", output["rendered"])

    def test_compute_clusters_raising_still_completes_main_with_empty_clusters(self):
        self._write_clustered_briefs()
        with mock.patch.object(
            dashboard.cluster_detect, "compute_clusters", side_effect=RuntimeError("boom")
        ):
            output = self._run_json()
        self.assertEqual(output["clusters"], [])
        self.assertIn("rendered", output)
        self.assertNotIn("🔗", output["rendered"])


class RecentRuns(unittest.TestCase):
    """route:J go-dashboard-run-record-history: surface recent /go run outcomes."""

    @staticmethod
    def _write_run(run_dir: Path, run_id: str, **fields) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        record = {
            "run_id": run_id,
            "started_at": "2026-07-01T00:00:00+0000",
            "completed_at": None,
            "selected_route": "D",
            "final_status": None,
            "pull_request": None,
        }
        record.update(fields)
        lines = []
        for key, value in record.items():
            lines.append(f"{key}: {'null' if value is None else value}")
        (run_dir / f"{run_id}.yaml").write_text("\n".join(lines) + "\n")

    def test_missing_run_dir_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            self.assertEqual(
                dashboard.load_recent_runs(repo, runs_dir=Path(tmp) / "runs"), []
            )

    def test_sorted_most_recent_first_and_limited(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            runs_dir = Path(tmp) / "runs"
            run_dir = runs_dir / "myrepo"
            self._write_run(
                run_dir, "go-20260701-000000",
                completed_at="2026-07-01T01:00:00+0000",
                final_status="completed_pr_open",
                pull_request="https://github.com/x/y/pull/1",
            )
            self._write_run(
                run_dir, "go-20260702-000000",
                started_at="2026-07-02T00:00:00+0000",
            )  # still in progress -- no completed_at
            runs = dashboard.load_recent_runs(repo, runs_dir=runs_dir)
            self.assertEqual([r["run_id"] for r in runs], [
                "go-20260702-000000", "go-20260701-000000",
            ])
            self.assertEqual(runs[1]["final_status"], "completed_pr_open")
            self.assertEqual(runs[1]["pull_request"], "https://github.com/x/y/pull/1")

    def test_limit_caps_result_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            runs_dir = Path(tmp) / "runs"
            run_dir = runs_dir / "myrepo"
            for i in range(7):
                self._write_run(
                    run_dir, f"go-2026070{i}-000000",
                    completed_at=f"2026-07-0{i}T00:00:00+0000",
                )
            runs = dashboard.load_recent_runs(repo, limit=5, runs_dir=runs_dir)
            self.assertEqual(len(runs), 5)

    def test_corrupt_record_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "myrepo"
            repo.mkdir()
            runs_dir = Path(tmp) / "runs"
            run_dir = runs_dir / "myrepo"
            run_dir.mkdir(parents=True)
            (run_dir / "broken.yaml").write_bytes(b"\xff\xfe not utf-8")
            self._write_run(run_dir, "go-20260701-000000", completed_at="2026-07-01T00:00:00+0000")
            runs = dashboard.load_recent_runs(repo, runs_dir=runs_dir)
            self.assertEqual([r["run_id"] for r in runs], ["go-20260701-000000"])

    def test_render_dashboard_recent_runs_section(self):
        runs = [
            {"run_id": "go-2", "selected_route": "D", "final_status": None,
             "pull_request": None},
            {"run_id": "go-1", "selected_route": "C",
             "final_status": "planned_ready_for_implementation",
             "pull_request": "https://github.com/x/y/pull/1"},
        ]
        out = dashboard.render_dashboard(None, [], [], [], recent_runs=runs)
        self.assertIn("🕘 Recent runs (2)", out)
        self.assertIn("go-2 | D | in progress | -", out)
        self.assertIn(
            "go-1 | C | planned_ready_for_implementation | https://github.com/x/y/pull/1",
            out,
        )

    def test_render_dashboard_no_recent_runs_section_when_empty(self):
        out = dashboard.render_dashboard(None, [], [], [])
        self.assertNotIn("Recent runs", out)

    def test_main_json_single_repo_includes_recent_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = root / "myrepo"
            specs_dir = repo / "docs" / "specs"
            specs_dir.mkdir(parents=True)
            runs_dir = root / "runs"
            self._write_run(
                runs_dir / "myrepo", "go-20260701-000000",
                completed_at="2026-07-01T00:00:00+0000",
                final_status="completed_pr_open",
            )

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                dashboard.main([
                    "--root", str(specs_dir),
                    "--run-record-dir", str(runs_dir),
                    "--json",
                ])
            output = json.loads(f.getvalue())
            self.assertEqual(len(output["recent_runs"]), 1)
            self.assertEqual(output["recent_runs"][0]["run_id"], "go-20260701-000000")
            self.assertIn("🕘 Recent runs (1)", output["rendered"])

    def test_main_json_multi_repo_tags_recent_runs_per_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            repo_a = parent / "repo-a"
            (repo_a / ".git").mkdir(parents=True)
            repo_b = parent / "repo-b"
            (repo_b / ".git").mkdir(parents=True)
            runs_dir = parent / "runs"
            self._write_run(
                runs_dir / "repo-a", "go-20260701-000000",
                completed_at="2026-07-01T00:00:00+0000",
            )

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with redirect_stdout(f):
                dashboard.main([
                    "--repos", str(parent),
                    "--run-record-dir", str(runs_dir),
                    "--json",
                ])
            output = json.loads(f.getvalue())
            ra = next(r for r in output["repos"] if r["repo"] == "repo-a")
            rb = next(r for r in output["repos"] if r["repo"] == "repo-b")
            self.assertEqual(len(ra["recent_runs"]), 1)
            self.assertEqual(rb["recent_runs"], [])
            self.assertIn("repo-a go-20260701-000000", output["rendered"])


class PostmergeAuditDashboardIntegration(unittest.TestCase):
    """spec post-merge-reconciliation-audit change 3 -- dashboard.py folds
    audit_postmerge.dashboard_snapshot() into the JSON `postmerge_check_failures`
    field and a rendered-text summary line, additively: every field that existed
    before this integration must be unchanged in shape/content whether or not any
    postmerge audit state has ever been written."""

    def setUp(self):
        # Isolate from this machine's real ~/work-queue (queue/cluster state
        # there is otherwise picked up by default and would make baseline vs.
        # with-flag renders nondeterministic across the two `main()` calls).
        self._tmp = tempfile.TemporaryDirectory()
        work_queue_dir = Path(self._tmp.name) / "work-queue"
        (work_queue_dir / "queue").mkdir(parents=True)
        self._env_patch = mock.patch.dict(os.environ, {"WORK_QUEUE_DIR": str(work_queue_dir)})
        self._env_patch.start()

    def tearDown(self):
        self._env_patch.stop()
        self._tmp.cleanup()

    @staticmethod
    def _run_json(argv):
        import io
        from contextlib import redirect_stdout

        f = io.StringIO()
        with redirect_stdout(f):
            dashboard.main(argv)
        return json.loads(f.getvalue())

    def test_single_repo_json_shape_unchanged_when_no_postmerge_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs_dir = root / "myrepo" / "docs" / "specs"
            _spec(specs_dir / "001-x", spec_body=SPEC_MIN)
            state_dir = root / "no-such-postmerge-state"  # never created

            baseline = self._run_json([
                "--root", str(specs_dir),
                "--postmerge-audit-state", str(state_dir),
                "--json",
            ])
            with_flag = self._run_json([
                "--root", str(specs_dir),
                "--postmerge-audit-state", str(state_dir),
                "--json",
            ])

            # cluster_precision reads a real shared telemetry log outside this
            # test's control (see cluster_telemetry.summarize()) -- its own key
            # presence is checked, but its value is not diffed across calls.
            for key in (
                "constitution", "specs", "active_specs", "handoff_queue",
                "inflight", "worktrees", "category_actions", "category_items",
                "auto_pick", "clusters", "capacity",
                "recent_runs", "staleness_warnings",
            ):
                self.assertEqual(baseline[key], with_flag[key], key)
            self.assertIn("cluster_precision", with_flag)
            self.assertEqual(baseline["rendered"], with_flag["rendered"])
            self.assertNotIn("Post-merge check failures", with_flag["rendered"])
            self.assertEqual(
                with_flag["postmerge_check_failures"],
                {"repos_flagged": 0, "prs_flagged": 0, "flagged": []},
            )

    def test_multi_repo_json_shape_unchanged_when_no_postmerge_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            (parent / "repo-a" / ".git").mkdir(parents=True)
            state_dir = parent / "no-such-postmerge-state"  # never created

            baseline = self._run_json([
                "--repos", str(parent),
                "--postmerge-audit-state", str(state_dir),
                "--json",
            ])
            with_flag = self._run_json([
                "--repos", str(parent),
                "--postmerge-audit-state", str(state_dir),
                "--json",
            ])

            for key in (
                "repos", "active_specs", "handoff_queue", "inflight",
                "category_actions", "category_items", "auto_pick", "clusters",
                "capacity", "staleness_warnings",
            ):
                self.assertEqual(baseline[key], with_flag[key], key)
            self.assertIn("cluster_precision", with_flag)
            self.assertEqual(baseline["rendered"], with_flag["rendered"])
            self.assertNotIn("Post-merge check failures", with_flag["rendered"])
            self.assertEqual(
                with_flag["postmerge_check_failures"],
                {"repos_flagged": 0, "prs_flagged": 0, "flagged": []},
            )

    def test_single_repo_json_postmerge_check_failures_present_when_state_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            specs_dir = root / "myrepo" / "docs" / "specs"
            _spec(specs_dir / "001-x", spec_body=SPEC_MIN)
            state_dir = root / "postmerge-state"
            state_dir.mkdir()
            (state_dir / "myrepo.json").write_text(json.dumps({
                "last_swept_at": "2026-08-01T00:00:00+00:00",
                "flagged": [
                    {
                        "repo": "myrepo",
                        "url": "https://github.com/org/myrepo/pull/7",
                        "failing_checks": ["ci/build"],
                        "merged_at": "2026-08-01T00:00:00Z",
                    },
                ],
            }))

            output = self._run_json([
                "--root", str(specs_dir),
                "--postmerge-audit-state", str(state_dir),
                "--json",
            ])

            self.assertEqual(
                output["postmerge_check_failures"],
                {
                    "repos_flagged": 1,
                    "prs_flagged": 1,
                    "flagged": [
                        {
                            "repo": "myrepo",
                            "url": "https://github.com/org/myrepo/pull/7",
                            "failing_checks": ["ci/build"],
                            "merged_at": "2026-08-01T00:00:00Z",
                        },
                    ],
                },
            )
            self.assertIn("Post-merge check failures", output["rendered"])
            self.assertIn("myrepo#7", output["rendered"])

    def test_multi_repo_json_postmerge_check_failures_present_when_state_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            (parent / "repo-a" / ".git").mkdir(parents=True)
            state_dir = parent / "postmerge-state"
            state_dir.mkdir()
            (state_dir / "repo-a.json").write_text(json.dumps({
                "last_swept_at": "2026-08-01T00:00:00+00:00",
                "flagged": [
                    {
                        "repo": "repo-a",
                        "url": "https://github.com/org/repo-a/pull/3",
                        "failing_checks": ["ci/test"],
                        "merged_at": "2026-08-01T00:00:00Z",
                    },
                ],
            }))

            output = self._run_json([
                "--repos", str(parent),
                "--postmerge-audit-state", str(state_dir),
                "--json",
            ])

            self.assertEqual(output["postmerge_check_failures"]["repos_flagged"], 1)
            self.assertEqual(output["postmerge_check_failures"]["prs_flagged"], 1)
            self.assertIn("Post-merge check failures", output["rendered"])
            self.assertIn("repo-a#3", output["rendered"])

    def test_text_mode_summary_line_only_emitted_when_state_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            import io
            from contextlib import redirect_stdout

            root = Path(tmp)
            specs_dir = root / "myrepo" / "docs" / "specs"
            _spec(specs_dir / "001-x", spec_body=SPEC_MIN)
            empty_state_dir = root / "no-such-postmerge-state"

            f = io.StringIO()
            with redirect_stdout(f):
                dashboard.main([
                    "--root", str(specs_dir),
                    "--postmerge-audit-state", str(empty_state_dir),
                ])
            self.assertNotIn("Post-merge check failures", f.getvalue())

            state_dir = root / "postmerge-state"
            state_dir.mkdir()
            (state_dir / "myrepo.json").write_text(json.dumps({
                "last_swept_at": "2026-08-01T00:00:00+00:00",
                "flagged": [
                    {
                        "repo": "myrepo",
                        "url": "https://github.com/org/myrepo/pull/9",
                        "failing_checks": ["ci/build"],
                        "merged_at": "2026-08-01T00:00:00Z",
                    },
                ],
            }))

            f2 = io.StringIO()
            with redirect_stdout(f2):
                dashboard.main([
                    "--root", str(specs_dir),
                    "--postmerge-audit-state", str(state_dir),
                ])
            self.assertIn("Post-merge check failures", f2.getvalue())
            self.assertIn("myrepo#9", f2.getvalue())


class StalenessWarnings(unittest.TestCase):
    """route:F go-dashboard-local-only-git-staleness -- opt-in freshness check."""

    def test_helper_returns_empty_when_freshness_module_unavailable(self):
        with mock.patch.object(dashboard, "_check_repo_freshness", None):
            self.assertEqual(dashboard._staleness_warnings([("repo-a", "/tmp/repo-a")]), [])

    def test_helper_reports_only_stale_repos(self):
        def fake_check(path):
            if "stale" in str(path):
                return {"stale": True, "warning": "local main is 3 commit(s) behind origin/main"}
            return {"stale": False, "warning": None}

        with mock.patch.object(dashboard, "_check_repo_freshness", fake_check):
            out = dashboard._staleness_warnings([
                ("fresh-repo", "/tmp/fresh-repo"),
                ("stale-repo", "/tmp/stale-repo"),
            ])
        self.assertEqual(out, [{"repo": "stale-repo", "warning": "local main is 3 commit(s) behind origin/main"}])

    def test_helper_degrades_silently_on_exception(self):
        def boom(path):
            raise RuntimeError("network unreachable")

        with mock.patch.object(dashboard, "_check_repo_freshness", boom):
            self.assertEqual(dashboard._staleness_warnings([("repo-a", "/tmp/repo-a")]), [])

    def test_render_dashboard_unaffected_when_no_staleness_warnings(self):
        baseline = dashboard.render_dashboard(None, [], [], [])
        self.assertEqual(baseline, dashboard.render_dashboard(None, [], [], [], staleness_warnings=[]))
        self.assertEqual(baseline, dashboard.render_dashboard(None, [], [], [], staleness_warnings=None))

    def test_render_dashboard_surfaces_staleness_warning_line(self):
        out = dashboard.render_dashboard(
            None, [], [], [],
            staleness_warnings=[{"repo": "datalena", "warning": "local dev is 3 commit(s) behind origin/dev"}],
        )
        self.assertIn("⚠️  Stale checkout — datalena: local dev is 3 commit(s) behind origin/dev", out)

    def test_main_json_single_repo_check_freshness_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "myrepo"
            specs = repo_dir / "docs" / "specs"
            specs.mkdir(parents=True)

            def fake_check(path):
                return {"stale": True, "warning": "local main is 1 commit(s) behind origin/main"}

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with mock.patch.object(dashboard, "_check_repo_freshness", fake_check):
                with redirect_stdout(f):
                    dashboard.main(["--root", str(specs), "--check-freshness", "--json"])
            output = json.loads(f.getvalue())
            self.assertEqual(len(output["staleness_warnings"]), 1)
            self.assertEqual(output["staleness_warnings"][0]["repo"], "myrepo")
            self.assertIn("⚠️  Stale checkout — myrepo:", output["rendered"])

    def test_main_json_defaults_check_freshness_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo_dir = Path(tmp) / "myrepo"
            specs = repo_dir / "docs" / "specs"
            specs.mkdir(parents=True)

            called = []

            def fake_check(path):
                called.append(path)
                return {"stale": True, "warning": "should never run"}

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with mock.patch.object(dashboard, "_check_repo_freshness", fake_check):
                with redirect_stdout(f):
                    dashboard.main(["--root", str(specs), "--json"])
            output = json.loads(f.getvalue())
            self.assertEqual(output["staleness_warnings"], [])
            self.assertEqual(called, [])

    def test_main_json_multi_repo_check_freshness_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            repo_a = parent / "repo-a"
            (repo_a / ".git").mkdir(parents=True)
            repo_b = parent / "repo-b"
            (repo_b / ".git").mkdir(parents=True)

            def fake_check(path):
                if str(path).endswith("repo-a"):
                    return {"stale": True, "warning": "behind"}
                return {"stale": False, "warning": None}

            import io
            from contextlib import redirect_stdout

            f = io.StringIO()
            with mock.patch.object(dashboard, "_check_repo_freshness", fake_check):
                with redirect_stdout(f):
                    dashboard.main(["--repos", str(parent), "--check-freshness", "--json"])
            output = json.loads(f.getvalue())
            self.assertEqual(
                output["staleness_warnings"], [{"repo": "repo-a", "warning": "behind"}]
            )


class DashboardRepoRootResolution(unittest.TestCase):
    """`_dashboard_repo_root()` must resolve the true repo checkout root
    regardless of install topology. `_HERE.parents[4]` alone is correct only
    for the marketplace topology; the plugin cache topology inserts an extra
    `<sha>` version segment
    (`.../cache/developer-kit/<plugin>/<sha>/skills/<skill>/scripts`), which
    left the old fixed-offset lands on `cache/developer-kit` -- a plain
    container directory for cached plugin snapshots, not a git checkout.
    Brief 20260723-221530-dashboard-repo-root-cache-topology."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._orig_here = dashboard._HERE
        self.addCleanup(setattr, dashboard, "_HERE", self._orig_here)

    def test_cache_topology_with_extra_sha_segment_finds_real_repo_root(self):
        """`_HERE.parents[4]` alone would land on `cache/developer-kit`
        (no `.git`); the ancestor walk must instead find the real checkout."""
        repo_root = self.root / "cache" / "developer-kit"
        scripts = (
            repo_root / "developer-kit-project-management" / "a1b2c3d4e5f6"
            / "skills" / "devkit-pm-go" / "scripts"
        )
        scripts.mkdir(parents=True)
        (repo_root / ".git").mkdir()
        dashboard._HERE = scripts

        self.assertEqual(dashboard._dashboard_repo_root(), repo_root)

    def test_marketplace_topology_still_resolves_via_git_root(self):
        """Fixed-offset fast path (no `<sha>` segment) keeps working, now via
        the same `.git`-ancestor walk rather than a hardcoded offset."""
        repo_root = self.root / "marketplaces" / "developer-kit"
        scripts = (
            repo_root / "plugins" / "developer-kit-project-management"
            / "skills" / "devkit-pm-go" / "scripts"
        )
        scripts.mkdir(parents=True)
        (repo_root / ".git").mkdir()
        dashboard._HERE = scripts

        self.assertEqual(dashboard._dashboard_repo_root(), repo_root)

    def test_no_git_ancestor_degrades_to_fixed_offset(self):
        """No `.git` anywhere above `_HERE` -> falls back to the old
        `parents[4]` offset instead of raising."""
        scripts = (
            self.root / "cache" / "developer-kit" / "developer-kit-project-management"
            / "deadbeefcafe" / "skills" / "devkit-pm-go" / "scripts"
        )
        scripts.mkdir(parents=True)
        dashboard._HERE = scripts
        # The test runner may itself place a marker at `/tmp/.git`; isolate
        # this fixture from unrelated ancestors so it exercises the stated
        # no-git condition.
        original_find_git_root = dashboard._find_git_root
        self.addCleanup(setattr, dashboard, "_find_git_root", original_find_git_root)
        dashboard._find_git_root = lambda _: None

        self.assertEqual(dashboard._dashboard_repo_root(), scripts.parents[4])


if __name__ == "__main__":
    unittest.main(verbosity=2)
