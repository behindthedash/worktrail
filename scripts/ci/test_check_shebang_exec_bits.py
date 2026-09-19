from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("check_shebang_exec_bits.py")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", str(r)], check=True, capture_output=True)
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    return r


def _add(repo: Path, rel: str, body: str, *, executable: bool) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    _git(repo, "add", rel)
    _git(repo, "update-index", f"--chmod={'+' if executable else '-'}x", rel)


def _run(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo)],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_repo_passes(repo: Path) -> None:
    _add(repo, "a.py", "#!/usr/bin/env python3\nx = 1\n", executable=True)
    _add(repo, "b.py", "x = 1\n", executable=False)
    result = _run(repo)
    assert result.returncode == 0, result.stderr


def test_shebang_without_exec_bit_is_exe001(repo: Path) -> None:
    """The exact shape PR #1279 shipped: mode 100644 plus a `#!` line, which
    ruff reports on CI but not on WSL."""
    _add(repo, "pkg/mod.py", "#!/usr/bin/env python3\nx = 1\n", executable=False)
    result = _run(repo)
    assert result.returncode == 1
    assert "EXE001" in result.stderr
    assert "pkg/mod.py" in result.stderr
    assert "git update-index --chmod=+x pkg/mod.py" in result.stderr


def test_exec_bit_without_shebang_is_exe002(repo: Path) -> None:
    _add(repo, "pkg/mod.py", "x = 1\n", executable=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "EXE002" in result.stderr
    assert "git update-index --chmod=-x pkg/mod.py" in result.stderr


def test_every_violation_is_reported_not_just_the_first(repo: Path) -> None:
    _add(repo, "a.py", "#!/usr/bin/env python3\nx = 1\n", executable=False)
    _add(repo, "b.py", "#!/usr/bin/env python3\nx = 2\n", executable=False)
    result = _run(repo)
    assert result.returncode == 1
    assert "a.py" in result.stderr
    assert "b.py" in result.stderr
    assert "2 file(s)" in result.stderr


def test_non_python_files_are_ignored(repo: Path) -> None:
    """Shell scripts have their own conventions and ruff does not lint them;
    a second opinion here would be a new rule, not a ported one."""
    _add(repo, "run.sh", "#!/usr/bin/env bash\necho hi\n", executable=False)
    _add(repo, "notes.md", "#!not a shebang\n", executable=False)
    assert _run(repo).returncode == 0


def test_pyi_stubs_are_checked(repo: Path) -> None:
    _add(repo, "mod.pyi", "#!/usr/bin/env python3\nx: int\n", executable=False)
    assert _run(repo).returncode == 1


def test_untracked_files_are_ignored(repo: Path) -> None:
    """The index is what ships; an untracked scratch file is not CI's problem."""
    _add(repo, "a.py", "#!/usr/bin/env python3\nx = 1\n", executable=True)
    (repo / "scratch.py").write_text("#!/usr/bin/env python3\nx = 1\n")
    assert _run(repo).returncode == 0


def test_the_index_wins_over_an_uncommitted_edit(repo: Path) -> None:
    """A local edit that removes the shebang must not mask a violation that
    the indexed content still carries."""
    _add(repo, "a.py", "#!/usr/bin/env python3\nx = 1\n", executable=False)
    (repo / "a.py").write_text("x = 1\n")  # worktree no longer has a shebang
    result = _run(repo)
    assert result.returncode == 1
    assert "EXE001" in result.stderr


def test_not_a_git_repo_exits_two(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "git ls-files failed" in result.stderr


def test_this_repo_is_clean() -> None:
    """The gate must be green on the tree that ships it."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(SCRIPT.resolve().parents[2])],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
