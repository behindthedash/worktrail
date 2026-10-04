from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("check_shebang_exec_bits.py")

# An executable file whose generic shebang cannot parse its own body: PEP 758
# lets the handler drop its parentheses, which only Python 3.14+ accepts.
_PEP758_BODY = (
    "#!/usr/bin/env python3\ntry:\n    pass\nexcept ValueError, TypeError:\n    pass\n"
)
_PEP758_BODY_PINNED = _PEP758_BODY.replace("env python3\n", "env python3.14\n")


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


def _add(repo: Path, rel: str, body: str | bytes, *, executable: bool) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, bytes):
        path.write_bytes(body)
    else:
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


def test_generic_shebang_with_pep758_handler_is_rejected(repo: Path) -> None:
    """`python3` may resolve to an older interpreter, and this handler only
    parses on 3.14; the diagnostic must name the pin that fixes it."""
    _add(repo, "pkg/mod.py", _PEP758_BODY, executable=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "pkg/mod.py: PEP 758" in result.stderr
    assert "python3.14" in result.stderr


def test_python314_shebang_with_pep758_handler_passes(repo: Path) -> None:
    """The compliant control: same body, interpreter pinned."""
    _add(repo, "pkg/mod.py", _PEP758_BODY_PINNED, executable=True)
    assert _run(repo).returncode == 0


def test_generic_shebang_without_pep758_syntax_passes(repo: Path) -> None:
    """The policy is narrow: a generic shebang alone is not a violation."""
    _add(repo, "a.py", "#!/usr/bin/env python3\nx = 1\n", executable=True)
    assert _run(repo).returncode == 0


def test_parenthesized_multi_except_is_not_a_pep758_violation(repo: Path) -> None:
    """`except (A, B):` parses on every supported python3, so the shebang may
    stay generic; only the PEP 758 spelling is the mismatch."""
    parenthesized = _PEP758_BODY.replace(
        "except ValueError, TypeError:", "except (ValueError, TypeError):"
    )
    _add(repo, "a.py", parenthesized, executable=True)
    assert _run(repo).returncode == 0


def test_individually_parenthesized_elements_are_a_pep758_violation(
    repo: Path,
) -> None:
    """`except (ValueError), (TypeError):` is the PEP 758 spelling with each
    element parenthesized: the leading `(` closes after the first exception,
    so it is not a parenthesized tuple and an older `python3` cannot parse it."""
    body = _PEP758_BODY.replace(
        "except ValueError, TypeError:", "except (ValueError), (TypeError):"
    )
    _add(repo, "a.py", body, executable=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "a.py: PEP 758" in result.stderr


def test_doubly_parenthesized_multi_except_is_not_a_pep758_violation(
    repo: Path,
) -> None:
    """The control for the case above: here the leading `(` really does close
    at the end, so the tuple is parenthesized and every python3 parses it."""
    body = _PEP758_BODY.replace(
        "except ValueError, TypeError:", "except ((ValueError), (TypeError)):"
    )
    _add(repo, "a.py", body, executable=True)
    assert _run(repo).returncode == 0


def test_pep758_lookalikes_in_comments_and_strings_are_not_violations(
    repo: Path,
) -> None:
    """Detection parses the source, so the text is only a hazard in real code."""
    body = (
        "#!/usr/bin/env python3\n"
        "# except ValueError, TypeError:\n"
        'NOTE = "except OSError, ValueError:"\n'
        "x = 1\n"
    )
    _add(repo, "a.py", body, executable=True)
    assert _run(repo).returncode == 0


def test_non_utf8_pep263_source_is_judged_as_written(repo: Path) -> None:
    """A declared latin-1 file is valid Python; decoding its bytes as UTF-8
    would turn `caf\xe9` into a replacement character, making the tree
    unparseable and hiding the very handler this rule exists to find."""
    body = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: latin-1 -*-\n"
        "caf\xe9 = 1\n"
        "try:\n"
        "    pass\n"
        "except ValueError, TypeError:\n"
        "    pass\n"
    ).encode("latin-1")
    _add(repo, "pkg/mod.py", body, executable=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "pkg/mod.py: PEP 758" in result.stderr


def test_non_utf8_pep263_source_with_parenthesized_handler_passes(
    repo: Path,
) -> None:
    """The compliant control for the case above: same declared encoding, a
    handler every python3 parses, judged from the same decoded bytes."""
    body = (
        "#!/usr/bin/env python3\n"
        "# -*- coding: latin-1 -*-\n"
        "caf\xe9 = 1\n"
        "try:\n"
        "    pass\n"
        "except (ValueError, TypeError):\n"
        "    pass\n"
    ).encode("latin-1")
    _add(repo, "pkg/mod.py", body, executable=True)
    assert _run(repo).returncode == 0


def test_non_ascii_path_is_scanned(repo: Path) -> None:
    """`git ls-files` C-quotes a non-ASCII path in line output (`"caf\\303\\251.py"`),
    and the quoted spelling resolves to no blob -- the index must be read
    NUL-separated (`-z`, verbatim paths) or the file is silently skipped."""
    _add(repo, "pkg/caf\xe9.py", _PEP758_BODY, executable=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "pkg/caf\xe9.py: PEP 758" in result.stderr


def test_nested_pep758_handler_is_found(repo: Path) -> None:
    body = (
        "#!/usr/bin/env python3\n"
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except OSError, ValueError:\n"
        "        pass\n"
    )
    _add(repo, "a.py", body, executable=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "a.py: PEP 758" in result.stderr


def test_every_pep758_violation_is_reported_not_just_the_first(repo: Path) -> None:
    _add(repo, "a.py", _PEP758_BODY, executable=True)
    _add(
        repo,
        "b.py",
        _PEP758_BODY.replace("ValueError, TypeError", "OSError, ValueError"),
        executable=True,
    )
    result = _run(repo)
    assert result.returncode == 1
    assert "a.py: PEP 758" in result.stderr
    assert "b.py: PEP 758" in result.stderr
    assert "2 file(s)" in result.stderr


def test_pep758_and_exe_violations_are_reported_together(repo: Path) -> None:
    """The new family does not replace the mode rules: both appear, counted once."""
    _add(repo, "interp.py", _PEP758_BODY, executable=True)
    _add(repo, "mode.py", "#!/usr/bin/env python3\nx = 1\n", executable=False)
    result = _run(repo)
    assert result.returncode == 1
    assert "mode.py: EXE001" in result.stderr
    assert "interp.py: PEP 758" in result.stderr
    assert "2 file(s)" in result.stderr


def test_pep758_rule_applies_only_to_executable_files(repo: Path) -> None:
    """Direct execution is the hazard; a non-executable file is already EXE001
    and gets that diagnostic instead of a second one."""
    _add(repo, "a.py", _PEP758_BODY, executable=False)
    result = _run(repo)
    assert result.returncode == 1
    assert "a.py: EXE001" in result.stderr
    assert "a.py: PEP 758" not in result.stderr


def test_extensionless_executable_with_pep758_handler_is_rejected(repo: Path) -> None:
    """The interpreter rule keys off the shebang, not a `.py` suffix: a file
    with no extension is run directly just the same."""
    _add(repo, "tool", _PEP758_BODY, executable=True)
    result = _run(repo)
    assert result.returncode == 1
    assert "tool: PEP 758" in result.stderr
    assert "python3.14" in result.stderr


def test_extensionless_executable_with_pinned_shebang_passes(repo: Path) -> None:
    _add(repo, "tool", _PEP758_BODY_PINNED, executable=True)
    assert _run(repo).returncode == 0


def test_extensionless_non_python_executables_are_ignored(repo: Path) -> None:
    """Only the exact generic python3 shebang makes an executable this rule's
    business; shell scripts and shebangless binaries keep their own rules."""
    _add(repo, "tool", "#!/usr/bin/env bash\nset -euo pipefail\n", executable=True)
    _add(repo, "bare", "just data\n", executable=True)
    assert _run(repo).returncode == 0


def test_binary_executable_does_not_crash_the_scan(repo: Path) -> None:
    """The scan now reads every executable's bytes; a blob that is not UTF-8
    must be skipped rather than blow up the whole check."""
    (repo / "blob").write_bytes(b"\x7fELF\x02\x01\x01\x00\xff\xfe\x00")
    _git(repo, "add", "blob")
    _git(repo, "update-index", "--chmod=+x", "blob")
    assert _run(repo).returncode == 0


def test_pep758_check_reads_the_index_not_the_worktree(repo: Path) -> None:
    """Fixing only the worktree must not mask what the index still ships."""
    _add(repo, "a.py", _PEP758_BODY, executable=True)
    (repo / "a.py").write_text(_PEP758_BODY_PINNED)  # worktree is compliant
    result = _run(repo)
    assert result.returncode == 1
    assert "a.py: PEP 758" in result.stderr


def test_a_worktree_only_pep758_edit_is_not_a_violation(repo: Path) -> None:
    """The reverse direction: an uncommitted edit to the hazard is not what
    ships, so it must not turn a compliant index red."""
    _add(repo, "a.py", _PEP758_BODY_PINNED, executable=True)
    (repo / "a.py").write_text(_PEP758_BODY)  # worktree carries the hazard
    assert _run(repo).returncode == 0


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
