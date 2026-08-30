"""Unit tests for router/audit_postmerge.py marker persistence — no live `gh`
calls; subprocess is faked. Mirrors tests/router/test_reconcile_pr_labels.py
conventions."""

import json
from datetime import datetime, timedelta, timezone

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
    assert (
        audit.first_run_lookback(lookback_days=7, now=now)
        == (now - timedelta(days=7)).isoformat()
    )


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

    result = audit.list_merged_prs(tmp_path / "repo", since="2026-01-01T00:00:00+00:00")

    assert result is None
    assert audit.read_marker("repo", state_dir) == "2026-01-01T00:00:00+00:00"


def test_marker_unchanged_when_gh_binary_missing(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    def fake_run(cmd, **kwargs):
        raise FileNotFoundError("gh not found")

    monkeypatch.setattr(audit.subprocess, "run", fake_run)

    result = audit.list_merged_prs(tmp_path / "repo", since="2026-01-01T00:00:00+00:00")

    assert result is None
    assert audit.read_marker("repo", state_dir) == "2026-01-01T00:00:00+00:00"


def test_marker_unchanged_when_gh_pr_view_fails_partway(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    audit.write_marker("repo", state_dir, "2026-01-01T00:00:00+00:00")

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [{"url": "u", "number": 1, "mergedAt": "2026-01-02T00:00:00+00:00"}]
                ),
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(1, "", "gh: not found")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(audit.subprocess, "run", fake_run)

    result = audit.list_merged_prs(tmp_path / "repo", since="2026-01-01T00:00:00+00:00")

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


# ---------------------------------------------------------------------------
# classify_checks() reuse


def _fake_gh_single_pr(rollup):
    """A fake `subprocess.run` returning one merged PR whose `statusCheckRollup`
    is `rollup` -- for exercising `sweep_repo()`'s reuse of `classify_checks()`."""

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "u1",
                            "number": 1,
                            "mergedAt": "2026-01-02T00:00:00+00:00",
                        }
                    ]
                ),
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    {
                        "url": "u1",
                        "number": 1,
                        "mergedAt": "2026-01-02T00:00:00+00:00",
                        "statusCheckRollup": rollup,
                    }
                ),
            )
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_run


def test_sweep_repo_flags_merged_pr_with_failing_required_check(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        _fake_gh_single_pr(
            [
                {"name": "ci/tests", "status": "COMPLETED", "conclusion": "FAILURE"},
            ]
        ),
    )

    result = audit.sweep_repo(tmp_path / "repo", state_dir, repo_name="repo")

    assert result["checked"] == 1
    assert len(result["flagged"]) == 1
    assert result["flagged"][0]["failing_checks"] == ["ci/tests"]


def test_sweep_repo_does_not_flag_merged_pr_with_all_green_checks(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        _fake_gh_single_pr(
            [
                {"name": "ci/tests", "status": "COMPLETED", "conclusion": "SUCCESS"},
            ]
        ),
    )

    result = audit.sweep_repo(tmp_path / "repo", state_dir, repo_name="repo")

    assert result["checked"] == 1
    assert result["flagged"] == []


def test_sweep_repo_does_not_flag_informational_check_failure(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(
        audit.subprocess,
        "run",
        _fake_gh_single_pr(
            [
                {
                    "name": "E2E (informational)",
                    "status": "COMPLETED",
                    "conclusion": "FAILURE",
                },
            ]
        ),
    )

    result = audit.sweep_repo(tmp_path / "repo", state_dir, repo_name="repo")

    assert result["checked"] == 1
    assert result["flagged"] == []


# ---------------------------------------------------------------------------
# --max-prs capping


def _fake_gh_many_candidates(count):
    """A fake `subprocess.run` whose `gh pr list` returns `count` candidate
    merged PRs (ignoring the `--limit` flag, as if the real `gh` returned
    more candidates than the cap) in increasing `mergedAt` order, and whose
    `gh pr view` raises if called for a PR number beyond `DEFAULT_MAX_PRS`'s
    -- i.e. beyond whatever cap the caller passed -- worth of candidates, so
    a test can prove the module's own defensive `listed[:max_prs]` slice
    (not just the `--limit` flag it also passes to `gh`) is what stops
    fetching, and that no PR past the cap is ever fetched at all."""
    items = [
        {"url": f"u{i}", "number": i, "mergedAt": f"2026-01-{i:02d}T00:00:00+00:00"}
        for i in range(1, count + 1)
    ]

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps(items))
        if cmd[:3] == ["gh", "pr", "view"]:
            number = int(cmd[3])
            if number > count:
                raise AssertionError(
                    f"gh pr view called for uncapped PR number {number}"
                )
            return _FakeCompleted(
                0,
                json.dumps(
                    {
                        "url": f"u{number}",
                        "number": number,
                        "mergedAt": f"2026-01-{number:02d}T00:00:00+00:00",
                        "statusCheckRollup": [],
                    }
                ),
            )
        raise AssertionError(f"unexpected command: {cmd}")

    return fake_run


