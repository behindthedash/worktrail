"""Unit tests for router/reconcile_pr_labels.py — no live `gh` calls; subprocess
is faked. Mirrors tests/router/test_pr_labels.py conventions."""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router import reconcile_pr_labels as rpl


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def _write_run_record(path: Path, **fields) -> None:
    defaults = {
        "run_id": "test-run",
        "repository": "/repo",
        "pull_request": "https://github.com/o/r/pull/1",
        "risk_level": "low",
        "final_status": "completed_pr_open",
    }
    defaults.update(fields)
    lines = [f"{key}: {value if value is not None else 'null'}"
             for key, value in defaults.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# discover_managed_repos

def test_discover_managed_repos_requires_go_policy_yaml(tmp_path):
    managed = tmp_path / "managed"
    (managed / "docs" / "specs").mkdir(parents=True)
    (managed / "docs" / "specs" / "go-policy.yaml").write_text("base_branch: main\n")
    (managed / ".git").mkdir()

    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / ".git").mkdir()

    assert rpl.discover_managed_repos(tmp_path) == ["managed"]


# ---------------------------------------------------------------------------
# load_risk_index

def test_load_risk_index_maps_pr_url_to_risk_level(tmp_path):
    repo_dir = tmp_path / "worktrail"
    repo_dir.mkdir()
    _write_run_record(repo_dir / "run1.yaml",
                       pull_request="https://github.com/o/r/pull/1", risk_level="high")
    _write_run_record(repo_dir / "run2.yaml",
                       pull_request="https://github.com/o/r/pull/2", risk_level="medium")

    index = rpl.load_risk_index(tmp_path)
    assert index == {
        "https://github.com/o/r/pull/1": "high",
        "https://github.com/o/r/pull/2": "medium",
    }


def test_load_risk_index_skips_records_missing_pr_or_risk(tmp_path):
    repo_dir = tmp_path / "worktrail"
    repo_dir.mkdir()
    _write_run_record(repo_dir / "no_pr.yaml", pull_request=None, risk_level="high")
    _write_run_record(repo_dir / "no_risk.yaml",
                       pull_request="https://github.com/o/r/pull/9", risk_level=None)

    assert rpl.load_risk_index(tmp_path) == {}


def test_load_risk_index_empty_when_dir_missing(tmp_path):
    assert rpl.load_risk_index(tmp_path / "does-not-exist") == {}


# ---------------------------------------------------------------------------
# reconcile_repo

def test_reconcile_repo_applies_label_from_matching_run_record(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps([
                {"url": "https://github.com/o/r/pull/1", "labels": []},
            ]))
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        if cmd[:3] == ["gh", "pr", "edit"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {"https://github.com/o/r/pull/1": "low"},
        dry_run=False,
    )
    assert result["applied"] == [{"pr": "https://github.com/o/r/pull/1", "label": "go:risk-low"}]
    assert result["unreconciled"] == []
    assert result["checked"] == 1


def test_reconcile_repo_skips_prs_already_labeled(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps([
                {"url": "https://github.com/o/r/pull/1",
                 "labels": [{"name": "go:risk-high"}]},
            ]))
        raise AssertionError(f"must not correct an already-labeled PR: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail", {"https://github.com/o/r/pull/1": "low"}, dry_run=False)
    assert result == {
        "repo": "worktrail", "path": str(tmp_path / "worktrail"),
        "applied": [], "unreconciled": [], "checked": 0,
    }


def test_reconcile_repo_reports_unreconciled_when_no_matching_run_record(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps([
                {"url": "https://github.com/o/r/pull/99", "labels": []},
            ]))
        raise AssertionError(f"must not call gh again with no risk level: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(tmp_path / "worktrail", {}, dry_run=False)
    assert result["applied"] == []
    assert result["unreconciled"] == ["https://github.com/o/r/pull/99"]
    assert result["checked"] == 1


def test_reconcile_repo_dry_run_never_edits(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps([
                {"url": "https://github.com/o/r/pull/1", "labels": []},
            ]))
        raise AssertionError(f"dry-run must not call gh pr view/edit: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {"https://github.com/o/r/pull/1": "medium"},
        dry_run=True,
    )
    assert result["applied"] == [
        {"pr": "https://github.com/o/r/pull/1", "label": "go:risk-medium", "dry_run": True},
    ]


def test_reconcile_repo_reports_error_when_gh_pr_list_fails(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        return _FakeCompleted(1, "")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(tmp_path / "worktrail", {}, dry_run=False)
    assert result["error"] == "gh pr list failed or unavailable"
    assert result["applied"] == []
    assert result["unreconciled"] == []


# ---------------------------------------------------------------------------
# main

def test_main_requires_repo_or_repos_root():
    out = StringIO()
    with patch("sys.stderr", out):
        try:
            rpl.main([])
        except SystemExit as exc:
            assert exc.code != 0
        else:
            raise AssertionError("expected SystemExit")


def test_main_single_repo_json_output(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(rpl, "load_risk_index", lambda runs_dir: {})

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps([]))
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    rc = rpl.main(["--repo", str(tmp_path / "worktrail"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"results": [{
        "repo": "worktrail", "path": str(tmp_path / "worktrail"),
        "applied": [], "unreconciled": [], "checked": 0,
    }], "applied": 0, "unreconciled": 0}


def test_main_repos_root_only_sweeps_managed_repos(monkeypatch, tmp_path, capsys):
    managed = tmp_path / "managed"
    (managed / "docs" / "specs").mkdir(parents=True)
    (managed / "docs" / "specs" / "go-policy.yaml").write_text("base_branch: main\n")
    (managed / ".git").mkdir()

    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / ".git").mkdir()

    monkeypatch.setattr(rpl, "load_risk_index", lambda runs_dir: {})
    calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        calls.append(cwd)
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps([]))
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    rc = rpl.main(["--repos-root", str(tmp_path), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert [r["repo"] for r in payload["results"]] == ["managed"]
    assert calls == [str(managed)]
