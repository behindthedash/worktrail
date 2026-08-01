#!/usr/bin/env python3
"""dashboard_selfcheck.py — spec-doc ambiguity detector for the dashboard's
own spec-file resolution.

`dashboard.py`'s `find_spec_file()` picks the spec doc for a `docs/specs/<id>/`
directory by exclusion, and deliberately refuses to guess when 2+ untagged
candidates tie on naming evidence (see its `_rank` docstring). A refusal there
silently drops that spec from dashboard rendering with no visible signal to a
human/agent — this is a passive detector that surfaces exactly that refusal
so it can be triaged, without changing `find_spec_file()`'s own behavior.

Usage:
  dashboard_selfcheck.py --repo /path/to/repo [--json]
  dashboard_selfcheck.py --repos-root ~/projects [--json]   # sweep every repo
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .dashboard import _is_spec_doc, find_spec_file
from .policy_selfcheck import discover_repo_names

_SPECS_RELPATH = Path("docs") / "specs"


def check_repo(repo: Path) -> Dict[str, Any]:
    """Findings for one repo's `docs/specs/*/`. Empty `findings` = clean."""
    repo = Path(repo)
    specs_root = repo / _SPECS_RELPATH
    result: Dict[str, Any] = {"repo": repo.name, "path": str(repo), "findings": []}
    if not specs_root.is_dir():
        return result
    findings = result["findings"]

    for spec_dir in sorted(p for p in specs_root.iterdir() if p.is_dir()):
        cands = [f for f in spec_dir.glob("*.md") if _is_spec_doc(f.name)]
        if not cands:
            continue
        if find_spec_file(spec_dir) is not None:
            continue
        findings.append({
            "signal": "ambiguous-spec-doc",
            "spec": spec_dir.name,
            "detail": (
                f"{len(cands)} untagged spec-doc candidates tie, refusing to guess: "
                + ", ".join(sorted(f.name for f in cands))
            ),
        })
    return result


def sweep(repos_root: Path) -> List[Dict[str, Any]]:
    """check_repo() for every repo under `repos_root` that has findings."""
    names = discover_repo_names(repos_root)
    results = []
    for name in names:
        r = check_repo(repos_root / name)
        if r["findings"]:
            results.append(r)
    return results


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="single repo to check")
    p.add_argument("--repos-root", help="sweep every repo under this directory")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not args.repo and not args.repos_root:
        p.error("one of --repo or --repos-root is required")

    if args.repo:
        results = [check_repo(Path(args.repo).expanduser())]
    else:
        root = Path(args.repos_root).expanduser()
        results = sweep(root)

    flagged = [r for r in results if r["findings"]]
    if args.json:
        print(json.dumps({"results": results, "flagged": len(flagged)}, indent=2))
    else:
        if not flagged:
            print(f"dashboard_selfcheck: {len(results)} repo(s) checked, no ambiguous spec docs")
        for r in flagged:
            print(f"{r['repo']}:")
            for f in r["findings"]:
                print(f"  [{f['signal']}] {f['spec']}: {f['detail']}")
    return 1 if flagged else 0


if __name__ == "__main__":
    sys.exit(main())
