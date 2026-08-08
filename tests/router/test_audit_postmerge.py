"""Unit tests for router/audit_postmerge.py marker persistence — no live `gh`
calls; subprocess is faked. Mirrors tests/router/test_reconcile_pr_labels.py
conventions."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from worktrail.router import audit_postmerge as audit


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# ---------------------------------------------------------------------------
# first-run lookback default

def test_first_run_lookback_is_n_days_before_now():
    now = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert audit.first_run_lookback(lookback_days=7, now=now) == (
        now - timedelta(days=7)
    ).isoformat()


def test_effective_since_with_no_marker_falls_back_to_first_run_lookback(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(audit, "first_run_lookback", lambda days: "SENTINEL-LOOKBACK")
    state_dir = tmp_path / "state"

    assert audit.effective_since("repo", state_dir) == "SENTINEL-LOOKBACK"


# ---------------------------------------------------------------------------
# marker advances on success

def test_write_marker_then_read_marker_round_trips(tmp_path):
    state_dir = tmp_path / "state"

    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    assert audit.read_marker("repo", state_dir) == "2026-01-01T00:00:00+00:00"


def test_write_marker_advances_effective_since_past_previous_value(tmp_path):
    state_dir = tmp_path / "state"
    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    audit.write_marker("repo", state_dir, "2026-01-08T00:00:00+00:00")

    assert audit.read_marker("repo", state_dir) == "2026-01-08T00:00:00+00:00"
    assert audit.effective_since("repo", state_dir) == "2026-01-08T00:00:00+00:00"


def test_write_marker_preserves_other_persisted_state_keys(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "repo.json").write_text(json.dumps({"flagged": ["pr1"]}))

    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    assert audit.load_state("repo", state_dir) == {
        "flagged": ["pr1"],
        "last_swept_at": "2026-01-01T00:00:00+00:00",
    }


# ---------------------------------------------------------------------------
# marker unchanged on `gh` failure

def test_marker_unchanged_when_gh_pr_list_fails(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    def fake_run(cmd, **kwargs):
        return _FakeCompleted(1, "", "gh: command failed")

    monkeypatch.setattr(audit.subprocess, "run", fake_run)

    result = audit.list_merged_prs(
        tmp_path / "repo", since="2026-01-01T00:00:00+00:00"
    )

    assert result is None
    assert audit.read_marker("repo", state_dir) == "2026-01-01T00:00:00+00:00"


def test_marker_unchanged_when_gh_binary_missing(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(audit.subprocess, "run", fake_run)

    result = audit.list_merged_prs(
        tmp_path / "repo", since="2026-01-01T00:00:00+00:00"
    )

    assert result is None
    assert audit.read_marker("repo", state_dir) == "2026-01-01T00:00:00+00:00"


def test_marker_unchanged_when_gh_pr_view_fails_partway(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0, json.dumps([{"url": "u", "number": 1, "mergedAt": "2026-01-02T00:00:00+00:00"}])
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(1, "", "gh: not found")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(audit.subprocess, "run", fake_run)

    result = audit.list_merged_prs(
        tmp_path / "repo", since="2026-01-01T00:00:00+00:00"
    )

    assert result is None
    assert audit.read_marker("repo", state_dir) == "2026-01-01T00:00:00+00:00"


# ---------------------------------------------------------------------------
# corrupt/missing marker degrades to first-run window

def test_missing_marker_degrades_to_first_run_lookback(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "first_run_lookback", lambda days: "SENTINEL-LOOKBACK")
    state_dir = tmp_path / "state"  # never created

    assert audit.read_marker("repo", state_dir) is None
    assert audit.effective_since("repo", state_dir) == "SENTINEL-LOOKBACK"


def test_corrupt_json_marker_degrades_to_first_run_lookback(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "first_run_lookback", lambda days: "SENTINEL-LOOKBACK")
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "repo.json").write_text("{not valid json")

    assert audit.read_marker("repo", state_dir) is None
    assert audit.effective_since("repo", state_dir) == "SENTINEL-LOOKBACK"


def test_marker_value_wrong_type_degrades_to_first_run_window(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "repo.json").write_text(json.dumps({"last_swept_at": 12345}))

    assert audit.read_marker("repo", state_dir) is None


def test_marker_value_not_iso8601_degrades_to_first_run_window(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "repo.json").write_text(json.dumps({"last_swept_at": "not-a-date"}))

    assert audit.read_marker("repo", state_dir) is None


def test_state_file_not_a_json_object_degrades_to_first_run_window(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "repo.json").write_text(json.dumps([1, 2, 3]))

    assert audit.load_state("repo", state_dir) == {}
    assert audit.read_marker("repo", state_dir) is None
