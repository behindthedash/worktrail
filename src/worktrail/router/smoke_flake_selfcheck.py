#!/usr/bin/env python3
"""smoke_flake_selfcheck.py — recurring-smoke-flake detector.

When `integrate_smoke_retries` is enabled, a smoke suite that fails once and
passes on retry is recorded under the run journal's `smoke_flakes[<suite>]`
(`integrate._record_smoke_flake`). One such entry is noise; the same suite
flaking across several runs is a flaky test to fix, not a reason to raise the
retry count. This module aggregates those entries per repository so the
dashboard can surface them.

Unlike `journal_selfcheck.py`, the run lock is deliberately NOT consulted: a
live run's flake evidence is just as relevant as a finished run's.

Passive detector, not a gate. No network calls.

Usage:
  smoke_flake_selfcheck.py --repo /path/to/repo [--window-days N] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


def _load_smoke_flakes(journal_file: Path) -> dict[str, str] | None:
    """Return the journal's `smoke_flakes` map, or None if the journal is
    unreadable, does not parse, is not an object, or the map is malformed."""
    try:
        journal = json.loads(journal_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(journal, dict):
        return None
    flakes = journal.get("smoke_flakes")
    if not isinstance(flakes, dict):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in flakes.items()):
        return None
    return flakes


def check_repo(
    repo: Path, *, window_days: int = 30, now: float | None = None
) -> dict[str, Any]:
    """Aggregate `smoke_flakes` across `<repo>-worktrees/run-*.json` journals
    modified within the last `window_days`.

    Returns {"entries": [{"suite", "count", "runs", "recurrence", "detail"}, ...]}
    ordered by count descending, then suite ascending. `runs` lists spec ids
    most recent first; `detail` is the most recent run's recorded detail.
    """
    repo = Path(repo)
    wt_base = repo.parent / f"{repo.name}-worktrees"
    if not wt_base.is_dir():
        return {"entries": []}
    if now is None:
        now = time.time()
    cutoff = now - window_days * 86400

    # suite -> list of (mtime, spec_id, detail)
    by_suite: dict[str, list[tuple[float, str, str]]] = {}
    for journal_file in wt_base.glob("run-*.json"):
        try:
            mtime = journal_file.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        flakes = _load_smoke_flakes(journal_file)
        if not flakes:
            continue
        spec_id = journal_file.stem[len("run-") :]
        for suite, detail in flakes.items():
            by_suite.setdefault(suite, []).append((mtime, spec_id, detail))

    entries: list[dict[str, Any]] = []
    for suite, hits in by_suite.items():
        hits.sort(key=lambda h: (-h[0], h[1]))
        entries.append(
            {
                "suite": suite,
                "count": len(hits),
                "runs": [h[1] for h in hits],
                "recurrence": "recurring" if len(hits) >= 2 else "single",
                "detail": hits[0][2],
            }
        )
    entries.sort(key=lambda e: (-e["count"], e["suite"]))
    return {"entries": entries}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="recurring-smoke-flake detector")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--window-days", type=int, default=30)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = check_repo(Path(args.repo).expanduser(), window_days=args.window_days)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for e in result["entries"]:
            print(
                f"{e['recurrence']}: {e['suite']} flaked in {e['count']} run(s) "
                f"[{', '.join(e['runs'])}] — {e['detail']}"
            )
        if not result["entries"]:
            print("clean: no smoke flakes recorded")
    return 1 if result["entries"] else 0


if __name__ == "__main__":
    sys.exit(main())
