#!/usr/bin/env python3
"""Tests for journal_selfcheck.py — stranded-run invariant detection
(brief 20260808-210929) — and run_record.py's hand-edit corruption guard.

The two incident classes these lock in were each caught only by an operator
noticing manually: integrate_complete with an undispatched tail (PR #235/#238),
and a state file rewritten by a generic YAML writer (2026-08-08 run record).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktrail.router import journal_selfcheck, run_record


def _repo_with_journal(tmp_path: Path, name: str, journal: "dict | str") -> Path:
    repo = tmp_path / "projects" / "myapp"
    repo.mkdir(parents=True, exist_ok=True)
    wt = tmp_path / "projects" / "myapp-worktrees"
    wt.mkdir(exist_ok=True)
    payload = journal if isinstance(journal, str) else json.dumps(journal)
    (wt / f"run-{name}.json").write_text(payload)
    return repo


class TestStrandedTail:
    def test_integrate_complete_with_pending_tail_is_flagged(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {"integrate_complete": True, "pending_tail_tasks": ["3.1", "3.2"], "groups": {}},
        )
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert len(findings) == 1
        assert findings[0]["kind"] == "stranded-tail"
        assert findings[0]["spec_id"] == "008-x"
        assert "3.1" in findings[0]["detail"]

    def test_no_pending_tail_is_clean(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path, "008-x", {"integrate_complete": True, "pending_tail_tasks": [], "groups": {}}
        )
        assert journal_selfcheck.check_repo(repo)["findings"] == []

    def test_integrate_incomplete_is_clean(self, tmp_path):
        # Mid-run state: tail outstanding but integrate not complete — normal.
        repo = _repo_with_journal(
            tmp_path, "008-x", {"integrate_complete": False, "pending_tail_tasks": ["3.1"]}
        )
        assert journal_selfcheck.check_repo(repo)["findings"] == []

    def test_live_runlock_suppresses_the_finding(self, tmp_path):
        import fcntl

        repo = _repo_with_journal(
            tmp_path, "008-x", {"integrate_complete": True, "pending_tail_tasks": ["3.1"]}
        )
        lock = repo.parent / "myapp-worktrees" / "run-008-x.lock"
        fh = open(lock, "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert journal_selfcheck.check_repo(repo)["findings"] == []
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def test_no_worktrees_dir_is_clean(self, tmp_path):
        repo = tmp_path / "projects" / "bare"
        repo.mkdir(parents=True)
        assert journal_selfcheck.check_repo(repo)["findings"] == []


class TestMalformedJournal:
    def test_unparseable_journal_is_flagged(self, tmp_path):
        repo = _repo_with_journal(tmp_path, "008-x", "integrate_complete: true\ngroups: {}\n")
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert len(findings) == 1
        assert findings[0]["kind"] == "malformed-journal"

    def test_non_object_root_is_flagged(self, tmp_path):
        repo = _repo_with_journal(tmp_path, "008-x", json.dumps(["not", "an", "object"]))
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert findings[0]["kind"] == "malformed-journal"
        assert "list" in findings[0]["detail"]


class TestRunRecordHandEditGuard:
    """run_record._load must fail LOUD (with a recovery hint) on a record
    rewritten outside its own renderer, instead of parsing garbage keys and
    compounding the corruption on the next save."""

    def test_generic_yaml_rewrite_is_rejected(self, tmp_path):
        import yaml

        rec = tmp_path / "go-x.yaml"
        # The exact incident shape: a nested list a generic YAML writer emits.
        rec.write_text(
            yaml.safe_dump(
                {
                    "run_id": "go-x",
                    "interventions": [{"category": "manual", "minutes": 5}],
                }
            )
        )
        with pytest.raises(run_record.RunRecordFormatError) as exc:
            run_record._load(rec)
        assert "worktrail-run-record start" in str(exc.value)

    def test_canonical_record_still_loads(self, tmp_path):
        rec = tmp_path / "go-y.yaml"
        run_record._save(
            rec,
            {
                "run_id": "go-y",
                "repository": "r",
                "decisions": ["one"],
                "final_status": None,
            },
        )
        loaded = run_record._load(rec)
        assert loaded["run_id"] == "go-y"
        assert loaded["decisions"] == ["one"]
        assert loaded["final_status"] is None


class TestDashboardWiring:
    def test_render_dashboard_shows_stranded_section(self):
        from worktrail.router import dashboard

        rendered = dashboard.render_dashboard(
            repo_rows=[
                {
                    "repo": "myapp",
                    "active_specs": [],
                    "backlog_ids": [],
                    "worktrees": [],
                    "policy_findings": [],
                    "automerge_findings": [],
                    "drift_findings": [],
                    "quarantine_findings": [],
                    "quarantine_resumable": [],
                    "journal_findings": [
                        {"kind": "stranded-tail", "spec_id": "008-x", "journal": "j", "detail": "d"}
                    ],
                }
            ],
            spec_rows=None,
            inflight=[],
            queue_briefs=[],
        )
        assert "Stranded runs (1)" in rendered
        assert "myapp (008-x: stranded-tail)" in rendered

    def test_clean_repo_renders_no_stranded_section(self):
        from worktrail.router import dashboard

        rendered = dashboard.render_dashboard(
            repo_rows=[
                {
                    "repo": "myapp",
                    "active_specs": [],
                    "backlog_ids": [],
                    "worktrees": [],
                    "policy_findings": [],
                    "automerge_findings": [],
                    "drift_findings": [],
                    "quarantine_findings": [],
                    "quarantine_resumable": [],
                    "journal_findings": [],
                }
            ],
            spec_rows=None,
            inflight=[],
            queue_briefs=[],
        )
        assert "Stranded runs" not in rendered
