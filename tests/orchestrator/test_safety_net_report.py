"""Tests for safety_net_report.py -- cross-run aggregation of orchestrator
"took the safety net" events (dependency_file_drift, automerge_preflight_fallback).
"""

import json

from worktrail.orchestrator import safety_net_report


def _write_journal(path, data):
    path.write_text(json.dumps(data))


def test_scan_with_no_worktrees_dir_returns_empty(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()

    summary = safety_net_report.scan(repo)

    assert summary["runs_scanned"] == 0
    assert summary["by_event"] == {}


def test_scan_ignores_journals_with_no_events(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [{"task": "TASK-001", "role": "implement", "report": {}}],
        },
    )

    summary = safety_net_report.scan(repo)

    assert summary["runs_scanned"] == 1
    assert summary["runs_with_safety_net_events"] == 0
    assert summary["by_event"] == {}


def test_scan_aggregates_dependency_file_drift_across_runs(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [
                {
                    "event": "dependency_file_drift",
                    "task": "TASK-002",
                    "dep_id": "TASK-001",
                    "declared_path": "helper_v1.py",
                    "dep_head_sha": "abc123",
                    "at": 1.0,
                },
            ],
        },
    )
    _write_journal(
        wt / "run-002-feature.json",
        {
            "spec_id": "002-feature",
            "entries": [
                {
                    "event": "dependency_file_drift",
                    "task": "TASK-005",
                    "dep_id": "TASK-001",
                    "declared_path": "helper.py",
                    "dep_head_sha": "def456",
                    "at": 2.0,
                },
            ],
        },
    )

    summary = safety_net_report.scan(repo)

    assert summary["runs_scanned"] == 2
    assert summary["runs_with_safety_net_events"] == 2
    assert summary["by_event"] == {"dependency_file_drift": 2}
    assert summary["dependency_file_drift_by_dep_id"] == {"TASK-001": 2}


def test_scan_aggregates_automerge_preflight_fallback(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [],
            "safety_net_events": [
                {
                    "event": "automerge_preflight_fallback",
                    "group": "base",
                    "pr": "run/base",
                    "reason": "gh api failed",
                    "outcome": "queued",
                    "at": 1.0,
                },
                {
                    "event": "automerge_preflight_fallback",
                    "group": "feature-1",
                    "pr": "run/feature-1",
                    "reason": "gh api failed",
                    "outcome": "fallback_failed",
                    "fallback_error": "boom",
                    "at": 2.0,
                },
            ],
        },
    )

    summary = safety_net_report.scan(repo)

    assert summary["by_event"] == {"automerge_preflight_fallback": 2}
    assert summary["automerge_preflight_fallback_by_outcome"] == {
        "queued": 1,
        "fallback_failed": 1,
    }


def test_scan_combines_both_event_kinds_in_one_journal(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [
                {
                    "event": "dependency_file_drift",
                    "task": "TASK-002",
                    "dep_id": "TASK-001",
                    "declared_path": "helper_v1.py",
                    "dep_head_sha": "abc123",
                    "at": 1.0,
                },
            ],
            "safety_net_events": [
                {
                    "event": "automerge_preflight_fallback",
                    "group": "base",
                    "pr": "run/base",
                    "reason": "x",
                    "outcome": "queued",
                    "at": 2.0,
                },
            ],
        },
    )

    summary = safety_net_report.scan(repo)

    assert summary["runs_scanned"] == 1
    assert summary["runs_with_safety_net_events"] == 1
    assert summary["by_event"] == {
        "dependency_file_drift": 1,
        "automerge_preflight_fallback": 1,
    }


def test_scan_tolerates_malformed_journal(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    (wt / "run-001-feature.json").write_text("not-json")

    summary = safety_net_report.scan(repo)

    assert summary["runs_scanned"] == 0  # unreadable journal is skipped, not counted


def test_render_reports_no_events_case(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()

    out = safety_net_report.render(safety_net_report.scan(repo), repo)

    assert "no safety-net events recorded" in out


def test_render_includes_breakdowns(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [
                {
                    "event": "dependency_file_drift",
                    "task": "TASK-002",
                    "dep_id": "TASK-001",
                    "declared_path": "helper_v1.py",
                    "dep_head_sha": "abc123",
                    "at": 1.0,
                },
            ],
        },
    )

    out = safety_net_report.render(safety_net_report.scan(repo), repo)

    assert "dependency_file_drift: 1" in out
    assert "TASK-001: 1" in out


def test_scan_aggregates_worktree_drift_repaired_by_task(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [
                {"event": "worktree_drift_repaired", "task": "TASK-002", "at": 1.0},
            ],
        },
    )
    _write_journal(
        wt / "run-002-feature.json",
        {
            "spec_id": "002-feature",
            "entries": [
                {"event": "worktree_drift_repaired", "task": "TASK-002", "at": 2.0},
            ],
        },
    )

    summary = safety_net_report.scan(repo)

    assert summary["by_event"] == {"worktree_drift_repaired": 2}
    assert summary["worktree_drift_repaired_by_task"] == {"TASK-002": 2}


def test_scan_aggregates_checklist_conflict_resolved_by_task(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [
                {"event": "checklist_conflict_resolved", "task": "TASK-005", "at": 1.0},
            ],
        },
    )

    summary = safety_net_report.scan(repo)

    assert summary["by_event"] == {"checklist_conflict_resolved": 1}
    assert summary["checklist_conflict_resolved_by_task"] == {"TASK-005": 1}


def test_scan_combines_repair_events_with_dependency_file_drift_in_one_journal(
    tmp_path,
):
    """`_require_dependency_files_with_repair` can return both
    `worktree_drift_repaired` and a `dependency_file_drift` in the same
    returned list (the retry succeeded but the re-check still detected
    filename drift on a different file) -- both must be counted."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [
                {"event": "worktree_drift_repaired", "task": "TASK-002", "at": 1.0},
                {
                    "event": "dependency_file_drift",
                    "task": "TASK-002",
                    "dep_id": "TASK-001",
                    "declared_path": "helper.py",
                    "dep_head_sha": "abc123",
                    "at": 1.0,
                },
            ],
        },
    )

    summary = safety_net_report.scan(repo)

    assert summary["by_event"] == {
        "worktree_drift_repaired": 1,
        "dependency_file_drift": 1,
    }
    assert summary["worktree_drift_repaired_by_task"] == {"TASK-002": 1}
    assert summary["dependency_file_drift_by_dep_id"] == {"TASK-001": 1}


def test_render_includes_repair_event_breakdowns(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    _write_journal(
        wt / "run-001-feature.json",
        {
            "spec_id": "001-feature",
            "entries": [
                {"event": "worktree_drift_repaired", "task": "TASK-002", "at": 1.0},
                {"event": "checklist_conflict_resolved", "task": "TASK-005", "at": 2.0},
            ],
        },
    )

    out = safety_net_report.render(safety_net_report.scan(repo), repo)

    assert "worktree_drift_repaired: 1" in out
    assert "checklist_conflict_resolved: 1" in out
    assert "worktree_drift_repaired by task:" in out
    assert "  TASK-002: 1" in out
    assert "checklist_conflict_resolved by task:" in out
    assert "  TASK-005: 1" in out