def test_list_merged_prs_only_fetches_max_prs_worth_of_candidates(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(audit.subprocess, "run", _fake_gh_many_candidates(5))

    result = audit.list_merged_prs(
        tmp_path / "repo", since="2026-01-01T00:00:00+00:00", max_prs=3
    )

    assert result is not None
    assert len(result) == 3
    assert [pr["number"] for pr in result] == [1, 2, 3]


def test_sweep_repo_checked_count_capped_at_max_prs(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(audit.subprocess, "run", _fake_gh_many_candidates(5))

    result = audit.sweep_repo(tmp_path / "repo", state_dir, repo_name="repo", max_prs=3)

    assert result["checked"] == 3


def test_sweep_repo_marker_advances_only_past_the_capped_prs_actually_processed(
    tmp_path, monkeypatch
):
    state_dir = tmp_path / "state"
    monkeypatch.setattr(audit.subprocess, "run", _fake_gh_many_candidates(5))

    audit.sweep_repo(tmp_path / "repo", state_dir, repo_name="repo", max_prs=3)

    # PR 3 (not PR 5) is the latest among the 3 actually fetched this sweep;
    # PRs 4 and 5 stay in-window for the next sweep rather than being
    # silently skipped past.
    assert audit.read_marker("repo", state_dir) == "2026-01-03T00:00:00+00:00"


# ---------------------------------------------------------------------------
# dashboard_snapshot()


def test_dashboard_snapshot_empty_state_dir_returns_empty_summary(tmp_path):
    state_dir = tmp_path / "state"  # never created

    assert audit.dashboard_snapshot(state_dir) == {
        "repos_flagged": 0,
        "prs_flagged": 0,
        "flagged": [],
    }


def test_dashboard_snapshot_with_flagged_prs_returns_them(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "repo-a.json").write_text(
        json.dumps(
            {
                "last_swept_at": "2026-01-01T00:00:00+00:00",
                "flagged": [
                    {
                        "repo": "repo-a",
                        "url": "u1",
                        "failing_checks": ["ci/tests"],
                        "merged_at": "2026-01-01T00:00:00+00:00",
                    },
                ],
            }
        )
    )
    (state_dir / "repo-b.json").write_text(
        json.dumps(
            {
                "last_swept_at": "2026-01-02T00:00:00+00:00",
                "flagged": [
                    {
                        "repo": "repo-b",
                        "url": "u2",
                        "failing_checks": ["ci/lint"],
                        "merged_at": "2026-01-02T00:00:00+00:00",
                    },
                    {
                        "repo": "repo-b",
                        "url": "u3",
                        "failing_checks": ["ci/build"],
                        "merged_at": "2026-01-02T00:00:00+00:00",
                    },
                ],
            }
        )
    )

    summary = audit.dashboard_snapshot(state_dir)

    assert summary["repos_flagged"] == 2
    assert summary["prs_flagged"] == 3
    assert {entry["url"] for entry in summary["flagged"]} == {"u1", "u2", "u3"}


def test_dashboard_snapshot_with_only_clean_sweeps_returns_empty(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "repo-a.json").write_text(
        json.dumps(
            {
                "last_swept_at": "2026-01-01T00:00:00+00:00",
                "flagged": [],
            }
        )
    )
    (state_dir / "repo-b.json").write_text(
        json.dumps(
            {
                "last_swept_at": "2026-01-02T00:00:00+00:00",
            }
        )
    )

    assert audit.dashboard_snapshot(state_dir) == {
        "repos_flagged": 0,
        "prs_flagged": 0,
        "flagged": [],
    }
