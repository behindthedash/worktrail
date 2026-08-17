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


class TestUnreconciledTailEvidence:
    """The sibling bug class to stranded-tail: the tail task DID dispatch and
    finish, but its own commit never merged onto base (brief 20260812-152318)."""

    def test_unreconciled_evidence_is_flagged(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {
                "integrate_complete": True,
                "unreconciled_tail_evidence": [
                    {"task": "T022", "worktree": "/wt/008-x-t022", "head_sha": "abc123"}
                ],
            },
        )
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert len(findings) == 1
        assert findings[0]["kind"] == "unreconciled-tail-evidence"
        assert findings[0]["spec_id"] == "008-x"
        assert "T022" in findings[0]["detail"]

    def test_no_unreconciled_evidence_is_clean(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {"integrate_complete": True, "unreconciled_tail_evidence": [], "groups": {}},
        )
        assert journal_selfcheck.check_repo(repo)["findings"] == []

    def test_live_runlock_suppresses_the_finding(self, tmp_path):
        import fcntl

        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {"unreconciled_tail_evidence": [{"task": "T022"}]},
        )
        lock = repo.parent / "myapp-worktrees" / "run-008-x.lock"
        fh = open(lock, "a")
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            assert journal_selfcheck.check_repo(repo)["findings"] == []
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()

    def test_composes_with_stranded_tail_for_the_same_spec(self, tmp_path):
        # A run can carry both signals at once: one tail task never dispatched
        # (stranded-tail) while a different, earlier tail task dispatched and
        # finished but was never reconciled (unreconciled-tail-evidence).
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {
                "integrate_complete": True,
                "pending_tail_tasks": ["T023"],
                "unreconciled_tail_evidence": [{"task": "T022"}],
            },
        )
        kinds = {f["kind"] for f in journal_selfcheck.check_repo(repo)["findings"]}
        assert kinds == {"stranded-tail", "unreconciled-tail-evidence"}

    @pytest.mark.parametrize("reconcile_state", ["opened", "already-open"])
    def test_open_auto_reconciliation_pr_gets_informational_wording(self, tmp_path, reconcile_state):
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {
                "integrate_complete": True,
                "unreconciled_tail_evidence": [
                    {
                        "task": "T022",
                        "reconcile_state": reconcile_state,
                        "reconcile_pr_url": "https://github.com/acme/myapp/pull/42",
                    }
                ],
            },
        )
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert len(findings) == 1
        detail = findings[0]["detail"]
        assert "T022" in detail
        assert "https://github.com/acme/myapp/pull/42" in detail
        assert "auto-reconciliation" in detail and "awaiting merge" in detail
        assert "reconcile before the worktree is cleaned up" not in detail

    def test_superseded_gets_informational_wording_not_manual_triage(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {
                "integrate_complete": True,
                "unreconciled_tail_evidence": [
                    {
                        "task": "T021",
                        "reconcile_state": "superseded",
                        "reconcile_pr_url": "",
                        "reconcile_superseded_by": "T022",
                    }
                ],
            },
        )
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert len(findings) == 1
        detail = findings[0]["detail"]
        assert "T021" in detail
        assert "superseded by T022" in detail
        assert "auto-reconciliation" in detail and "awaiting merge" in detail
        assert "reconcile before the worktree is cleaned up" not in detail

    @pytest.mark.parametrize("reconcile_state", ["quarantined", None])
    def test_unresolved_state_keeps_manual_triage_wording(self, tmp_path, reconcile_state):
        entry: "dict[str, object]" = {"task": "T022"}
        if reconcile_state is not None:
            entry["reconcile_state"] = reconcile_state
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {"integrate_complete": True, "unreconciled_tail_evidence": [entry]},
        )
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert len(findings) == 1
        detail = findings[0]["detail"]
        assert "T022" in detail
        assert "reconcile before the worktree is cleaned up" in detail
        assert "auto-reconciliation" not in detail

    def test_all_merged_emits_no_finding(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {
                "integrate_complete": True,
                "unreconciled_tail_evidence": [
                    {"task": "T022", "reconcile_state": "merged"},
                    {"task": "T023", "reconcile_state": "merged"},
                ],
            },
        )
        assert journal_selfcheck.check_repo(repo)["findings"] == []

    def test_mixed_states_report_both_wordings_in_one_finding(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path,
            "008-x",
            {
                "integrate_complete": True,
                "unreconciled_tail_evidence": [
                    {"task": "T022", "reconcile_state": "quarantined"},
                    {
                        "task": "T023",
                        "reconcile_state": "opened",
                        "reconcile_pr_url": "https://github.com/acme/myapp/pull/43",
                    },
                    {"task": "T024", "reconcile_state": "merged"},
                ],
            },
        )
        findings = journal_selfcheck.check_repo(repo)["findings"]
        assert len(findings) == 1
        detail = findings[0]["detail"]
        assert "T022" in detail and "reconcile before the worktree is cleaned up" in detail
        assert "T023" in detail and "auto-reconciliation" in detail
        assert "https://github.com/acme/myapp/pull/43" in detail
        assert "T024" not in detail


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


