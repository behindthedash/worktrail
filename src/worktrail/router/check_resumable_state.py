#!/usr/bin/env python3
"""
Mechanical pre-check for Route E (continue/resume) on a brief-sourced dispatch.

`classify.py`'s Route E signals (`resume`, `handoff`, `worktree`, `open pr`, ...)
are plain keyword regexes with no negation awareness. A brief *reporting* that no
resumable state exists ("no worktree", "no open PR", "route hint defaults to E
(continue/resume)") trips the exact same signals as a brief describing real
in-flight work -- observed live: brief 20260812-163747, whose own prose is a bug
report about this failure mode, scored Route E at high confidence (11) purely
from its own words. `classify.py` stays a pure text scorer by design and has no
way to tell those two cases apart from text alone.

This module answers the one question text can't: does *this specific claimed
brief* actually have resumable state right now? "Resumable" means either (a) an
in-flight run record (no `final_status`) referencing the brief whose `worktree`
path still exists on disk -- the same definition `worktrail-go`'s active-run-resume
guard already uses (subagent-prompts.md `#active-run-resume`) -- or (b) an open PR
referencing the brief. Feed the result into `classify.py --resumable-state` so a
confirmed-fresh claim can never land on E, mechanically, instead of relying on an
agent to notice (the gap worktrail-drain's unattended one-shots have no one to
catch).

Best-effort by design, same posture as `check_repo_freshness.py` and
`check_brief_staleness.py`. `checked: false` means the question is unanswered:
either the brief itself couldn't be read, or it carries no `repo:` frontmatter
AND no `--repo` override was supplied, so there was nothing to scan evidence
against -- the caller's contract treats that as "unknown", not as an
authoritative "not resumable" (worktrail-go's `/go` SKILL.md Phase 5 fails
open on `checked: false` rather than passing `--resumable-state` at all).
`gh` unavailable/unauthenticated/erroring, or no run-records directory, still
degrade to "not resumable" plus a warning under a `checked: true` result,
since those only affect one piece of evidence, not whether the check ran at
all.

A brief captured without `repo:` frontmatter (e.g. a meta-brief about
worktrail itself, or one hand-authored without `worktrail-resolve-repo`'s
hint-based resolution) used to fail open unconditionally -- silently
disabling this whole disqualification check for exactly the briefs that most
need it, since `--handoff-route`/`recommended-route: E` still reaches
`classify.py` unchallenged. `/go`'s SKILL.md already resolves the dispatch's
target repo in Phase 3, before this check ever runs in Phase 5 -- `--repo`
lets the caller pass that already-resolved path through as a fallback instead
of this module re-deriving it. Frontmatter still wins when present, since a
brief that names its own repo is more authoritative than the dispatch's
resolved target (they should normally agree, but the brief's own claim is
kept as the primary source of truth). The override does not change the
`resumable=False` contract in any way -- it only changes whether the check
runs at all when frontmatter is absent.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..shared.brief_frontmatter import read_frontmatter
from ..shared.homedir import worktrail_home
from .poll_run import read_run_record

# Bounded so an unresponsive `gh` never stalls dispatch.
DEFAULT_GH_TIMEOUT = 10


def _run_gh(repo: Path, args: List[str], timeout: int) -> Optional["subprocess.CompletedProcess[str]"]:
    try:
        return subprocess.run(
            ["gh", *args], capture_output=True, text=True, timeout=timeout, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _find_inflight_run_record(brief_id: str, repo: str, runs_dir: Path) -> Optional[Dict[str, Any]]:
    """Scan `runs_dir/<repo-name>/*.yaml` for a non-terminal record referencing
    `brief_id`. Returns `{"path": str, "worktree": str|None}` for the first
    match, or `None` if the directory is missing or nothing non-terminal
    references this brief. One unreadable record is skipped, not fatal."""
    repo_dir = runs_dir / Path(repo).name
    if not repo_dir.is_dir():
        return None
    for path in sorted(repo_dir.glob("*.yaml")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if brief_id not in content:
            continue
        record = read_run_record(path)
        if record.get("final_status") is not None:
            continue
        worktree = record.get("worktree")
        return {"path": str(path), "worktree": worktree if worktree else None}
    return None


def _find_open_pr(brief_id: str, repo: str, timeout: int) -> Optional[Dict[str, Any]]:
    """Best-effort `gh pr list --search <brief_id> --state open`. Returns the
    first match, or `None` on no match, `gh` unavailable, or any failure."""
    if not shutil.which("gh"):
        return None
    out = _run_gh(
        Path(repo),
        ["pr", "list", "--state", "open", "--search", brief_id,
         "--json", "number,title,url", "--limit", "5"],
        timeout,
    )
    if out is None or out.returncode != 0:
        return None
    try:
        items = json.loads(out.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(items, list) or not items:
        return None
    first = items[0]
    return first if isinstance(first, dict) else None


def check(
    brief_path: Path,
    runs_dir: Optional[Path] = None,
    do_gh: bool = True,
    gh_timeout: int = DEFAULT_GH_TIMEOUT,
    repo_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Does the claimed brief at `brief_path` have real resumable state?

    Returns `{"checked": bool, "resumable": bool, "evidence": {"run_record":
    str|None, "worktree": str|None, "open_pr": dict|None}, "warning": str|None}`.
    `checked=False` means the question is unanswered -- either the brief itself
    couldn't be read, or it carries no `repo:` frontmatter and no `repo_override`
    was given, so evidence-gathering had nothing to scan. Callers must treat
    `checked=False` as "unknown", never as "not resumable": `resumable` is only
    meaningful when `checked` is True. `resumable=True` requires either an
    in-flight run record whose worktree still exists on disk, or an open PR
    referencing the brief; a run record alone (worktree gone, e.g. already
    cleaned up post-merge) is surfaced as evidence but is not "resumable" on
    its own.

    `repo_override` is used only when the brief's own `repo:` frontmatter is
    absent -- the caller's already-resolved dispatch target repo (e.g. `/go`
    SKILL.md's Phase 3 `$REPO`), not a second source of truth to reconcile
    against the brief's own claim.
    """
    result: Dict[str, Any] = {
        "checked": False,
        "resumable": False,
        "evidence": {"run_record": None, "worktree": None, "open_pr": None},
        "warning": None,
    }
    brief_path = Path(brief_path)
    try:
        brief_path.stat()
    except OSError as exc:
        result["warning"] = f"could not read claimed brief {brief_path}: {exc!r}"
        return result

    frontmatter = read_frontmatter(brief_path)
    brief_id = str(frontmatter.get("id") or brief_path.stem)
    repo = frontmatter.get("repo") or repo_override

    if not repo:
        result["warning"] = (
            "brief has no repo: frontmatter and no --repo override was given -- "
            "resumable-state check skipped"
        )
        return result

    result["checked"] = True
    resolved_runs_dir = Path(runs_dir).expanduser() if runs_dir is not None else worktrail_home() / "runs"

    record = _find_inflight_run_record(brief_id, str(repo), resolved_runs_dir)
    if record is not None:
        result["evidence"]["run_record"] = record["path"]
        worktree = record.get("worktree")
        if worktree and Path(str(worktree)).expanduser().exists():
            result["evidence"]["worktree"] = str(worktree)

    if do_gh:
        pr = _find_open_pr(brief_id, str(repo), gh_timeout)
        if pr is not None:
            result["evidence"]["open_pr"] = pr

    result["resumable"] = bool(
        (result["evidence"]["run_record"] and result["evidence"]["worktree"])
        or result["evidence"]["open_pr"]
    )
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--brief", required=True, help="path to the claimed brief")
    p.add_argument("--dir", default=None, help="run records directory (default worktrail_home()/runs)")
    p.add_argument("--no-gh", action="store_true", help="skip the open-PR gh lookup")
    p.add_argument("--gh-timeout", type=int, default=DEFAULT_GH_TIMEOUT)
    p.add_argument(
        "--repo", default=None,
        help="fallback target repo, used only when the brief has no repo: frontmatter",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    res = check(
        Path(args.brief),
        runs_dir=Path(args.dir) if args.dir else None,
        do_gh=not args.no_gh,
        gh_timeout=args.gh_timeout,
        repo_override=args.repo,
    )
    if args.json:
        print(json.dumps(res))
    elif not res["checked"]:
        print(f"unknown: {res['warning']}")
    elif res["resumable"]:
        print(f"RESUMABLE: {res['evidence']}")
    else:
        line = "not resumable: no in-flight run record with existing worktree, no open PR"
        if res.get("warning"):
            line += f"\n  warning: {res['warning']}"
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
