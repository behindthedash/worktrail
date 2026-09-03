"""Tests for `worktrail.workqueue.repo_inference` against a fixture repos root."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worktrail.workqueue.repo_inference import infer_repo


@pytest.fixture
def repos_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir()
    for name in ("worktrail", "datalena", "datalena-worktrees"):
        checkout = root / name
        checkout.mkdir()
        subprocess.run(["git", "init", "-q", str(checkout)], check=True)
        (checkout / "README.md").write_text(f"# {name}\n")
    # Non-git directory: never a known repo, regardless of name or mentions.
    (root / "foo-worktrees").mkdir()
    return root


def test_rule_a_token_mid_sentence(repos_root: Path) -> None:
    result = infer_repo("please check Repo: worktrail for details", repos_root)
    assert result.rule == "a"
    assert result.repo == str((repos_root / "worktrail").resolve())


def test_rule_a_token_with_trailing_comma(repos_root: Path) -> None:
    result = infer_repo("Repo: worktrail, needs a fix", repos_root)
    assert result.rule == "a"
    assert result.repo == str((repos_root / "worktrail").resolve())


def test_rule_a_owner_name(repos_root: Path) -> None:
    result = infer_repo("Repo: behindthedash/worktrail", repos_root)
    assert result.rule == "a"
    assert result.repo == str((repos_root / "worktrail").resolve())


def test_rule_b_whole_word(repos_root: Path) -> None:
    result = infer_repo("the worktrail dashboard is stale", repos_root)
    assert result.rule == "b"
    assert result.repo == str((repos_root / "worktrail").resolve())


def test_rule_b_worktrees_suffix_does_not_match_bare_name(repos_root: Path) -> None:
    result = infer_repo("datalena-worktrees has a stale worktree", repos_root)
    assert result.rule == "b"
    assert result.repo == str((repos_root / "datalena-worktrees").resolve())


def test_rule_c_unique_path(repos_root: Path) -> None:
    (repos_root / "worktrail" / "src").mkdir()
    (repos_root / "worktrail" / "src" / "drain.py").write_text("x = 1\n")
    result = infer_repo("check src/drain.py for the bug", repos_root)
    assert result.rule == "c"
    assert result.repo == str((repos_root / "worktrail").resolve())


def test_rule_c_path_with_line_number(repos_root: Path) -> None:
    (repos_root / "worktrail" / "src").mkdir()
    (repos_root / "worktrail" / "src" / "drain.py").write_text("x = 1\n")
    result = infer_repo("check src/drain.py:42 for the bug", repos_root)
    assert result.rule == "c"
    assert result.repo == str((repos_root / "worktrail").resolve())


def test_rule_c_path_present_in_two_checkouts_is_none(repos_root: Path) -> None:
    result = infer_repo("check README.md for the bug", repos_root)
    assert result.repo is None


def test_two_repo_names_returns_none_with_candidates(repos_root: Path) -> None:
    result = infer_repo("compare worktrail and datalena behavior", repos_root)
    assert result.repo is None
    assert result.rule == "b"
    assert set(result.candidates) == {"worktrail", "datalena"}


def test_rule_a_beats_conflicting_rule_b_mention(repos_root: Path) -> None:
    result = infer_repo("Repo: worktrail -- unrelated mention of datalena here", repos_root)
    assert result.rule == "a"
    assert result.repo == str((repos_root / "worktrail").resolve())


def test_no_mention_returns_none(repos_root: Path) -> None:
    result = infer_repo("this focus names no known repo at all", repos_root)
    assert result.repo is None
    assert result.rule is None
    assert result.candidates == []


def test_non_git_directory_is_never_a_known_repo(repos_root: Path) -> None:
    result = infer_repo("foo-worktrees needs cleanup", repos_root)
    assert result.repo is None
