"""Unit tests for router/reconcile_pr_labels.py — no live `gh` calls; subprocess
is faked. Mirrors tests/router/test_pr_labels.py conventions."""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router import reconcile_pr_labels as rpl


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_run_record(path: Path, **fields) -> None:
    defaults = {
        "run_id": "test-run",
        "repository": "/repo",
        "pull_request": "https://github.com/o/r/pull/1",
        "risk_level": "low",
        "final_status": "completed_pr_open",
    }
    defaults.update(fields)
    lines = []
    for key, value in defaults.items():
        if isinstance(value, list):
            lines.append(f"{key}:")
            lines.extend(f"  - {item}" for item in value)
        else:
            lines.append(f"{key}: {value if value is not None else 'null'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


_NO_POLICY = {
    "automerge": {"enabled": True, "max_risk": "medium", "target_branches": []},
    "protected_paths": [],
    "require_human_routes": [],
    "_meta": {"external_automerge": {"detected": False}},
}


# ---------------------------------------------------------------------------
# discover_managed_repos


def test_discover_managed_repos_requires_go_policy_yaml(tmp_path):
    managed = tmp_path / "managed"
    (managed / ".worktrail").mkdir(parents=True)
    (managed / ".worktrail" / "policy.yaml").write_text("base_branch: main\n")
    (managed / ".git").mkdir()

    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / ".git").mkdir()

    assert rpl.discover_managed_repos(tmp_path) == ["managed"]


# ---------------------------------------------------------------------------
# load_run_index


def test_load_run_index_maps_pr_url_to_risk_gates_route(tmp_path):
    repo_dir = tmp_path / "worktrail"
    repo_dir.mkdir()
    _write_run_record(
        repo_dir / "run1.yaml",
        pull_request="https://github.com/o/r/pull/1",
        risk_level="high",
        gates=["never_automerge"],
        selected_route="F",
    )
    _write_run_record(
        repo_dir / "run2.yaml",
        pull_request="https://github.com/o/r/pull/2",
        risk_level="medium",
    )

    index = rpl.load_run_index(tmp_path)
    assert index == {
        "https://github.com/o/r/pull/1": {
            "risk_level": "high",
            "gates": ["never_automerge"],
            "route": "F",
        },
        "https://github.com/o/r/pull/2": {
            "risk_level": "medium",
            "gates": None,
            "route": None,
        },
    }


def test_load_run_index_gates_none_when_field_absent(tmp_path):
    """A run record started before `run_record.py start` gained `--gates` has
    no `gates` key at all -- this must map to `None`, never `[]` (an absent
    field is not evidence of zero gates)."""
    repo_dir = tmp_path / "worktrail"
    repo_dir.mkdir()
    _write_run_record(
        repo_dir / "legacy.yaml",
        pull_request="https://github.com/o/r/pull/1",
        risk_level="low",
    )

    index = rpl.load_run_index(tmp_path)
    assert index["https://github.com/o/r/pull/1"]["gates"] is None


def test_load_run_index_gates_empty_list_when_persisted_empty(tmp_path):
    repo_dir = tmp_path / "worktrail"
    repo_dir.mkdir()
    _write_run_record(
        repo_dir / "run1.yaml",
        pull_request="https://github.com/o/r/pull/1",
        risk_level="low",
        gates=[],
    )

    index = rpl.load_run_index(tmp_path)
    assert index["https://github.com/o/r/pull/1"]["gates"] == []


def test_load_run_index_skips_records_missing_pr_or_risk(tmp_path):
    repo_dir = tmp_path / "worktrail"
    repo_dir.mkdir()
    _write_run_record(repo_dir / "no_pr.yaml", pull_request=None, risk_level="high")
    _write_run_record(
        repo_dir / "no_risk.yaml",
        pull_request="https://github.com/o/r/pull/9",
        risk_level=None,
    )

    assert rpl.load_run_index(tmp_path) == {}


def test_load_run_index_empty_when_dir_missing(tmp_path):
    assert rpl.load_run_index(tmp_path / "does-not-exist") == {}


def test_load_run_index_handles_list_form_pull_request(tmp_path):
    """A run that produces more than one PR (parallel-orchestrator group-PR
    path, or any run appended to more than once) records `pull_request` as a
    list, not a scalar -- observed in production run records with an
    empty-string placeholder entry alongside a real URL."""
    repo_dir = tmp_path / "datalena"
    repo_dir.mkdir()
    (repo_dir / "run1.yaml").write_text(
        "risk_level: high\n"
        "pull_request:\n"
        '  - ""\n'
        '  - "https://github.com/o/r/pull/1"\n',
        encoding="utf-8",
    )

    index = rpl.load_run_index(tmp_path)
    assert index == {
        "https://github.com/o/r/pull/1": {
            "risk_level": "high",
            "gates": None,
            "route": None,
        },
    }


