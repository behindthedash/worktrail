"""Unit tests for router/pr_labels.py — no live `gh` calls; subprocess is faked.

Moved from tests/drain/test_drain.py (worktrail PR #128 introduced these
against drain.py directly) when ensure_pr_risk_label/_current_pr_labels were
extracted to router/pr_labels.py so poll_run.py and the worktrail-ensure-pr-
label CLI could share the same correction -- see
docs/specs/research/go-dispatch-one-shot-pr-label-gap.md.
"""

import json
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

from worktrail.router import pr_labels
from worktrail.router.pr_labels import ensure_pr_risk_label, main


class _FakeCompleted:
    def __init__(self, returncode=0, stdout=""):
        self.returncode = returncode
        self.stdout = stdout


def test_ensure_pr_risk_label_adds_when_none_present(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        if cmd[:3] == ["gh", "pr", "edit"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result == "go:risk-low"
    assert ["gh", "pr", "view", "https://github.com/o/r/pull/1", "--json", "labels"] in calls
    assert ["gh", "pr", "edit", "https://github.com/o/r/pull/1",
            "--add-label", "go:risk-low"] in calls


def test_ensure_pr_risk_label_noop_when_risk_label_already_present(monkeypatch):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": [{"name": "go:risk-high"},
                                                             {"name": "go:no-automerge"}]}))
        raise AssertionError(f"gh pr edit must not run: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result is None


def test_ensure_pr_risk_label_never_touches_no_automerge(monkeypatch):
    """A go:no-automerge label an agent legitimately added must survive
    untouched -- this corrector only ADDS a missing risk label, it never
    inspects or removes go:no-automerge."""
    edit_calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": [{"name": "go:no-automerge"}]}))
        if cmd[:3] == ["gh", "pr", "edit"]:
            edit_calls.append(cmd)
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "high")
    assert result == "go:risk-high"
    assert edit_calls == [["gh", "pr", "edit", "https://github.com/o/r/pull/1",
                           "--add-label", "go:risk-high"]]
    for call in edit_calls:
        assert "go:no-automerge" not in call


@pytest.mark.parametrize("repo,pr_url,risk", [
    (None, "https://github.com/o/r/pull/1", "low"),
    ("/repo", None, "low"),
    ("/repo", "https://github.com/o/r/pull/1", None),
])
def test_ensure_pr_risk_label_noop_on_missing_inputs(monkeypatch, repo, pr_url, risk):
    def fake_run(*a, **k):
        raise AssertionError("gh must not be called with incomplete inputs")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    assert ensure_pr_risk_label(repo, pr_url, risk) is None


def test_ensure_pr_risk_label_noop_when_gh_view_fails(monkeypatch):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        return _FakeCompleted(1, "")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result is None


def test_ensure_pr_risk_label_returns_none_when_gh_edit_fails(monkeypatch):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        return _FakeCompleted(1, "")  # gh pr edit fails

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "medium")
    assert result is None


# ---------------------------------------------------------------------------
# CLI entrypoint — reads repo/pull_request/risk_level from a run record

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


def test_main_applies_correction_from_run_record_fields(tmp_path, monkeypatch):
    run_path = tmp_path / "run.yaml"
    _write_run_record(run_path)
    seen = []
    monkeypatch.setattr(pr_labels, "ensure_pr_risk_label",
                        lambda repo, pr, risk: seen.append((repo, pr, risk)) or "go:risk-low")
    out = StringIO()
    with patch("sys.stdout", out):
        rc = main(["--run", str(run_path)])
    assert rc == 0
    assert seen == [("/repo", "https://github.com/o/r/pull/1", "low")]
    assert json.loads(out.getvalue()) == {"applied": "go:risk-low"}


def test_main_noop_when_no_pull_request(tmp_path, monkeypatch):
    run_path = tmp_path / "run.yaml"
    _write_run_record(run_path, pull_request=None)

    def unexpected(*_a, **_k):
        raise AssertionError("must not be called when the record has no PR")

    monkeypatch.setattr(pr_labels, "ensure_pr_risk_label", unexpected)
    out = StringIO()
    with patch("sys.stdout", out):
        rc = main(["--run", str(run_path)])
    assert rc == 0
    assert json.loads(out.getvalue()) == {"applied": None}
