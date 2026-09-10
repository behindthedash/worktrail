#!/usr/bin/env python3
"""Tests for smoke_flake_selfcheck.py — per-repo aggregation of the
`smoke_flakes` map that `integrate._record_smoke_flake` writes into run
journals when a smoke suite passes only on retry."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from worktrail.router import smoke_flake_selfcheck

DAY = 86400
NOW = 1_800_000_000.0


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "projects" / "myapp"
    repo.mkdir(parents=True, exist_ok=True)
    (tmp_path / "projects" / "myapp-worktrees").mkdir(exist_ok=True)
    return repo


def _journal(repo: Path, spec_id: str, payload, *, age_days: float = 0) -> Path:
    f = repo.parent / "myapp-worktrees" / f"run-{spec_id}.json"
    f.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    mtime = NOW - age_days * DAY
    os.utime(f, (mtime, mtime))
    return f


class TestAggregation:
    def test_multi_run_aggregation_and_ordering(self, tmp_path):
        repo = _repo(tmp_path)
        _journal(repo, "spec-a", {"smoke_flakes": {"unit": "a-unit"}}, age_days=3)
        _journal(
            repo,
            "spec-b",
            {"smoke_flakes": {"unit": "b-unit", "e2e": "b-e2e"}},
            age_days=1,
        )
        _journal(repo, "spec-c", {"smoke_flakes": {"alpha": "c-alpha"}}, age_days=2)
        entries = smoke_flake_selfcheck.check_repo(repo, now=NOW)["entries"]
        assert [e["suite"] for e in entries] == ["unit", "alpha", "e2e"]
        unit = entries[0]
        assert unit["count"] == 2
        assert unit["runs"] == ["spec-b", "spec-a"]  # most recent first
        assert unit["detail"] == "b-unit"
        assert unit["recurrence"] == "recurring"
        assert entries[1]["recurrence"] == "single"

    def test_no_journals_is_empty(self, tmp_path):
        repo = _repo(tmp_path)
        assert smoke_flake_selfcheck.check_repo(repo, now=NOW) == {"entries": []}

    def test_no_worktrees_dir_is_empty(self, tmp_path):
        repo = tmp_path / "lonely"
        repo.mkdir()
        assert smoke_flake_selfcheck.check_repo(repo, now=NOW) == {"entries": []}

    def test_journal_without_flakes_is_empty(self, tmp_path):
        repo = _repo(tmp_path)
        _journal(repo, "spec-a", {"groups": {}})
        _journal(repo, "spec-b", {"smoke_flakes": {}})
        assert smoke_flake_selfcheck.check_repo(repo, now=NOW)["entries"] == []


class TestWindow:
    def test_stale_journal_excluded_and_wider_window_includes_it(self, tmp_path):
        repo = _repo(tmp_path)
        _journal(repo, "old", {"smoke_flakes": {"unit": "old"}}, age_days=45)
        _journal(repo, "new", {"smoke_flakes": {"unit": "new"}}, age_days=1)
        entries = smoke_flake_selfcheck.check_repo(repo, now=NOW)["entries"]
        assert entries[0]["runs"] == ["new"]
        assert entries[0]["recurrence"] == "single"
        wide = smoke_flake_selfcheck.check_repo(repo, window_days=60, now=NOW)
        assert wide["entries"][0]["runs"] == ["new", "old"]
        assert wide["entries"][0]["recurrence"] == "recurring"


class TestRecurrence:
    def test_two_runs_is_recurring(self, tmp_path):
        repo = _repo(tmp_path)
        _journal(repo, "a", {"smoke_flakes": {"unit": "x"}}, age_days=1)
        _journal(repo, "b", {"smoke_flakes": {"unit": "y"}}, age_days=2)
        [e] = smoke_flake_selfcheck.check_repo(repo, now=NOW)["entries"]
        assert e["recurrence"] == "recurring"
        assert e["count"] == 2

    def test_one_run_is_single(self, tmp_path):
        repo = _repo(tmp_path)
        _journal(repo, "a", {"smoke_flakes": {"unit": "x"}})
        [e] = smoke_flake_selfcheck.check_repo(repo, now=NOW)["entries"]
        assert e["recurrence"] == "single"
        assert e["count"] == 1


class TestResilience:
    def test_malformed_journals_are_skipped_not_raised(self, tmp_path):
        repo = _repo(tmp_path)
        _journal(repo, "good", {"smoke_flakes": {"unit": "ok"}})
        _journal(repo, "garbage", "{not json")
        _journal(repo, "list-root", ["smoke_flakes"])
        _journal(repo, "flakes-not-map", {"smoke_flakes": ["unit"]})
        _journal(repo, "flakes-bad-values", {"smoke_flakes": {"unit": 3}})
        entries = smoke_flake_selfcheck.check_repo(repo, now=NOW)["entries"]
        assert len(entries) == 1
        assert entries[0]["runs"] == ["good"]

    def test_live_lock_held_journal_is_counted(self, tmp_path):
        import fcntl

        repo = _repo(tmp_path)
        _journal(repo, "live", {"smoke_flakes": {"unit": "live"}})
        lock = repo.parent / "myapp-worktrees" / "run-live.lock"
        fh = open(lock, "a")  # noqa: SIM115 -- held across the surrounding scope as a lock file
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            entries = smoke_flake_selfcheck.check_repo(repo, now=NOW)["entries"]
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            fh.close()
        assert [e["runs"] for e in entries] == [["live"]]


class TestCli:
    def test_clean_exits_zero(self, tmp_path, capsys):
        repo = _repo(tmp_path)
        rc = smoke_flake_selfcheck.main(["--repo", str(repo)])
        assert rc == 0
        assert "clean" in capsys.readouterr().out

    def test_entries_exit_one_human_and_json(self, tmp_path, capsys):
        repo = _repo(tmp_path)
        f = _journal(repo, "a", {"smoke_flakes": {"unit": "exit 1: boom"}})
        os.utime(f, None)  # fresh mtime: main() uses the real clock
        rc = smoke_flake_selfcheck.main(["--repo", str(repo)])
        assert rc == 1
        out = capsys.readouterr().out
        assert "unit" in out and "exit 1: boom" in out
        rc = smoke_flake_selfcheck.main(["--repo", str(repo), "--json"])
        assert rc == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["entries"][0]["suite"] == "unit"

    def test_window_days_flag_excludes_stale(self, tmp_path):
        repo = _repo(tmp_path)
        f = _journal(repo, "a", {"smoke_flakes": {"unit": "x"}})
        old = time.time() - 10 * DAY
        os.utime(f, (old, old))
        assert (
            smoke_flake_selfcheck.main(["--repo", str(repo), "--window-days", "5"]) == 0
        )
        assert (
            smoke_flake_selfcheck.main(["--repo", str(repo), "--window-days", "30"])
            == 1
        )
