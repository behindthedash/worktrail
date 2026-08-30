#!/usr/bin/env python3
"""audit_postmerge.py — fleet-wide post-merge reconciliation audit.

`verify.py`'s `classify_checks()` only ever runs while a PR's own orchestrator
run is still live, polling that one PR's `statusCheckRollup` until it goes
green or the run gives up. Nothing re-checks a PR after it merges — a check
that starts failing *after* merge (a flaky re-run, a required check added to
the branch ruleset post-merge, a rollup that was still pending when the
orchestrator's own poll budget ran out and it merged anyway) is never
flagged again. This module closes that gap with a periodic sweep across
every worktrail-managed repo's recently-merged PRs, re-classifying each PR's
current `statusCheckRollup` with the exact same `classify_checks()` every
in-flight verify run already uses, so a merged-but-red PR surfaces the same
way a not-yet-merged one would.

Repo discovery reuses `reconcile_pr_labels.py`'s `discover_managed_repos()`
rather than re-implementing the "which repos has this machine opted into
GO/worktrail" scan a second time.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..orchestrator.verify import classify_checks
from ..shared.homedir import env_setting, worktrail_home
from .reconcile_pr_labels import discover_managed_repos

__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_MAX_PRS",
    "classify_checks",
    "dashboard_snapshot",
    "discover_managed_repos",
    "effective_since",
    "first_run_lookback",
    "list_merged_prs",
    "load_state",
    "main",
    "read_marker",
    "resolve_state_dir",
    "sweep_repo",
    "write_marker",
]

# Bounded first-run window: how far back a repo with no (or a corrupt)
# marker looks for merged PRs, so a brand-new/never-swept repo doesn't pull
# in a repo's entire merge history on its first sweep.
DEFAULT_LOOKBACK_DAYS = 7

# Per-repo per-sweep cap on how many merged PRs get a `gh pr view` rollup
# fetch, so one repo with a merge backlog can't starve every other repo's
# sweep in the same run.
DEFAULT_MAX_PRS = 50


def resolve_state_dir(cli_arg: str | None = None) -> Path:
    """`--state-dir` > `$WORKTRAIL_POSTMERGE_AUDIT_STATE` > `worktrail_home()/postmerge-audit-state`."""
    raw = cli_arg or env_setting("WORKTRAIL_POSTMERGE_AUDIT_STATE")
    if raw:
        return Path(raw).expanduser()
    return worktrail_home() / "postmerge-audit-state"


def first_run_lookback(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS, now: datetime | None = None
) -> str:
    """ISO8601 timestamp `lookback_days` before `now` (UTC) -- the window a
    repo with no persisted marker falls back to."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(days=lookback_days)).isoformat()


def _state_path(repo_name: str, state_dir: Path) -> Path:
    return state_dir / f"{repo_name}.json"


def load_state(repo_name: str, state_dir: Path) -> dict[str, Any]:
    """The repo's full persisted state dict. `{}` when the file is missing,
    unreadable, or not valid JSON -- a corrupt marker must degrade to the
    first-run lookback window rather than raise."""
    path = _state_path(repo_name, state_dir)
    try:
        raw = path.read_text()
    except OSError:
        return {}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return state if isinstance(state, dict) else {}


def read_marker(repo_name: str, state_dir: Path) -> str | None:
    """The repo's persisted `last_swept_at` ISO8601 string, or `None` if
    absent/corrupt (missing file, invalid JSON, wrong type, or a value that
    doesn't parse as ISO8601)."""
    value = load_state(repo_name, state_dir).get("last_swept_at")
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return None
    return value


