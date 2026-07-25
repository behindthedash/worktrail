#!/usr/bin/env python3
"""
Plugin-cache freshness guard.

`go`/`sdd-workflow`'s Phase 0 script resolution falls back to
`find ~/.claude/plugins -path '*<plugin>*/skills/<skill>'` whenever
`$CLAUDE_PLUGIN_ROOT` is unset (frequent in the Bash-tool shell). That `find`
walks both `~/.claude/plugins/marketplaces/<name>/` (a live git checkout the
plugin manager keeps roughly in sync) and `~/.claude/plugins/cache/<name>/
<plugin>/<short-sha>/` (install-time snapshots -- multiple stale generations
accumulate uncleaned). `find`'s traversal order across those sibling
directories is filesystem-dependent and NOT stable across invocations (verified:
five repeat runs of the identical `find` command returned five different
orderings of the cache generations), so `head -1` can silently pick a
feature-lagged snapshot -- and two sibling resolutions in the same script
(e.g. `$GO` and `$HANDOFF`) can each land on a *different* stale generation.

This module answers one question: does the resolved path's commit match the
canonical dev checkout (default `~/projects/developer-kit`)? When it doesn't,
and the canonical checkout has the same skill dir on disk, `prefer_path`
offers the fresher path so the caller can warn and auto-prefer it -- the
"diff/grep of cache vs checkout, then use the checkout" recovery every prior
incident did by hand.

Best-effort by design: no canonical checkout, no git, or an unrecognized path
shape all resolve to `fresh: true` (nothing to compare against) rather than
raising -- staleness detection is an enhancement, not a hard dependency.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

_CACHE_SHA_RE = re.compile(r"/([0-9a-f]{12})(?:/|$)")


def _git_head_short(repo: Path) -> Optional[str]:
    """Short (12-char) HEAD SHA of `repo`, or None if it isn't a usable git repo."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    sha = out.stdout.strip()
    return sha or None


def _find_git_root(path: Path) -> Optional[Path]:
    """Walk up from `path` to the nearest ancestor containing `.git`."""
    for d in (path, *path.parents):
        if (d / ".git").exists():
            return d
    return None


def resolved_sha(resolved: Path) -> Optional[str]:
    """Best-effort commit identity of a resolved skill-dir path.

    - A `.../<plugin>/<12-hex-sha>/...` cache path -> the embedded SHA (no git
      call needed -- the cache directory name IS the install-time commit).
    - A path inside a live git checkout (e.g. `marketplaces/<name>/...` or the
      canonical dev checkout itself) -> that checkout's current HEAD.
    - Anything else -> None (unrecognized shape; can't compare).
    """
    m = _CACHE_SHA_RE.search(str(resolved))
    if m:
        return m.group(1)
    root = _find_git_root(resolved)
    if root is not None:
        return _git_head_short(root)
    return None


def _skill_suffix(resolved: Path) -> Optional[str]:
    """The `skills/<name>[/...]` tail of a resolved path, for rebuilding the
    equivalent path under a different checkout root."""
    parts = resolved.parts
    for i, part in enumerate(parts):
        if part == "skills":
            return str(Path(*parts[i:]))
    return None


def check(resolved: str, plugin: str, canonical: str) -> Dict[str, object]:
    """Compare `resolved` (an already-resolved skill dir path) against the
    canonical checkout's copy of the same plugin/skill.

    Returns `{"fresh": bool, "resolved_sha": str|None, "canonical_sha": str|None,
    "prefer_path": str|None, "warning": str|None}`. `prefer_path` is populated
    only when stale AND the canonical checkout actually has that skill dir on
    disk (never point at a path that doesn't exist).
    """
    resolved_path = Path(resolved)
    canonical_path = Path(canonical).expanduser()

    result: Dict[str, object] = {
        "fresh": True,
        "resolved_sha": None,
        "canonical_sha": None,
        "prefer_path": None,
        "warning": None,
    }

    if not canonical_path.is_dir() or not (canonical_path / ".git").exists():
        return result  # no canonical checkout on this machine -- nothing to compare

    canon_sha = _git_head_short(canonical_path)
    r_sha = resolved_sha(resolved_path)
    result["resolved_sha"] = r_sha
    result["canonical_sha"] = canon_sha

    if r_sha is None or canon_sha is None or r_sha == canon_sha:
        return result  # can't compare, or already fresh

    result["fresh"] = False
    suffix = _skill_suffix(resolved_path)
    if suffix is not None:
        candidate = canonical_path / "plugins" / plugin / suffix
        if candidate.is_dir():
            result["prefer_path"] = str(candidate)

    prefer_note = f" -- prefer {result['prefer_path']}" if result["prefer_path"] else ""
    result["warning"] = (
        f"resolved path is a stale plugin snapshot ({r_sha}, canonical dev checkout "
        f"{canonical} is at {canon_sha}){prefer_note}"
    )
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Plugin-cache freshness guard")
    p.add_argument("--resolved", required=True, help="already-resolved skill dir path")
    p.add_argument(
        "--plugin",
        required=True,
        help="plugin name as it appears under plugins/ in the canonical checkout "
        "(e.g. developer-kit-project-management)",
    )
    p.add_argument(
        "--canonical",
        default="~/projects/developer-kit",
        help="canonical dev checkout to compare against (default: ~/projects/developer-kit)",
    )
    p.add_argument("--json", action="store_true", help="emit JSON instead of a summary line")
    args = p.parse_args(argv)

    res = check(args.resolved, args.plugin, args.canonical)
    if args.json:
        print(json.dumps(res))
    elif res["fresh"]:
        print("fresh")
    else:
        print(f"STALE: {res['warning']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
