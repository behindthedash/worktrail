import json
from datetime import datetime, timezone

from worktrail.drain import stuck_remediation


def _finding(repo="repo-a", spec_id="spec-1"):
    return {"repo": repo, "spec_id": spec_id}


def test_repeated_recurrence_increments_streak_and_flags_at_threshold():
    history = {"version": 1, "identities": {}}
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    resumed = {"sync_pending": [_finding()]}

    for expected_streak in (1, 2):
        history, stuck = stuck_remediation.record_and_detect(
            history,
            resumed,
            now,
            threshold=3,
            retention=stuck_remediation.DEFAULT_RETENTION,
        )
        ident = history["identities"]["sync_pending::repo-a::spec-1"]
        assert ident["streak"] == expected_streak
        assert stuck == []

    history, stuck = stuck_remediation.record_and_detect(
        history,
        resumed,
        now,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
    )

    assert history["identities"]["sync_pending::repo-a::spec-1"]["streak"] == 3
    assert stuck == [
        {"key": "sync_pending", "repo_name": "repo-a", "spec_id": "spec-1", "streak": 3}
    ]


def test_identity_absent_from_resumed_drops_out_instead_of_persisting_streak():
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    history = {
        "version": 1,
        "identities": {
            "sync_pending::repo-a::spec-1": {"streak": 2, "last_seen": now.isoformat()},
        },
    }

    history, stuck = stuck_remediation.record_and_detect(
        history, {}, now, threshold=3, retention=stuck_remediation.DEFAULT_RETENTION
    )

    assert history["identities"] == {}
    assert stuck == []

    # A subsequent sweep re-affirming the same finding starts its streak over
    # rather than resuming from the dropped value.
    history, stuck = stuck_remediation.record_and_detect(
        history,
        {"sync_pending": [_finding()]},
        now,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
    )

    assert history["identities"]["sync_pending::repo-a::spec-1"]["streak"] == 1
    assert stuck == []


def test_independent_tracking_across_repo_spec_identities():
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    history = {"version": 1, "identities": {}}
    resumed = {
        "sync_pending": [
            _finding(repo="repo-a", spec_id="spec-1"),
            _finding(repo="repo-b", spec_id="spec-1"),
            _finding(repo="repo-a", spec_id="spec-2"),
        ]
    }

    history, _ = stuck_remediation.record_and_detect(
        history,
        resumed,
        now,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
    )
    # Only two of the three findings recur in this sweep.
    history, _ = stuck_remediation.record_and_detect(
        history,
        {
            "sync_pending": [
                _finding(repo="repo-a", spec_id="spec-1"),
                _finding(repo="repo-b", spec_id="spec-1"),
            ]
        },
        now,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
    )

    assert history["identities"]["sync_pending::repo-a::spec-1"]["streak"] == 2
    assert history["identities"]["sync_pending::repo-b::spec-1"]["streak"] == 2
    assert "sync_pending::repo-a::spec-2" not in history["identities"]


def test_independent_tracking_across_different_remediation_keys():
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    history = {"version": 1, "identities": {}}
    resumed = {
        "sync_pending": [_finding(repo="repo-a", spec_id="spec-1")],
        "verify_pending": [_finding(repo="repo-a", spec_id="spec-1")],
    }

    history, _ = stuck_remediation.record_and_detect(
        history,
        resumed,
        now,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
    )
    # Only sync_pending recurs on the next sweep.
    history, stuck = stuck_remediation.record_and_detect(
        history,
        {"sync_pending": [_finding(repo="repo-a", spec_id="spec-1")]},
        now,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
    )

    assert history["identities"]["sync_pending::repo-a::spec-1"]["streak"] == 2
    assert "verify_pending::repo-a::spec-1" not in history["identities"]
    assert stuck == []


def test_load_missing_file_degrades_to_empty_history(tmp_path):
    path = tmp_path / "does-not-exist.json"
    assert stuck_remediation.load(path) == {"version": 1, "identities": {}}


def test_load_corrupt_file_degrades_to_empty_history(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("not-json", encoding="utf-8")
    assert stuck_remediation.load(path) == {"version": 1, "identities": {}}


def test_save_writes_atomically(tmp_path):
    path = tmp_path / "history.json"
    value = {"version": 1, "identities": {"k::r::s": {"streak": 1, "last_seen": "now"}}}

    stuck_remediation.save(value, path)

    assert json.loads(path.read_text(encoding="utf-8")) == value
    assert list(tmp_path.glob(".history.json.*")) == []


def test_sweep_and_record_round_trip_across_calls(tmp_path):
    path = tmp_path / "history.json"
    now = datetime(2026, 8, 17, tzinfo=timezone.utc)
    resumed = {"sync_pending": [_finding()]}

    stuck_first = stuck_remediation.sweep_and_record(
        resumed,
        path,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
        now=now,
    )
    assert stuck_first == []
    assert (
        json.loads(path.read_text(encoding="utf-8"))["identities"][
            "sync_pending::repo-a::spec-1"
        ]["streak"]
        == 1
    )

    stuck_second = stuck_remediation.sweep_and_record(
        resumed,
        path,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
        now=now,
    )
    assert stuck_second == []
    assert (
        json.loads(path.read_text(encoding="utf-8"))["identities"][
            "sync_pending::repo-a::spec-1"
        ]["streak"]
        == 2
    )

    stuck_third = stuck_remediation.sweep_and_record(
        resumed,
        path,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
        now=now,
    )
    assert stuck_third == [
        {"key": "sync_pending", "repo_name": "repo-a", "spec_id": "spec-1", "streak": 3}
    ]


def test_sweep_and_record_recovers_from_corrupt_history_file(tmp_path):
    path = tmp_path / "history.json"
    path.write_text("not-json", encoding="utf-8")

    stuck = stuck_remediation.sweep_and_record(
        {"sync_pending": [_finding()]},
        path,
        threshold=3,
        retention=stuck_remediation.DEFAULT_RETENTION,
        now=datetime(2026, 8, 17, tzinfo=timezone.utc),
    )

    assert stuck == []
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["identities"]["sync_pending::repo-a::spec-1"]["streak"] == 1