def write_marker(repo_name: str, state_dir: Path, last_swept_at: str) -> None:
    """Persist `last_swept_at`, preserving any other keys already in the
    repo's state file (e.g. flagged-PR records written by a later sweep
    step) rather than clobbering the whole file."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(repo_name, state_dir)
    state["last_swept_at"] = last_swept_at
    _state_path(repo_name, state_dir).write_text(json.dumps(state, indent=2))


def effective_since(
    repo_name: str, state_dir: Path, lookback_days: int = DEFAULT_LOOKBACK_DAYS
) -> str:
    """The ISO8601 timestamp to sweep from: the repo's marker if present and
    valid, else the bounded first-run lookback window."""
    return read_marker(repo_name, state_dir) or first_run_lookback(lookback_days)


def _run_gh(args: list[str], repo: Path, timeout: float = 30) -> Any | None:
    """Run a `gh` subcommand in `repo` and parse its JSON stdout. `None` on
    any failure (missing, unauthenticated, offline, non-zero exit, non-JSON
    output) -- matches `reconcile_pr_labels.py`'s `_open_prs()` never-guess
    posture."""
    try:
        result = subprocess.run(
            ["gh", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def list_merged_prs(
    repo: Path, since: str, max_prs: int = DEFAULT_MAX_PRS
) -> list[dict[str, Any]] | None:
    """Merged PRs in `repo` merged at/after `since` (ISO8601), each carrying
    its live `statusCheckRollup`.

    Two `gh` calls per candidate PR: `gh pr list --state merged --search
    "merged:>=<since>"` finds which PRs merged in-window, capped at
    `max_prs` so one repo with a merge backlog can't starve every other
    repo's sweep in the same run; a per-PR `gh pr view ... --json
    url,number,mergedAt,statusCheckRollup` then re-fetches each one's
    current rollup, since a merged PR's checks can keep resolving/re-running
    after the listing call and `gh pr list` itself doesn't expose
    `statusCheckRollup`.

    `None` on any `gh` failure (missing, unauthenticated, offline, no GitHub
    remote, or a later `pr view` call failing partway through) -- matches
    `reconcile_pr_labels.py`'s `_open_prs()` fail-open posture: the caller
    reports `error` and leaves the repo's marker unchanged rather than
    advancing it past PRs it never actually got to classify.
    """
    listed = _run_gh(
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--search",
            f"merged:>={since}",
            "--json",
            "url,number,mergedAt",
            "--limit",
            str(max_prs),
        ],
        repo,
    )
    if not isinstance(listed, list):
        return None
    prs: list[dict[str, Any]] = []
    for item in listed[:max_prs]:
        number = item.get("number")
        if number is None:
            continue
        detail = _run_gh(
            [
                "pr",
                "view",
                str(number),
                "--json",
                "url,number,mergedAt,statusCheckRollup",
            ],
            repo,
        )
        if not isinstance(detail, dict):
            return None
        prs.append(detail)
    return prs


def _write_sweep_state(
    repo_name: str,
    state_dir: Path,
    last_swept_at: str | None,
    flagged_this_sweep: list[dict[str, Any]],
    checked_urls: set[str],
) -> None:
    """Merge this sweep's `flagged_this_sweep` into the persisted `flagged`
    list rather than replacing it outright: a PR whose URL is in
    `checked_urls` (fetched and re-classified this sweep) is fully
    superseded by this sweep's verdict -- present in `flagged_this_sweep` if
    still failing, absent (and therefore dropped) if it now passes -- while a
    previously-flagged PR that fell outside this sweep's fetch window is
    carried forward untouched, since it was never re-verified as resolved.
    Also persists the new marker when `last_swept_at` is given. Other keys
    already in the repo's state file are kept."""
    state_dir.mkdir(parents=True, exist_ok=True)
    state = load_state(repo_name, state_dir)
    if last_swept_at is not None:
        state["last_swept_at"] = last_swept_at
    existing = state.get("flagged")
    if not isinstance(existing, list):
        existing = []
    carried = [
        entry
        for entry in existing
        if not (isinstance(entry, dict) and entry.get("url") in checked_urls)
    ]
    state["flagged"] = carried + flagged_this_sweep
    _state_path(repo_name, state_dir).write_text(json.dumps(state, indent=2))


def sweep_repo(
    repo: Path,
    state_dir: Path,
    *,
    repo_name: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_prs: int = DEFAULT_MAX_PRS,
) -> dict[str, Any]:
    """Sweep `repo`'s merged PRs since its marker (or the first-run lookback
    window), classify each fetched `statusCheckRollup` with the exact same
    `classify_checks()` every in-flight verify run uses, and persist any
    flagged PRs plus the advanced marker.

    On a `gh` failure, `list_merged_prs()` returns `None`: this reports
    `error` and returns without touching the repo's state file at all --
    the marker must not advance past PRs this sweep never actually fetched,
    and a transient `gh` outage must not wipe out flags a previous sweep
    already recorded.

    On success, the marker only advances to the latest `mergedAt` among the
    PRs actually fetched this sweep (not to "now") -- when `--max-prs`
    truncates a merge backlog, the untouched remainder stays in-window for
    the next sweep instead of being silently skipped. When zero PRs were
    fetched (a clean window, not a failure), the marker is left as-is: there
    is nothing to advance past.
    """
    name = repo_name or repo.name
    result: dict[str, Any] = {
        "repo": name,
        "path": str(repo),
        "checked": 0,
        "flagged": [],
    }
    since = effective_since(name, state_dir, lookback_days)
    prs = list_merged_prs(repo, since, max_prs)
    if prs is None:
        result["error"] = "gh pr list/view failed or unavailable"
        return result

    result["checked"] = len(prs)
    flagged: list[dict[str, Any]] = []
    checked_urls: set = set()
    latest_merged_at: str | None = None
    for pr in prs:
        merged_at = pr.get("mergedAt")
        url = pr.get("url")
        checked_urls.add(url)
        _, failing = classify_checks(pr.get("statusCheckRollup"))
        if failing:
            flagged.append(
                {
                    "repo": name,
                    "url": url,
                    "failing_checks": failing,
                    "merged_at": merged_at,
                }
            )
        if isinstance(merged_at, str) and (
            latest_merged_at is None or merged_at > latest_merged_at
        ):
            latest_merged_at = merged_at

    result["flagged"] = flagged
    _write_sweep_state(name, state_dir, latest_merged_at, flagged, checked_urls)
    return result


def dashboard_snapshot(state_dir: Path) -> dict[str, Any]:
    """Pure read of every repo's persisted state file under `state_dir` into
    a summary dict for `dashboard.py` to fold in -- no `gh` calls, no
    network, no writes, safe to call from a hot dashboard-render path.

    `{"repos_flagged": 0, "prs_flagged": 0, "flagged": []}` when `state_dir`
    doesn't exist yet (no sweep has ever run) or every persisted state file
    has an empty/absent `flagged` list (fleet is clean). Reuses `load_state`
    per file so a corrupt state file degrades to being skipped rather than
    raising, same as a corrupt marker does for `sweep_repo`.
    """
    summary: dict[str, Any] = {"repos_flagged": 0, "prs_flagged": 0, "flagged": []}
    if not state_dir.is_dir():
        return summary
    for path in sorted(state_dir.glob("*.json")):
        flagged = load_state(path.stem, state_dir).get("flagged")
        if not isinstance(flagged, list) or not flagged:
            continue
        summary["repos_flagged"] += 1
        summary["prs_flagged"] += len(flagged)
        summary["flagged"].extend(flagged)
    return summary


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="single repo to sweep")
    p.add_argument(
        "--repos-root",
        help="sweep every worktrail-go-policy.yaml repo under this directory",
    )
    p.add_argument(
        "--state-dir",
        help="persisted per-repo marker/flag state directory "
        "(default: $WORKTRAIL_POSTMERGE_AUDIT_STATE or ~/.worktrail/postmerge-audit-state)",
    )
    p.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        help="first-run window for a repo with no persisted marker",
    )
    p.add_argument(
        "--max-prs",
        type=int,
        default=DEFAULT_MAX_PRS,
        help="per-repo per-sweep cap on merged PRs fetched",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not args.repo and not args.repos_root:
        p.error("one of --repo or --repos-root is required")

    state_dir = resolve_state_dir(args.state_dir)

    if args.repos_root:
        root = Path(args.repos_root).expanduser()
        if args.repo:
            targets = [Path(args.repo).expanduser()]
        else:
            targets = [root / name for name in discover_managed_repos(root)]
    else:
        targets = [Path(args.repo).expanduser()]

    results = [
        sweep_repo(
            repo, state_dir, lookback_days=args.lookback_days, max_prs=args.max_prs
        )
        for repo in targets
    ]
    total_checked = sum(r["checked"] for r in results)
    total_flagged = sum(len(r["flagged"]) for r in results)

    if args.json:
        print(
            json.dumps(
                {
                    "results": results,
                    "checked": total_checked,
                    "flagged": total_flagged,
                },
                indent=2,
            )
        )
    else:
        for r in results:
            if r.get("error"):
                print(f"{r['repo']}: ERROR {r['error']}")
            elif r["flagged"]:
                print(
                    f"{r['repo']}: checked={r['checked']} flagged={len(r['flagged'])}"
                )
        print(
            f"audit_postmerge: {total_checked} PR(s) checked, "
            f"{total_flagged} PR(s) flagged with failing checks"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