def test_load_run_index_handles_repo_labeled_list_entries(tmp_path):
    """A multi-repo group-PR run's list entries are `"<repo>: <url>"`, not a
    bare URL -- extract the URL substring rather than matching verbatim."""
    repo_dir = tmp_path / "datalena"
    repo_dir.mkdir()
    (repo_dir / "run1.yaml").write_text(
        "risk_level: low\n"
        "pull_request:\n"
        "  - datalena: https://github.com/o/datalena/pull/1\n"
        "  - ggb: https://github.com/o/ggb/pull/2\n",
        encoding="utf-8",
    )

    index = rpl.load_run_index(tmp_path)
    assert index == {
        "https://github.com/o/datalena/pull/1": {
            "risk_level": "low",
            "gates": None,
            "route": None,
        },
        "https://github.com/o/ggb/pull/2": {
            "risk_level": "low",
            "gates": None,
            "route": None,
        },
    }


# ---------------------------------------------------------------------------
# reconcile_repo — go:risk-* correction (pre-existing behavior)


def test_reconcile_repo_applies_risk_label_from_matching_run_record(
    monkeypatch, tmp_path
):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        if cmd[:2] == ["gh", "api"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "low",
                "gates": None,
                "route": None,
            }
        },
        dry_run=False,
    )
    assert result["applied"] == [
        {"pr": "https://github.com/o/r/pull/1", "label": "go:risk-low"}
    ]
    assert result["unreconciled"] == []
    assert result["checked"] == 1


def test_reconcile_repo_skips_prs_already_fully_labeled(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [{"name": "go:risk-high"}],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        raise AssertionError(f"must not correct an already-labeled PR: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "low",
                "gates": None,
                "route": None,
            }
        },
        dry_run=False,
    )
    assert result == {
        "repo": "worktrail",
        "path": str(tmp_path / "worktrail"),
        "applied": [],
        "unreconciled": [],
        "checked": 0,
    }


def test_reconcile_repo_reports_unreconciled_when_no_matching_run_record(
    monkeypatch, tmp_path
):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/99",
                            "labels": [],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        raise AssertionError(
            f"must not call gh again with no matching run record: {cmd}"
        )

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(tmp_path / "worktrail", {}, dry_run=False)
    assert result["applied"] == []
    assert result["unreconciled"] == ["https://github.com/o/r/pull/99"]
    assert result["checked"] == 1


def test_reconcile_repo_dry_run_never_edits(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        raise AssertionError(f"dry-run must not call gh pr view/edit: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "medium",
                "gates": None,
                "route": None,
            }
        },
        dry_run=True,
    )
    assert result["applied"] == [
        {
            "pr": "https://github.com/o/r/pull/1",
            "label": "go:risk-medium",
            "dry_run": True,
        },
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
# reconcile_repo — go:no-automerge correction (new)


def test_reconcile_repo_adds_no_automerge_when_ineligible(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [{"name": "go:risk-high"}],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": [{"name": "go:risk-high"}]}))
        if cmd[:2] == ["gh", "api"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)
    monkeypatch.setattr(rpl, "load_policy", lambda repo: _NO_POLICY)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "high",
                "gates": ["never_automerge"],
                "route": "F",
            }
        },
        dry_run=False,
    )
    assert result["applied"] == [
        {"pr": "https://github.com/o/r/pull/1", "label": "go:no-automerge"},
    ]
    assert result["unreconciled"] == []
    assert result["checked"] == 1


def test_reconcile_repo_applies_both_labels_when_both_missing(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        if cmd[:2] == ["gh", "api"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)
    monkeypatch.setattr(rpl, "load_policy", lambda repo: _NO_POLICY)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "high",
                "gates": ["never_automerge"],
                "route": "F",
            }
        },
        dry_run=False,
    )
    assert result["applied"] == [
        {"pr": "https://github.com/o/r/pull/1", "label": "go:risk-high"},
        {"pr": "https://github.com/o/r/pull/1", "label": "go:no-automerge"},
    ]


