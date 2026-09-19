from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).with_name("check_gitleaks_signal_integrity.py")

# Real gitleaks 8.21.2 stderr output (ANSI codes included, as captured when
# redirected on this repo's CI runners -- the regex matches the digit/text
# substring regardless).
_HEALTHY_LOG = "\x1b[90m8:47PM\x1b[0m \x1b[32mINF\x1b[0m 1 commits scanned.\n\x1b[90m8:47PM\x1b[0m \x1b[32mINF\x1b[0m no leaks found\n"
_ZERO_COMMITS_LOG = "\x1b[90m8:47PM\x1b[0m \x1b[32mINF\x1b[0m 0 commits scanned.\n\x1b[90m8:47PM\x1b[0m \x1b[32mINF\x1b[0m no leaks found\n"
_CRASH_LOG = "\x1b[90m8:53PM\x1b[0m \x1b[1m\x1b[31mFTL\x1b[0m\x1b[0m stat /nonexistent: no such file or directory\n"


def _run(
    *,
    log_text: str | None,
    tmp_path: Path,
    min_commits: str = "1",
    range_spec: str | None = None,
) -> subprocess.CompletedProcess[str]:
    args = [sys.executable, str(SCRIPT), "--min-commits", min_commits]
    if range_spec is not None:
        args += ["--range", range_spec]
    if log_text is not None:
        log_path = tmp_path / "gitleaks.log"
        log_path.write_text(log_text, encoding="utf-8")
        args += ["--log", str(log_path)]
    else:
        args += ["--log", str(tmp_path / "missing.log")]
    return subprocess.run(
        args, cwd=tmp_path, text=True, capture_output=True, check=False
    )


def test_healthy_run_passes(tmp_path: Path) -> None:
    result = _run(log_text=_HEALTHY_LOG, tmp_path=tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 commit" in result.stdout


def test_zero_commit_range_fails(tmp_path: Path) -> None:
    result = _run(log_text=_ZERO_COMMITS_LOG, tmp_path=tmp_path)

    assert result.returncode == 1
    assert "below the floor" in result.stdout


def test_missing_log_fails_loudly(tmp_path: Path) -> None:
    result = _run(log_text=None, tmp_path=tmp_path)

    assert result.returncode == 1
    assert "not found" in result.stdout


def test_crash_before_scan_summary_fails_loudly(tmp_path: Path) -> None:
    result = _run(log_text=_CRASH_LOG, tmp_path=tmp_path)

    assert result.returncode == 1
    assert "Could not find" in result.stdout


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _commit_history(repo: Path) -> tuple[str, str, str]:
    """base (adds lines) -> deletion-only commit -> addition commit; returns their SHAs."""
    _git(repo, "init", "-q")
    (repo / "a.md").write_text("hello\nworld\n", encoding="utf-8")
    (repo / "b.md").write_text("x\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _git(repo, "rm", "-q", "a.md")
    _git(repo, "commit", "-qm", "delete only")
    deletion = _git(repo, "rev-parse", "HEAD")
    (repo / "b.md").write_text("x\ny\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "addition")
    return base, deletion, _git(repo, "rev-parse", "HEAD")


def test_deletion_only_range_passes_despite_zero_scanned(tmp_path: Path) -> None:
    # gitleaks 8.21.2 logs "0 commits scanned." for a real range whose only
    # commit removes lines (nothing to leak); the range itself is non-empty.
    base, deletion, _ = _commit_history(tmp_path)

    result = _run(
        log_text=_ZERO_COMMITS_LOG, tmp_path=tmp_path, range_spec=f"{base}..{deletion}"
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "deletion-only" in result.stdout


def test_empty_range_still_fails_when_range_given(tmp_path: Path) -> None:
    _, deletion, _ = _commit_history(tmp_path)

    result = _run(
        log_text=_ZERO_COMMITS_LOG,
        tmp_path=tmp_path,
        range_spec=f"{deletion}..{deletion}",
    )

    assert result.returncode == 1
    assert "below the floor" in result.stdout


def test_zero_scanned_fails_when_range_adds_lines(tmp_path: Path) -> None:
    base, _, addition = _commit_history(tmp_path)

    result = _run(
        log_text=_ZERO_COMMITS_LOG, tmp_path=tmp_path, range_spec=f"{base}..{addition}"
    )

    assert result.returncode == 1
    assert "below the floor" in result.stdout


def test_unresolvable_range_fails(tmp_path: Path) -> None:
    _commit_history(tmp_path)

    result = _run(
        log_text=_ZERO_COMMITS_LOG,
        tmp_path=tmp_path,
        range_spec="nonexistent1..nonexistent2",
    )

    assert result.returncode == 1
    assert "below the floor" in result.stdout