_CORRUPTED_RECORD_TEXT = (
    "run_id: go-corrupted\n"
    "request_summary: fix the thing across a line that\n"
    "  wraps unexpectedly without quoting\n"
)


class TestMalformedRunRecord:
    """run_record.py's directory scans (active-conflicts, prune) already skip
    a malformed record and keep going -- this is the dashboard-visibility half
    of the same fix: the degraded file should show up in the existing
    Stranded runs section instead of only ever surfacing in a scan's
    `warnings` field.
    """

    def test_malformed_record_is_flagged(self, tmp_path):
        repo = tmp_path / "projects" / "myapp"
        repo.mkdir(parents=True)
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "myapp"
        run_dir.mkdir(parents=True)
        (run_dir / "go-corrupted.yaml").write_text(_CORRUPTED_RECORD_TEXT)

        findings = journal_selfcheck.check_repo(repo, run_record_dir=runs_dir)["findings"]

        assert len(findings) == 1
        assert findings[0]["kind"] == "malformed-run-record"
        assert findings[0]["spec_id"] == "go-corrupted"
        assert "go-corrupted.yaml" in findings[0]["journal"]

    def test_canonical_records_are_clean(self, tmp_path):
        repo = tmp_path / "projects" / "myapp"
        repo.mkdir(parents=True)
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "myapp"
        run_dir.mkdir(parents=True)
        run_record._save(
            run_dir / "go-y.yaml",
            {"run_id": "go-y", "repository": "r", "decisions": [], "final_status": None},
        )

        assert journal_selfcheck.check_repo(repo, run_record_dir=runs_dir)["findings"] == []

    def test_no_run_record_dir_is_clean(self, tmp_path):
        repo = tmp_path / "projects" / "myapp"
        repo.mkdir(parents=True)

        findings = journal_selfcheck.check_repo(
            repo, run_record_dir=tmp_path / "never-created"
        )["findings"]

        assert findings == []

    def test_defaults_to_env_override_dir(self, tmp_path, monkeypatch):
        repo = tmp_path / "projects" / "myapp"
        repo.mkdir(parents=True)
        runs_dir = tmp_path / "custom-runs"
        run_dir = runs_dir / "myapp"
        run_dir.mkdir(parents=True)
        (run_dir / "go-corrupted.yaml").write_text(_CORRUPTED_RECORD_TEXT)
        monkeypatch.setenv("GO_RUN_RECORD_DIR", str(runs_dir))

        findings = journal_selfcheck.check_repo(repo)["findings"]

        assert len(findings) == 1
        assert findings[0]["kind"] == "malformed-run-record"

    def test_composes_with_journal_findings_for_the_same_repo(self, tmp_path):
        repo = _repo_with_journal(
            tmp_path, "008-x",
            {"integrate_complete": True, "pending_tail_tasks": ["3.1"], "groups": {}},
        )
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "myapp"
        run_dir.mkdir(parents=True)
        (run_dir / "go-corrupted.yaml").write_text(_CORRUPTED_RECORD_TEXT)

        kinds = {
            f["kind"]
            for f in journal_selfcheck.check_repo(repo, run_record_dir=runs_dir)["findings"]
        }

        assert kinds == {"stranded-tail", "malformed-run-record"}


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
