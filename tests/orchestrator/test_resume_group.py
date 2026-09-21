"""Tests for worktrail.orchestrator.resume_group -- the un-quarantine CLI.

Hermetic: every journal lives under tmp_path, no network, no git.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from worktrail.orchestrator import resume_group
from worktrail.orchestrator.integrate import QUARANTINE_BUDGET_EXHAUSTED


def _write_journal(tmp_path: Path, groups: dict, **extra) -> tuple[Path, Path, str]:
    """Lay out a repo + its sibling worktrees dir, write a run journal, and
    return (repo, journal_path, spec)."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    wt = tmp_path / "myrepo-worktrees"
    wt.mkdir()
    spec = "openspec/changes/some-change"
    journal_path = wt / "run-some-change.json"
    payload = {"groups": groups, **extra}
    journal_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return repo, journal_path, spec


QUARANTINED = {
    "state": "QUARANTINED",
    "quarantine_reason": QUARANTINE_BUDGET_EXHAUSTED,
    "quarantine_detail": "ran out of budget",
    "pr_url": "https://example.test/pr/1",
    "head_branch": "run/g1",
}
MERGED = {
    "state": "MERGED",
    "pr_url": "https://example.test/pr/2",
    "head_branch": "run/g2",
}
OPEN = {"state": "OPEN", "pr_url": "https://example.test/pr/3", "head_branch": "run/g3"}


def test_clears_quarantined_group_leaving_others_byte_identical(tmp_path, capsys):
    repo, journal_path, spec = _write_journal(
        tmp_path,
        {"g1": dict(QUARANTINED), "g2": dict(MERGED), "g3": dict(OPEN)},
        integrate_complete=True,
    )
    rc = resume_group.main(["--repo", str(repo), "--spec", spec, "--group", "g1"])
    assert rc == 0
    after = json.loads(journal_path.read_text())
    assert "g1" not in after["groups"]
    assert after["groups"]["g2"] == MERGED
    assert after["groups"]["g3"] == OPEN
    assert "integrate_complete" not in after
    out = capsys.readouterr().out
    assert "g1" in out
    assert f"worktrail-live full-real --repo {repo} --spec {spec} --resume" in out


def test_non_quarantined_named_group_is_a_problem(tmp_path):
    repo, journal_path, spec = _write_journal(tmp_path, {"g2": dict(MERGED)})
    before = journal_path.read_text()
    rc = resume_group.main(["--repo", str(repo), "--spec", spec, "--group", "g2"])
    assert rc == 1
    assert journal_path.read_text() == before


def test_absent_group_is_a_problem(tmp_path):
    repo, journal_path, spec = _write_journal(tmp_path, {"g1": dict(QUARANTINED)})
    before = journal_path.read_text()
    rc = resume_group.main(["--repo", str(repo), "--spec", spec, "--group", "nope"])
    assert rc == 1
    assert journal_path.read_text() == before


def test_missing_journal_exits_non_zero(tmp_path):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    rc = resume_group.main(
        ["--repo", str(repo), "--spec", "openspec/changes/gone", "--group", "g1"]
    )
    assert rc == 1


def test_unparseable_journal_exits_non_zero_without_writing(tmp_path):
    repo, journal_path, spec = _write_journal(tmp_path, {})
    journal_path.write_text("{not json")
    rc = resume_group.main(["--repo", str(repo), "--spec", spec, "--group", "g1"])
    assert rc == 1
    assert journal_path.read_text() == "{not json"


def test_all_resumable_selects_only_budget_exhausted(tmp_path):
    other = {
        "state": "QUARANTINED",
        "quarantine_reason": "merge_conflict",
        "pr_url": "",
        "head_branch": "run/g4",
    }
    repo, journal_path, spec = _write_journal(
        tmp_path, {"g1": dict(QUARANTINED), "g4": dict(other), "g2": dict(MERGED)}
    )
    rc = resume_group.main(["--repo", str(repo), "--spec", spec, "--all-resumable"])
    assert rc == 0
    after = json.loads(journal_path.read_text())
    assert "g1" not in after["groups"]
    assert after["groups"]["g4"] == other
    assert [e["group"] for e in after["resumed_quarantines"]] == ["g1"]


def test_all_resumable_reports_nothing_to_clear(tmp_path, capsys):
    repo, journal_path, spec = _write_journal(tmp_path, {"g2": dict(MERGED)})
    before = journal_path.read_text()
    rc = resume_group.main(["--repo", str(repo), "--spec", spec, "--all-resumable"])
    assert rc == 0
    assert "nothing to clear" in capsys.readouterr().out
    assert journal_path.read_text() == before


def test_resumed_quarantines_carries_prior_state_and_appends(tmp_path):
    repo, journal_path, spec = _write_journal(
        tmp_path, {"g1": dict(QUARANTINED), "g5": dict(QUARANTINED)}
    )
    assert (
        resume_group.main(["--repo", str(repo), "--spec", spec, "--group", "g1"]) == 0
    )
    entry = json.loads(journal_path.read_text())["resumed_quarantines"][0]
    assert entry["group"] == "g1"
    assert entry["quarantine_reason"] == QUARANTINE_BUDGET_EXHAUSTED
    assert entry["quarantine_detail"] == "ran out of budget"
    assert entry["pr_url"] == "https://example.test/pr/1"
    assert entry["cleared_at"]

    assert (
        resume_group.main(["--repo", str(repo), "--spec", spec, "--group", "g5"]) == 0
    )
    history = json.loads(journal_path.read_text())["resumed_quarantines"]
    assert [e["group"] for e in history] == ["g1", "g5"]


def test_held_runlock_refuses_to_edit(tmp_path, monkeypatch):
    repo, journal_path, spec = _write_journal(tmp_path, {"g1": dict(QUARANTINED)})
    before = journal_path.read_text()
    monkeypatch.setattr(resume_group, "_runlock_held", lambda _path: True)
    rc = resume_group.main(["--repo", str(repo), "--spec", spec, "--group", "g1"])
    assert rc == 1
    assert journal_path.read_text() == before


def test_dry_run_prints_selection_without_writing(tmp_path, capsys):
    repo, journal_path, spec = _write_journal(tmp_path, {"g1": dict(QUARANTINED)})
    before = journal_path.read_text()
    rc = resume_group.main(
        ["--repo", str(repo), "--spec", spec, "--group", "g1", "--dry-run"]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "g1" in out
    assert journal_path.read_text() == before


@pytest.mark.parametrize("all_resumable", [False, True])
def test_select_groups_is_pure(all_resumable):
    journal = {"groups": {"g1": dict(QUARANTINED)}}
    selected, problems = resume_group.select_groups(
        journal, [] if all_resumable else ["g1"], all_resumable
    )
    assert selected == ["g1"]
    assert problems == []
    assert journal["groups"]["g1"] == QUARANTINED
