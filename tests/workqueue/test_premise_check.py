"""Tests for `worktrail.workqueue.premise_check` against a real `git init`'d repo.

`git grep` only searches tracked (or staged) files, never the raw working
tree, so every fixture file that a needle should match must be `git add`-ed
(no commit required).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from worktrail.workqueue.premise_check import (
    extract_needles,
    format_premise_block,
    run_premise_check,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _add(repo: Path, relpath: str, content: str) -> Path:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    subprocess.run(["git", "-C", str(repo), "add", relpath], check=True)
    return target


MOTIVATING_FOCUS = (
    "Repo: /home/brian/projects/worktrail\n"
    "The drain loop logged 'no TASK-*.md found: check queue directory' before "
    "exiting early.\n"
    "Check src/worktrail/drain/drain.py:42 for the retry loop.\n"
    "Reproduce with `pytest tests/drain/test_drain_loop.py`.\n"
)


def test_extract_needles_from_motivating_focus() -> None:
    needles = extract_needles(MOTIVATING_FOCUS)
    kinds = [(n.kind, n.needle) for n in needles]

    assert (
        "quoted",
        "no TASK-*.md found: check queue directory",
    ) in kinds
    assert ("path", "src/worktrail/drain/drain.py:42") in kinds
    assert ("command", "pytest tests/drain/test_drain_loop.py") in kinds
    # "Repo:" itself is bare prose, never quoted, so it is never a candidate
    # for a command needle regardless of the allow-list/verb heuristics.
    assert not any(n.needle.startswith("Repo") for n in needles)


def test_whole_string_hit_confirms(repo: Path) -> None:
    focus = "The log said 'a very specific error message here' during the run."
    _add(repo, "logs/output.txt", "a very specific error message here\n")

    results = run_premise_check(focus, repo)
    quoted = next(r for r in results if r["kind"] == "quoted")

    assert quoted["confirmed"] is True
    assert "matched whole string" in quoted["detail"]
    assert "logs/output.txt" in quoted["detail"]


def test_fragment_fallback_confirms(repo: Path) -> None:
    focus = "The drain loop logged 'no TASK-*.md found: check queue directory' before exiting."
    _add(repo, "logs/output.txt", "no TASK-*.md found\n")

    results = run_premise_check(focus, repo)
    quoted = next(r for r in results if r["kind"] == "quoted")

    assert quoted["confirmed"] is True
    assert "whole string not found" in quoted["detail"]
    assert "no TASK-*.md found" in quoted["detail"]
    assert "logs/output.txt" in quoted["detail"]


def test_quoted_no_hit_is_unconfirmed(repo: Path) -> None:
    focus = "The log said 'this text never appears anywhere in the repo'."

    results = run_premise_check(focus, repo)
    quoted = next(r for r in results if r["kind"] == "quoted")

    assert quoted["confirmed"] is False
    assert quoted["detail"] == "no match for whole string or fragments"


def test_path_present(repo: Path) -> None:
    _add(repo, "src/worktrail/drain/drain.py", "line1\nline2\nline3\n")
    focus = "See `src/worktrail/drain/drain.py` for the loop."

    results = run_premise_check(focus, repo)
    path_result = next(r for r in results if r["kind"] == "path")

    assert path_result["confirmed"] is True
    assert "path exists" in path_result["detail"]


def test_path_absent(repo: Path) -> None:
    focus = "See `src/worktrail/does_not_exist.py` for the loop."

    results = run_premise_check(focus, repo)
    path_result = next(r for r in results if r["kind"] == "path")

    assert path_result["confirmed"] is False
    assert "does not exist" in path_result["detail"]


def test_path_with_line_beyond_file_length(repo: Path) -> None:
    _add(repo, "src/worktrail/drain/drain.py", "line1\nline2\n")
    focus = "See `src/worktrail/drain/drain.py:42` for the loop."

    results = run_premise_check(focus, repo)
    path_result = next(r for r in results if r["kind"] == "path")

    assert path_result["confirmed"] is False
    assert "only 2 lines" in path_result["detail"]
    assert "line 42" in path_result["detail"]


def test_path_with_line_within_file_length(repo: Path) -> None:
    _add(repo, "src/worktrail/drain/drain.py", "line1\nline2\nline3\n")
    focus = "See `src/worktrail/drain/drain.py:2` for the loop."

    results = run_premise_check(focus, repo)
    path_result = next(r for r in results if r["kind"] == "path")

    assert path_result["confirmed"] is True
    assert "line 2 present" in path_result["detail"]


def test_allow_listed_command_nonzero_exit_confirms(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    focus = "Reproduce with `pytest tests/drain/test_drain_loop.py`."
    captured: dict = {}

    def fake_run(args, **kwargs):
        captured["args"] = args
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(
            args, returncode=1, stdout="1 failed\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    results = run_premise_check(focus, repo)
    command_result = next(r for r in results if r["kind"] == "command")

    assert command_result["confirmed"] is True
    assert "exit code 1" in command_result["detail"]
    assert captured["args"] == ["pytest", "tests/drain/test_drain_loop.py"]
    assert captured["cwd"] == repo


def test_allow_listed_command_zero_exit_does_not_confirm(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    focus = "Reproduce with `pytest tests/drain/test_drain_loop.py`."

    def fake_run(args, **kwargs):
        return subprocess.CompletedProcess(
            args, returncode=0, stdout="1 passed\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    results = run_premise_check(focus, repo)
    command_result = next(r for r in results if r["kind"] == "command")

    assert command_result["confirmed"] is False
    assert "exit code 0" in command_result["detail"]


def test_non_allow_listed_command_never_run_and_recorded_unrunnable(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    focus = "Do not run `rm -rf /tmp/whatever` under any circumstances."
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if isinstance(args, list) and args and args[0].startswith("rm"):
            raise AssertionError(f"subprocess.run must not be called with {args!r}")
        # git grep (from the sibling "quoted" needle check) is allowed through.
        return real_run(args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    results = run_premise_check(focus, repo)
    command_result = next(r for r in results if r["kind"] == "command")

    assert command_result["needle"] == "rm -rf /tmp/whatever"
    assert command_result["confirmed"] is False
    assert "not allow-listed" in command_result["detail"]


def test_timeout_expired_is_unconfirmed_with_timeout_detail(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    focus = "Reproduce with `pytest tests/drain/test_drain_loop.py`."
    real_run = subprocess.run

    def fake_run(args, **kwargs):
        if isinstance(args, list) and args[0] == "git":
            return real_run(args, **kwargs)
        raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs.get("timeout", 120))

    monkeypatch.setattr(subprocess, "run", fake_run)

    results = run_premise_check(focus, repo, timeout_s=5)
    command_result = next(r for r in results if r["kind"] == "command")

    assert command_result["confirmed"] is False
    assert "timed out after 5s" in command_result["detail"]


def test_empty_focus_returns_empty_list(repo: Path) -> None:
    assert run_premise_check("", repo) == []
    assert extract_needles("") == []


def test_format_premise_block_renders_none_for_empty_list() -> None:
    assert format_premise_block([]) == "(none)"


def test_format_premise_block_renders_confirmed_and_unconfirmed(repo: Path) -> None:
    focus = "The log said 'this text never appears anywhere in the repo'."

    results = run_premise_check(focus, repo)
    block = format_premise_block(results)

    assert "[UNCONFIRMED] quoted:" in block