def test_reconcile_repo_skips_no_automerge_recompute_when_gates_absent(
    monkeypatch, tmp_path
):
    """A run record that predates `--gates` (gates=None) must never drive a
    go:no-automerge correction -- there is no way to know what the true
    gates were, and guessing "no gates" could wrongly mark an ineligible PR
    eligible."""

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [{"name": "go:risk-high"}],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        raise AssertionError(
            f"must not touch an already-risk-labeled PR with no gates data: {cmd}"
        )

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    def fail_load_policy(repo):
        raise AssertionError("must not load policy when gates is None")

    monkeypatch.setattr(rpl, "load_policy", fail_load_policy)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "high",
                "gates": None,
                "route": "F",
            }
        },
        dry_run=False,
    )
    assert result == {
        "repo": "worktrail",
        "path": str(tmp_path / "worktrail"),
        "applied": [],
        "unreconciled": [],
        "checked": 0,
    }


def test_reconcile_repo_never_removes_existing_no_automerge(monkeypatch, tmp_path):
    """A PR already carrying go:no-automerge is left untouched, even if it
    would recompute as eligible -- this corrector is additive-only."""

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [
                                {"name": "go:risk-low"},
                                {"name": "go:no-automerge"},
                            ],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        raise AssertionError(f"must not call gh again: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)
    monkeypatch.setattr(rpl, "load_policy", lambda repo: _NO_POLICY)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "low",
                "gates": [],
                "route": "F",
            }
        },
        dry_run=False,
    )
    assert result == {
        "repo": "worktrail",
        "path": str(tmp_path / "worktrail"),
        "applied": [],
        "unreconciled": [],
        "checked": 0,
    }


def test_reconcile_repo_no_automerge_dry_run_never_edits(monkeypatch, tmp_path):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [{"name": "go:risk-high"}],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        raise AssertionError(f"dry-run must not call gh pr view/edit: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)
    monkeypatch.setattr(rpl, "load_policy", lambda repo: _NO_POLICY)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "high",
                "gates": ["never_automerge"],
                "route": "F",
            }
        },
        dry_run=True,
    )
    assert result["applied"] == [
        {
            "pr": "https://github.com/o/r/pull/1",
            "label": "go:no-automerge",
            "dry_run": True,
        },
    ]


def test_reconcile_repo_no_op_when_eligible_and_no_automerge_absent(
    monkeypatch, tmp_path
):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [{"name": "go:risk-low"}],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        raise AssertionError(
            f"eligible PR with risk label already present must not call gh again: {cmd}"
        )

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)
    monkeypatch.setattr(rpl, "load_policy", lambda repo: _NO_POLICY)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "low",
                "gates": [],
                "route": "F",
            }
        },
        dry_run=False,
    )
    assert result == {
        "repo": "worktrail",
        "path": str(tmp_path / "worktrail"),
        "applied": [],
        "unreconciled": [],
        "checked": 0,
    }


def test_reconcile_repo_reports_unreconciled_when_no_automerge_edit_fails(
    monkeypatch, tmp_path
):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    [
                        {
                            "url": "https://github.com/o/r/pull/1",
                            "labels": [{"name": "go:risk-high"}],
                            "baseRefName": "main",
                        },
                    ]
                ),
            )
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": [{"name": "go:risk-high"}]}))
        if cmd[:2] == ["gh", "api"]:
            return _FakeCompleted(1, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)
    monkeypatch.setattr(rpl, "load_policy", lambda repo: _NO_POLICY)

    result = rpl.reconcile_repo(
        tmp_path / "worktrail",
        {
            "https://github.com/o/r/pull/1": {
                "risk_level": "high",
                "gates": ["never_automerge"],
                "route": "F",
            }
        },
        dry_run=False,
    )
    assert result["applied"] == []
    assert result["unreconciled"] == ["https://github.com/o/r/pull/1"]


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
    monkeypatch.setattr(rpl, "load_run_index", lambda runs_dir: {})

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "list"]:
            return _FakeCompleted(0, json.dumps([]))
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(rpl.subprocess, "run", fake_run)

    rc = rpl.main(["--repo", str(tmp_path / "worktrail"), "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "results": [
            {
                "repo": "worktrail",
                "path": str(tmp_path / "worktrail"),
                "applied": [],
                "unreconciled": [],
                "checked": 0,
            }
        ],
        "applied": 0,
        "unreconciled": 0,
    }


def test_main_repos_root_only_sweeps_managed_repos(monkeypatch, tmp_path, capsys):
    managed = tmp_path / "managed"
    (managed / ".worktrail").mkdir(parents=True)
    (managed / ".worktrail" / "policy.yaml").write_text("base_branch: main\n")
    (managed / ".git").mkdir()

    unmanaged = tmp_path / "unmanaged"
    unmanaged.mkdir()
    (unmanaged / ".git").mkdir()

    monkeypatch.setattr(rpl, "load_run_index", lambda runs_dir: {})
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
