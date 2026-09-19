#!/usr/bin/env python3
"""Assert a gitleaks scan actually covered a non-zero set of commits.

`gitleaks detect` is already blocking on this repo's own findings (no
`continue-on-error`), so a real leak already fails the job. The gap this
script closes is different: `gitleaks detect` exits 0 and logs "no leaks
found" both when the scan genuinely covered the repo cleanly *and* when its
commit range covered zero commits (e.g. a degenerate `--log-opts` range on
the per-PR diff job) -- both report identically as "no leaks found".

Verified directly (gitleaks 8.21.2): every `detect` run logs a line of the
exact shape `<N> commits scanned.` to stderr (colorized even when the stream
is redirected -- this script matches the digit/text substring, not the
surrounding ANSI codes) before reporting its findings. `--log-opts="X..X"`
(a degenerate/empty range) reliably reproduces `0 commits scanned.` with exit
0 and "no leaks found", identical in job outcome to a healthy clean scan.

`<N> commits scanned` counts only commits whose patch adds lines, so a real
range holding only deletion-only commits (e.g. a PR that just deletes a file)
also logs `0 commits scanned.` (verified: gitleaks 8.21.2, 1-commit range
deleting a file). When `--range` is given, that zero is accepted only if the
range itself is non-empty (git's own commit count meets the floor) and no
commit in it adds a line; a degenerate range or a range with added lines that
gitleaks still reported as 0 keeps failing.

Usage:
    python scripts/ci/check_gitleaks_signal_integrity.py \\
        --log gitleaks-pr-diff.log \\
        --min-commits 1 \\
        [--range BASE..HEAD]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Verified against gitleaks 8.21.2's own log output (redirected to a file);
# ANSI color codes surround the digits/words but are not part of this match.
_COMMITS_SCANNED_RE = re.compile(r"(\d+)\s+commits scanned")


def _is_deletion_only_range(range_spec: str, min_commits: int) -> bool:
    """True when `range_spec` holds at least `min_commits` commits and none adds a line."""
    log = subprocess.run(
        ["git", "log", "--format=@%H", "--numstat", range_spec],
        text=True,
        capture_output=True,
        check=False,
    )
    if log.returncode != 0:
        return False

    commits = 0
    for line in log.stdout.splitlines():
        if line.startswith("@"):
            commits += 1
        elif (
            line
            and line.split("\t", 1)[0].isdigit()
            and int(line.split("\t", 1)[0]) > 0
        ):
            return False
    return commits >= min_commits


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--log",
        required=True,
        help="Path to the tee'd gitleaks CLI log (stdout+stderr)",
    )
    parser.add_argument(
        "--min-commits",
        type=int,
        default=1,
        help="Fail if the scan reports covering fewer commits than this floor",
    )
    parser.add_argument(
        "--range",
        dest="range_spec",
        help="Git revision range the scan covered; lets a zero count pass when the range is deletion-only",
    )
    args = parser.parse_args()

    log_path = Path(args.log)
    if not log_path.is_file():
        print(
            f"::error::gitleaks log not found at {args.log} -- the scan step may not have run at all."
        )
        return 1

    log_text = log_path.read_text(encoding="utf-8", errors="replace")
    match = _COMMITS_SCANNED_RE.search(log_text)
    if match is None:
        print(
            f"::error::Could not find gitleaks's own '<N> commits scanned.' line in {args.log} -- the run "
            "may have crashed before completing, or gitleaks's log output format changed. Treating this "
            "as a signal-integrity failure rather than silently reporting clean."
        )
        return 1

    commits_scanned = int(match.group(1))
    print(f"gitleaks signal integrity: {commits_scanned} commit(s) scanned.")

    if commits_scanned < args.min_commits:
        if args.range_spec and _is_deletion_only_range(
            args.range_spec, args.min_commits
        ):
            print(
                f"gitleaks signal integrity: {args.range_spec} is deletion-only (no commit adds a line), "
                "so a zero commit count is expected."
            )
            return 0
        print(
            f"::error::gitleaks scanned only {commits_scanned} commit(s), below the floor "
            f"({args.min_commits}) -- the scan's commit range was effectively empty, so 'no leaks found' "
            "carries no real signal."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
