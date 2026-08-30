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
from worktrail.router.pr_labels import (
    ensure_pr_no_automerge_label,
    ensure_pr_risk_label,
    main,
)


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_ensure_pr_risk_label_adds_when_none_present(monkeypatch):
    calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        if cmd[:2] == ["gh", "api"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result == "go:risk-low"
    assert [
        "gh",
        "pr",
        "view",
        "https://github.com/o/r/pull/1",
        "--json",
        "labels",
    ] in calls
    assert [
        "gh",
        "api",
        "repos/o/r/issues/1/labels",
        "-X",
        "POST",
        "-f",
        "labels[]=go:risk-low",
    ] in calls


def test_ensure_pr_risk_label_noop_when_risk_label_already_present(monkeypatch):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(
                0,
                json.dumps(
                    {"labels": [{"name": "go:risk-high"}, {"name": "go:no-automerge"}]}
                ),
            )
        raise AssertionError(f"gh api add-label must not run: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result is None


def test_ensure_pr_risk_label_never_touches_no_automerge(monkeypatch):
    """A go:no-automerge label an agent legitimately added must survive
    untouched -- this corrector only ADDS a missing risk label, it never
    inspects or removes go:no-automerge."""
    add_calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(
                0, json.dumps({"labels": [{"name": "go:no-automerge"}]})
            )
        if cmd[:2] == ["gh", "api"]:
            add_calls.append(cmd)
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "high")
    assert result == "go:risk-high"
    assert add_calls == [
        [
            "gh",
            "api",
            "repos/o/r/issues/1/labels",
            "-X",
            "POST",
            "-f",
            "labels[]=go:risk-high",
        ]
    ]
    for call in add_calls:
        assert "go:no-automerge" not in call


@pytest.mark.parametrize(
    "repo,pr_url,risk",
    [
        (None, "https://github.com/o/r/pull/1", "low"),
        ("/repo", None, "low"),
        ("/repo", "https://github.com/o/r/pull/1", None),
    ],
)
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


def test_ensure_pr_risk_label_returns_none_when_gh_api_add_fails(monkeypatch, capsys):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        return _FakeCompleted(
            1, "", stderr="GraphQL: Projects (classic) is being deprecated"
        )

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "medium")
    assert result is None
    assert "failed to add label" in capsys.readouterr().err


def test_ensure_pr_risk_label_retries_on_transient_tls_then_succeeds(
    monkeypatch, capsys
):
    """A transient TLS certificate failure (~1-in-8 connections to the GitHub
    API edge) must be retried with backoff, not treated as a terminal error --
    verified live 2026-08-11 where the go:risk-* label add flaked once."""
    gh_api_calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        gh_api_calls.append(cmd)
        # fail once with the Go x509 name-mismatch text, succeed on retry
        if len(gh_api_calls) == 1:
            return _FakeCompleted(
                1,
                "",
                stderr="tls: failed to verify certificate: "
                "x509: certificate is not valid for any names, "
                "but wanted to match api.github.com",
            )
        return _FakeCompleted(0, "")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result == "go:risk-low"
    assert len(gh_api_calls) == 2
    assert "transient TLS failure" in capsys.readouterr().err


def test_ensure_pr_risk_label_gives_up_after_retry_ceiling(monkeypatch, capsys):
    """Persistent transient TLS failures must not loop forever -- bounded at
    the retry ceiling, then the standard fail-open warning is emitted."""
    gh_api_calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        gh_api_calls.append(cmd)
        return _FakeCompleted(1, "", stderr="failed to verify certificate")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result is None
    assert len(gh_api_calls) == pr_labels._RETRY_ATTEMPTS
    assert "failed to add label" in capsys.readouterr().err


def test_ensure_pr_risk_label_non_transient_failure_not_retried(monkeypatch):
    """A real (non-TLS) failure must NOT be retried -- retrying would mask an
    actual error like GraphQL drift or an auth problem."""
    gh_api_calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        gh_api_calls.append(cmd)
        return _FakeCompleted(1, "", stderr="HTTP 422: invalid payload")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result is None
    assert len(gh_api_calls) == 1


def test_ensure_pr_risk_label_noop_when_gh_view_transient_tls_then_succeeds(
    monkeypatch, capsys
):
    """The read path (gh pr view) must also ride the retry, so a transient TLS
    flake on the initial label read doesn't abort the correction."""
    view_calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            view_calls.append(cmd)
            if len(view_calls) == 1:
                return _FakeCompleted(
                    1, "", stderr="x509: certificate is not valid for any names"
                )
            return _FakeCompleted(0, json.dumps({"labels": []}))
        return _FakeCompleted(0, "")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/pull/1", "low")
    assert result == "go:risk-low"
    assert len(view_calls) == 2


def test_ensure_pr_risk_label_returns_none_and_warns_when_owner_repo_unresolvable(
    monkeypatch, capsys
):
    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        if cmd == ["git", "remote", "get-url", "origin"]:
            return _FakeCompleted(1, "")  # no origin remote to fall back to
        raise AssertionError(
            f"gh api must not run when owner/repo is unresolvable: {cmd}"
        )

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    # not a full PR URL and not a bare PR number either -- nothing to resolve
    result = ensure_pr_risk_label("/repo", "https://github.com/o/r/issues/1", "medium")
    assert result is None
    assert "could not resolve" in capsys.readouterr().err


