#!/usr/bin/env python3
"""Enforce ruff's EXE001/EXE002 on a platform where ruff itself will not.

ruff's own rule documentation says of `shebang-not-executable` (EXE001):
"available on Unix-like systems, and is **not enforced on Windows or WSL**."
The same holds for `shebang-missing-executable-file` (EXE002). This fleet
develops on WSL2 and runs CI on GitHub's Linux runners, so those two rules are
silently off locally and on for every PR -- a gate that reports PASS on a tree
CI is about to reject.

Verified directly on 2026-09-19 (PR #1279): five files committed at mode
100644 with a `#!` line passed `ruff check .` locally and failed CI's
`Lint, Test & Build` on both matrix legs with five EXE001 findings. Pinning
ruff does not help: `uvx ruff@0.16.7 check --select EXE001,EXE002` finds
nothing on a WSL checkout either, because the rules are disabled by platform,
not by version.

This check answers the same question from **git's index**, which is identical
on every platform and is also what actually ships: `git ls-files -s` reports
the recorded mode (100644 or 100755), and the blob's first two bytes say
whether there is a shebang. No filesystem permission bits are consulted, so
WSL, macOS and a Linux runner all agree.

Scope is deliberately the two mode-dependent rules only. EXE003/4/5 inspect
shebang *text*, which ruff does enforce everywhere, so duplicating them here
would create a second opinion on a rule that already has one.

Usage:

    python3 scripts/ci/check_shebang_exec_bits.py            # whole index
    python3 scripts/ci/check_shebang_exec_bits.py --repo DIR
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# Extensions whose shebang/mode agreement this enforces. Kept to the ones ruff
# itself lints, so this check and `ruff check .` can never disagree about a
# file on a platform where both run.
_EXTENSIONS = (".py", ".pyi")

_MODE_EXEC = "100755"
_MODE_PLAIN = "100644"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def tracked_modes(repo: Path) -> list[tuple[str, str]]:
    """`(mode, path)` for every tracked file, straight from the index."""
    result = _git(repo, "ls-files", "-s")
    if result.returncode != 0:
        raise RuntimeError(f"git ls-files failed in {repo}: {result.stderr.strip()}")
    rows: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        # "<mode> <sha> <stage>\t<path>"
        meta, _, path = line.partition("\t")
        if not path:
            continue
        rows.append((meta.split()[0], path))
    return rows


def has_shebang(repo: Path, path: str) -> bool:
    """Whether the INDEXED content of `path` starts with `#!`.

    Read from the index rather than the worktree so an uncommitted local edit
    can never make this disagree with what CI will lint.
    """
    result = _git(repo, "show", f":{path}")
    if result.returncode != 0:
        return False
    return result.stdout.startswith("#!")


def find_violations(repo: Path) -> list[str]:
    """One message per EXE001/EXE002-shaped disagreement, in path order."""
    problems: list[str] = []
    for mode, path in sorted(tracked_modes(repo), key=lambda row: row[1]):
        if not path.endswith(_EXTENSIONS):
            continue
        if mode not in (_MODE_EXEC, _MODE_PLAIN):
            continue  # symlink (120000) or gitlink (160000): not our business
        shebang = has_shebang(repo, path)
        if shebang and mode == _MODE_PLAIN:
            problems.append(
                f"{path}: EXE001 shebang present but the file is not executable. "
                f"Fix with `git update-index --chmod=+x {path}` (or drop the shebang)."
            )
        elif not shebang and mode == _MODE_EXEC:
            problems.append(
                f"{path}: EXE002 file is executable but has no shebang. "
                f"Fix with `git update-index --chmod=-x {path}` (or add a shebang)."
            )
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="repository to check")
    args = parser.parse_args(argv)

    repo = Path(args.repo).expanduser().resolve()
    try:
        problems = find_violations(repo)
    except RuntimeError as exc:
        print(f"check_shebang_exec_bits: {exc}", file=sys.stderr)
        return 2
    if not problems:
        return 0
    print(
        f"check_shebang_exec_bits: {len(problems)} file(s) whose shebang and git "
        "mode disagree (ruff reports these as EXE001/EXE002 on CI, but not on "
        "Windows or WSL):",
        file=sys.stderr,
    )
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
