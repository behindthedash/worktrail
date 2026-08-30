#!/usr/bin/env python3
"""Tests for release_gate.py — the N-consecutive-clean-runs readiness readout."""

from __future__ import annotations

from pathlib import Path

from worktrail.router import release_gate, run_record


def _mk_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "projects" / "myapp"
    (repo / ".worktrail").mkdir(parents=True)
    (repo / ".worktrail" / "policy.yaml").write_text(
        f"release_gate: v1.0\nrun_record_dir: {tmp_path}/runs\n"
    )
    return repo


def _write_run(
    tmp_path: Path, name: str, status: str | None, interventions: int = 0
) -> Path:
    d = tmp_path / "runs" / "myapp"
    d.mkdir(parents=True, exist_ok=True)
    rec = {"run_id": name, "final_status": status, "interventions": []}
    for i in range(interventions):
        rec["interventions"].append({"category": "manual", "note": f"i{i}"})
    p = d / f"{name}.yaml"
    run_record._save(p, rec)
    return p


def test_streak_counts_consecutive_clean_runs(tmp_path):
    repo = _mk_repo(tmp_path)
    _write_run(tmp_path, "go-001", "completed_and_merged")
    _write_run(tmp_path, "go-002", "completed_pr_open")
    _write_run(tmp_path, "go-003", "completed_and_merged")
    result = release_gate.evaluate(repo, target=5)
    assert result["streak"] == 3
    assert result["met"] is False
    assert result["release_gate"] == "v1.0"


def test_intervention_breaks_the_streak(tmp_path):
    repo = _mk_repo(tmp_path)
    _write_run(tmp_path, "go-001", "completed_and_merged")
    _write_run(tmp_path, "go-002", "completed_and_merged", interventions=1)
    _write_run(tmp_path, "go-003", "completed_and_merged")
    result = release_gate.evaluate(repo, target=5)
    # newest-first: go-003 clean, go-002 breaks — streak stops at 1.
    assert result["streak"] == 1


def test_failed_status_breaks_the_streak(tmp_path):
    repo = _mk_repo(tmp_path)
    _write_run(tmp_path, "go-001", "failed_recoverable")
    _write_run(tmp_path, "go-002", "completed_and_merged")
    result = release_gate.evaluate(repo, target=5)
    assert result["streak"] == 1


def test_in_flight_run_is_skipped_not_a_break(tmp_path):
    repo = _mk_repo(tmp_path)
    _write_run(tmp_path, "go-001", "completed_and_merged")
    _write_run(tmp_path, "go-002", None)  # in flight
    _write_run(tmp_path, "go-003", "completed_and_merged")
    result = release_gate.evaluate(repo, target=2)
    assert result["streak"] == 2
    assert result["met"] is True


def test_malformed_record_breaks_the_streak(tmp_path):
    repo = _mk_repo(tmp_path)
    _write_run(tmp_path, "go-001", "completed_and_merged")
    bad = tmp_path / "runs" / "myapp" / "go-002.yaml"
    bad.write_text("interventions:\n- category: manual\n  minutes: 5\n")  # generic YAML
    _write_run(tmp_path, "go-003", "completed_and_merged")
    result = release_gate.evaluate(repo, target=5)
    assert result["streak"] == 1  # go-003 clean, go-002 malformed -> break


def test_gate_met_exit_code_and_target(tmp_path):
    repo = _mk_repo(tmp_path)
    for i in range(5):
        _write_run(tmp_path, f"go-00{i}", "completed_and_merged")
    assert release_gate.main(["--repo", str(repo), "--target", "5", "--json"]) == 0
    assert release_gate.main(["--repo", str(repo), "--target", "6", "--json"]) == 1


def test_no_runs_dir_is_zero_streak(tmp_path):
    repo = _mk_repo(tmp_path)
    result = release_gate.evaluate(repo, target=5)
    assert result["streak"] == 0
    assert result["met"] is False
