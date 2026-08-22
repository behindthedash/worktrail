#!/usr/bin/env python3
"""
`go`'s Phase 3 repo-resolution staleness guard.

`resolve_repo.py` decides *which* checkout on disk is `$REPO` -- correctly,
and by pure file inspection only (see its own docstring). It has no way to
know whether that checkout is missing commits that already landed on the
remote. A stale local checkout is not just "behind" in the abstract: it can
be missing files entirely -- `policy.py` reads `docs/specs/worktrail-go-policy.yaml`
off disk and reports "no policy configured" when the file doesn't exist
locally, with no way to distinguish "this repo has never had a policy file"
from "the policy file was added upstream and this checkout hasn't pulled it
yet." Both look identical to `policy.py`.

Incident: `kudera-consulting`'s local checkout was one commit behind
`origin/main`, and `policy.py` silently fell back to defaults and reported
no policy at all instead of surfacing "a policy exists upstream, pull
first." Multiplied across a 16+ repo workspace, a stale local checkout is a
systemic way for `/go` to file briefs against phantom gaps -- an absence on
disk that isn't actually an absence upstream.

This module answers one question: is `$REPO`'s checked-out branch behind its
remote-tracking branch? Best-effort by design -- offline, no remote, a
detached HEAD, or an unreachable network all degrade to "unknown"
(`checked: false`) rather than raising or blocking; staleness detection is a
nudge, not a hard gate.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple


def _run(args, timeout: int) -> Optional[subprocess.CompletedProcess]:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def current_branch(repo: Path) -> Optional[str]:
    """The checked-out branch name, or None on a detached HEAD / non-repo."""
    out = _run(["git", "-C", str(repo), "symbolic-ref", "--short", "-q", "HEAD"], timeout=5)
    if out is None or out.returncode != 0:
        return None
    return out.stdout.strip() or None


def fetch_remote(repo: Path, remote: str, branch: str, timeout: int = 10) -> bool:
    """Best-effort `git fetch` of just `branch` from `remote`.

    Failure (offline, auth, unknown remote, timeout) is swallowed -- the
    caller falls back to whatever remote-tracking ref is already cached
    locally, if any.
    """
    out = _run(["git", "-C", str(repo), "fetch", "--quiet", remote, branch], timeout=timeout)
    return out is not None and out.returncode == 0


def ahead_behind(repo: Path, remote: str, branch: str) -> Optional[Tuple[int, int]]:
    """(ahead, behind) of HEAD vs `<remote>/<branch>`.

    None if that remote-tracking ref doesn't exist locally (never fetched,
    wrong remote/branch name, ...).
    """
    out = _run(
        ["git", "-C", str(repo), "rev-list", "--left-right", "--count",
         f"HEAD...{remote}/{branch}"],
        timeout=5,
    )
    if out is None or out.returncode != 0:
        return None
    parts = out.stdout.split()
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None


def check(repo: Path, remote: str = "origin", branch: Optional[str] = None,
          do_fetch: bool = True, fetch_timeout: int = 10) -> Dict[str, object]:
    """Is `repo`'s checked-out branch behind `<remote>/<branch>`?

    Returns `{"checked": bool, "stale": bool, "branch": str|None, "ahead": int,
    "behind": int, "warning": str|None}`. `checked=False` means the question
    could not be answered (not a git repo, detached HEAD, offline, unknown
    remote branch) -- callers must treat that as "no signal", not as "fresh".
    """
    repo = Path(repo)
    result: Dict[str, object] = {
        "checked": False, "stale": False, "branch": None,
        "ahead": 0, "behind": 0, "warning": None,
    }
    if not (repo / ".git").exists():
        return result

    br = branch or current_branch(repo)
    if not br:
        result["warning"] = "detached HEAD or unresolvable branch; cannot verify freshness"
        return result
    result["branch"] = br

    if do_fetch:
        fetch_remote(repo, remote, br, timeout=fetch_timeout)

    ab = ahead_behind(repo, remote, br)
    if ab is None:
        result["warning"] = (
            f"could not compare against {remote}/{br} (offline, unreachable, or no "
            "such remote branch) -- freshness unknown"
        )
        return result

    ahead, behind = ab
    result["checked"] = True
    result["ahead"] = ahead
    result["behind"] = behind
    if behind > 0:
        result["stale"] = True
        result["warning"] = (
            f"local {br} is {behind} commit(s) behind {remote}/{br} -- run `git pull` "
            "before trusting an absent file/policy/spec as a real gap"
        )
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--remote", default="origin")
    p.add_argument("--branch", default=None, help="defaults to the checked-out branch")
    p.add_argument(
        "--no-fetch", action="store_true",
        help="skip the network `git fetch`; compare against whatever remote-tracking "
             "ref is already cached locally",
    )
    p.add_argument("--fetch-timeout", type=int, default=10)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    res = check(Path(args.repo), remote=args.remote, branch=args.branch,
                do_fetch=not args.no_fetch, fetch_timeout=args.fetch_timeout)
    if args.json:
        print(json.dumps(res))
    elif res["stale"]:
        print(f"STALE: {res['warning']}")
    elif res["checked"]:
        print(f"fresh: {res['branch']} up to date with {args.remote}/{res['branch']}")
    else:
        print(f"unknown: {res['warning']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