def test_ensure_pr_no_automerge_label_resolves_owner_repo_from_git_for_bare_pr_number(
    monkeypatch,
):
    """check_review_threads.py passes a bare PR number (gh pr edit resolves
    the repo from cwd) -- the REST endpoint needs owner/repo explicitly, so
    this must fall back to the local git remote instead of failing to parse."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        if cmd == ["git", "remote", "get-url", "origin"]:
            return _FakeCompleted(0, "https://github.com/acme/widgets.git\n")
        if cmd[:2] == ["gh", "api"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_no_automerge_label("/repo", "42", eligible=False)
    assert result == "go:no-automerge"
    assert [
        "gh",
        "api",
        "repos/acme/widgets/issues/42/labels",
        "-X",
        "POST",
        "-f",
        "labels[]=go:no-automerge",
    ] in calls


# ---------------------------------------------------------------------------
# ensure_pr_no_automerge_label


def test_ensure_pr_no_automerge_label_adds_when_ineligible_and_none_present(
    monkeypatch,
):
    calls = []

    def fake_run(cmd, capture_output, text, cwd, timeout):
        calls.append(cmd)
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": [{"name": "go:risk-high"}]}))
        if cmd[:2] == ["gh", "api"]:
            return _FakeCompleted(0, "")
        raise AssertionError(f"unexpected command: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_no_automerge_label(
        "/repo", "https://github.com/o/r/pull/1", eligible=False
    )
    assert result == "go:no-automerge"
    assert [
        "gh",
        "api",
        "repos/o/r/issues/1/labels",
        "-X",
        "POST",
        "-f",
        "labels[]=go:no-automerge",
    ] in calls


def test_ensure_pr_no_automerge_label_noop_when_eligible(monkeypatch):
    def fake_run(*a, **k):
        raise AssertionError("gh must not be called when the PR is eligible")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_no_automerge_label(
        "/repo", "https://github.com/o/r/pull/1", eligible=True
    )
    assert result is None


def test_ensure_pr_no_automerge_label_noop_when_already_present(monkeypatch):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(
                0, json.dumps({"labels": [{"name": "go:no-automerge"}]})
            )
        raise AssertionError(f"gh api add-label must not run: {cmd}")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_no_automerge_label(
        "/repo", "https://github.com/o/r/pull/1", eligible=False
    )
    assert result is None


def test_ensure_pr_no_automerge_label_never_removes_existing_label(monkeypatch):
    """This corrector is additive only -- an existing go:no-automerge a human
    or agent added deliberately must never be removed, even if it later
    becomes eligible (eligible=True is a noop, never a removal)."""

    def fake_run(*a, **k):
        raise AssertionError("gh must not be called; eligible=True is always a noop")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_no_automerge_label(
        "/repo", "https://github.com/o/r/pull/1", eligible=True
    )
    assert result is None


@pytest.mark.parametrize(
    "repo,pr_url,eligible",
    [
        (None, "https://github.com/o/r/pull/1", False),
        ("/repo", None, False),
    ],
)
def test_ensure_pr_no_automerge_label_noop_on_missing_inputs(
    monkeypatch, repo, pr_url, eligible
):
    def fake_run(*a, **k):
        raise AssertionError("gh must not be called with incomplete inputs")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    assert ensure_pr_no_automerge_label(repo, pr_url, eligible) is None


def test_ensure_pr_no_automerge_label_noop_when_gh_view_fails(monkeypatch):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        return _FakeCompleted(1, "")

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_no_automerge_label(
        "/repo", "https://github.com/o/r/pull/1", eligible=False
    )
    assert result is None


def test_ensure_pr_no_automerge_label_returns_none_when_gh_api_add_fails(
    monkeypatch, capsys
):
    def fake_run(cmd, capture_output, text, cwd, timeout):
        if cmd[:3] == ["gh", "pr", "view"]:
            return _FakeCompleted(0, json.dumps({"labels": []}))
        return _FakeCompleted(
            1, "", stderr="GraphQL: Projects (classic) is being deprecated"
        )

    monkeypatch.setattr(pr_labels.subprocess, "run", fake_run)
    result = ensure_pr_no_automerge_label(
        "/repo", "https://github.com/o/r/pull/1", eligible=False
    )
    assert result is None
    assert "failed to add label" in capsys.readouterr().err


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
    lines = [
        f"{key}: {value if value is not None else 'null'}"
        for key, value in defaults.items()
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_main_applies_correction_from_run_record_fields(tmp_path, monkeypatch):
    run_path = tmp_path / "run.yaml"
    _write_run_record(run_path)
    seen = []
    monkeypatch.setattr(
        pr_labels,
        "ensure_pr_risk_label",
        lambda repo, pr, risk: seen.append((repo, pr, risk)) or "go:risk-low",
    )
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
