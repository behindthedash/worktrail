#!/usr/bin/env python3
"""Unit tests for CI's structural half of the Route C scope-check gate."""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest

from worktrail.conductor import compile as conductor_compile
from worktrail.conductor import runplan
from worktrail.router import check_compile_markers as ccm

TASKS_MD = textwrap.dedent(
    """\
    ## 1. Core

    - [ ] 1.1 Add the parser
    """
)


@pytest.fixture()
def change(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    d = repo / "openspec" / "changes" / "add-parser"
    d.mkdir(parents=True)
    (repo / ".git").mkdir()
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(TASKS_MD)
    return d


def _fp(change_dir: Path) -> str:
    from worktrail.taskformats import resolve

    _, tasks = resolve.load_spec(str(change_dir))
    return runplan.fingerprint(change_dir, tasks)


# --------------------------------------------------------------------------- #
# check_marker()
# --------------------------------------------------------------------------- #
def test_missing_marker_is_reported_as_missing(change):
    result = ccm.check_marker(change)
    assert result["status"] == "missing"
    assert result["marker_fingerprint"] is None
    assert result["expected_fingerprint"] == _fp(change)


def test_fresh_marker_is_reported_as_ok(change):
    conductor_compile.write_marker(change, _fp(change))
    result = ccm.check_marker(change)
    assert result["status"] == "ok"
    assert result["marker_fingerprint"] == result["expected_fingerprint"]


def test_stale_marker_is_reported_as_stale(change):
    conductor_compile.write_marker(change, "a-fingerprint-from-before-tasks-md-was-edited")
    result = ccm.check_marker(change)
    assert result["status"] == "stale"
    assert result["marker_fingerprint"] != result["expected_fingerprint"]


def test_the_marker_file_itself_does_not_change_the_fingerprint(change):
    """Regression: `fingerprint()` used to hash every file in the change
    directory including the marker itself, so the fingerprint it just
    recorded would disagree with itself the moment it existed on disk."""
    fp_before = _fp(change)
    conductor_compile.write_marker(change, fp_before)
    assert _fp(change) == fp_before


# --------------------------------------------------------------------------- #
# changed_change_dirs() -- real git repo, matches how CI actually invokes this
# --------------------------------------------------------------------------- #
def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture()
def repo_with_a_change_on_a_branch(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", "feature")
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(TASKS_MD)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "add change")
    return repo


def test_changed_change_dirs_finds_a_touched_change(repo_with_a_change_on_a_branch):
    dirs = ccm.changed_change_dirs(repo_with_a_change_on_a_branch, "main", "feature")
    assert [d.relative_to(repo_with_a_change_on_a_branch) for d in dirs] == [
        Path("openspec/changes/add-thing")
    ]


def test_changed_change_dirs_is_empty_when_nothing_touched(repo_with_a_change_on_a_branch):
    assert ccm.changed_change_dirs(repo_with_a_change_on_a_branch, "main", "main") == []


def test_changed_change_dirs_degrades_to_empty_on_an_unresolvable_ref(tmp_path):
    repo = tmp_path / "not-a-repo"
    repo.mkdir()
    assert ccm.changed_change_dirs(repo, "main", "HEAD") == []


@pytest.fixture()
def repo_with_an_archived_change_on_a_branch(tmp_path: Path) -> Path:
    """Reproduces `openspec archive`'s layout: the change directory moves one
    level deeper, under `openspec/changes/archive/<name>/`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")

    _git(repo, "checkout", "-q", "-b", "feature")
    d = repo / "openspec" / "changes" / "archive" / "2026-08-08-add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text(TASKS_MD)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "archive change")
    return repo


def test_changed_change_dirs_skips_archived_changes(repo_with_an_archived_change_on_a_branch):
    """Regression: `openspec archive` moves a change to
    `openspec/changes/archive/<name>/`, one level deeper than a live change.
    `resolve._split()` assumes the fixed `openspec/changes/<id>` depth, so
    treating an archived change's `tasks.md` as still-live crashed CI with
    `FileNotFoundError` at a doubled `openspec/openspec/changes/...` path
    (worktrail PR #206). An archived change's `tasks.md` is historical, not a
    still-live plan subject to fresh scope verification -- it must never reach
    `check_marker()`."""
    assert ccm.changed_change_dirs(repo_with_an_archived_change_on_a_branch, "main", "feature") == []


# --------------------------------------------------------------------------- #
# check() / main() -- `--change-dir` bypasses git-diff discovery for direct,
# fast unit coverage of the pass/fail contract itself.
# --------------------------------------------------------------------------- #
def test_check_passes_when_every_touched_change_has_a_fresh_marker(change):
    conductor_compile.write_marker(change, _fp(change))
    result = ccm.check(change.parents[2], "main", change_dirs=[change])
    assert result["passed"] is True
    assert result["checked"][0]["status"] == "ok"


def test_check_fails_when_a_touched_change_is_missing_its_marker(change):
    result = ccm.check(change.parents[2], "main", change_dirs=[change])
    assert result["passed"] is False
    assert result["checked"][0]["status"] == "missing"


def test_main_exit_code_matches_pass_fail(change, capsys):
    rc = ccm.main(["--repo", str(change.parents[2]), "--base-ref", "main", "--change-dir", str(change)])
    capsys.readouterr()
    assert rc == 1

    conductor_compile.write_marker(change, _fp(change))
    rc = ccm.main(["--repo", str(change.parents[2]), "--base-ref", "main", "--change-dir", str(change)])
    capsys.readouterr()
    assert rc == 0


def test_main_json_output_is_parseable_and_reports_failures(change, capsys):
    rc = ccm.main(
        ["--repo", str(change.parents[2]), "--base-ref", "main", "--change-dir", str(change), "--json"]
    )
    out, err = capsys.readouterr()
    assert rc == 1
    payload = json.loads(out)
    assert payload["passed"] is False
    assert payload["checked"][0]["status"] == "missing"
    assert "no compile marker found" in err
