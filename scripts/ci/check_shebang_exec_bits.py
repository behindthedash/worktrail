#!/usr/bin/env python3.14
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

The same index scan also catches one interpreter-version hazard no platform's
ruff can: an executable file whose shebang is the generic
`#!/usr/bin/env python3` while its source needs PEP 758's unparenthesized
multi-exception syntax (Python 3.14+). `python3` follows the host -- a 3.12
`python3` killed the Stop hook -- while the syntax only parses on 3.14, so
the diagnostic names the `python3.14` shebang that fixes it. The policy stays
narrow: a generic shebang on source without that syntax is left alone, and
the rule keys off that shebang rather than a `.py` suffix, so an extensionless
executable is covered too -- its name is not what makes it run directly. This
script's own shebang is pinned for the same reason: ruff's 3.14 formatter
normalizes `except (A, B):` to the unparenthesized spelling, so its source
needs 3.14 just as the files it reports on do.

Scope is deliberately the two mode-dependent rules plus that one narrow
interpreter check. EXE003/4/5 inspect shebang *text*, which ruff does enforce
everywhere, so duplicating them here would create a second opinion on a rule
that already has one.

Usage:

    python3.14 scripts/ci/check_shebang_exec_bits.py            # whole index
    python3.14 scripts/ci/check_shebang_exec_bits.py --repo DIR
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

# Extensions whose EXE001/EXE002 shebang/mode agreement this enforces. Kept to
# the ones ruff itself lints, so this check and `ruff check .` can never
# disagree about a file on a platform where both run. The interpreter rule
# below is deliberately not suffix-filtered: it keys off the python3 shebang
# itself, so an extensionless executable is checked as well.
_EXTENSIONS = (".py", ".pyi")

_MODE_EXEC = "100755"
_MODE_PLAIN = "100644"

# A generic `python3` is whatever the host ships; PEP 758 syntax needs 3.14.
_GENERIC_PYTHON_SHEBANG = "#!/usr/bin/env python3"
_PINNED_PYTHON_SHEBANG = "#!/usr/bin/env python3.14"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        # Executables include binaries; replacement characters keep a blob that
        # is not text from crashing the scan (and it can never be a match).
        errors="replace",
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


def indexed_content(repo: Path, path: str) -> str | None:
    """The INDEXED content of `path`, or None when the index has no such blob.

    Read from the index rather than the worktree so an uncommitted local edit
    can never make this disagree with what CI will lint.
    """
    result = _git(repo, "show", f":{path}")
    if result.returncode != 0:
        return None
    return result.stdout


def has_unparenthesized_multi_except(source: str) -> bool:
    """Whether `source` contains a PEP 758 unparenthesized multi-exception handler.

    PEP 758 (Python 3.14) accepts `except Foo, Bar:` in place of
    `except (Foo, Bar):`, so a file using it cannot even be parsed by an older
    `python3`. Detection parses the source with the interpreter running this
    check -- 3.14 in CI and in the policy's own command -- and inspects the
    handler's own source span, so `except (Foo, Bar):` (valid everywhere) is
    not reported and neither is `except Foo, Bar:` quoted in a comment or
    string literal. Source this interpreter cannot parse is not judged.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError, ValueError:
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler) or not isinstance(
            node.type, ast.Tuple
        ):
            continue
        span = ast.get_source_segment(source, node.type)
        if span is None:
            continue  # no position info: do not report what cannot be verified
        stripped = span.strip()
        if stripped.startswith("(") and stripped.endswith(")"):
            continue  # parenthesized tuple, valid on every supported python3
        return True
    return False


def generic_interpreter_pep758_mismatch(content: str) -> bool:
    """Whether indexed `content` pairs a generic `python3` shebang with PEP 758."""
    lines = content.splitlines()
    if not lines or lines[0] != _GENERIC_PYTHON_SHEBANG:
        return False
    return has_unparenthesized_multi_except(content)


def find_violations(repo: Path) -> list[str]:
    """One message per direct-execution shebang problem, in path order.

    Two families: the EXE001/EXE002-shaped mode disagreements we port from
    ruff -- limited to the suffixes ruff lints -- and any executable file
    whose generic `python3` shebang cannot parse its PEP 758 syntax,
    extensionless scripts included, since the shebang rather than the suffix
    is what makes a directly runnable file Python.
    """
    problems: list[str] = []
    for mode, path in sorted(tracked_modes(repo), key=lambda row: row[1]):
        if mode not in (_MODE_EXEC, _MODE_PLAIN):
            continue  # symlink (120000) or gitlink (160000): not our business
        ruff_linted = path.endswith(_EXTENSIONS)
        if not ruff_linted and mode != _MODE_EXEC:
            continue  # only the interpreter rule reaches beyond ruff's files
        content = indexed_content(repo, path)
        if content is None:
            continue
        shebang = content.startswith("#!")
        if ruff_linted and shebang and mode == _MODE_PLAIN:
            problems.append(
                f"{path}: EXE001 shebang present but the file is not executable. "
                f"Fix with `git update-index --chmod=+x {path}` (or drop the shebang)."
            )
        elif ruff_linted and not shebang and mode == _MODE_EXEC:
            problems.append(
                f"{path}: EXE002 file is executable but has no shebang. "
                f"Fix with `git update-index --chmod=-x {path}` (or add a shebang)."
            )
        elif mode == _MODE_EXEC and generic_interpreter_pep758_mismatch(content):
            problems.append(
                f"{path}: PEP 758 unparenthesized multi-exception handler behind the "
                f"generic `{_GENERIC_PYTHON_SHEBANG}` shebang -- that syntax parses only "
                f"on Python 3.14+, while `python3` resolves to whatever the host ships. "
                f"Fix line 1 to `{_PINNED_PYTHON_SHEBANG}`."
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
        f"check_shebang_exec_bits: {len(problems)} file(s) whose shebang breaks "
        "direct execution (EXE001/EXE002 mode disagreements -- reported by ruff on "
        "CI but not on Windows or WSL -- or a generic `python3` shebang on PEP 758 "
        "syntax):",
        file=sys.stderr,
    )
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
