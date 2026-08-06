#!/usr/bin/env python3
"""reconcile_pr_labels.py — scheduled self-heal for drifted `go:risk-*` PR labels.

`pr_labels.py`'s `ensure_pr_risk_label()` only runs at the moment one specific
dispatch call site finishes: drain.py's queue-drain loop (PR #128), go's own
Phase 7 headless-dispatch poll-exit path (`poll_run.py`), and interactive/Codex
in-session dispatch (sdd-workflow's Phase 8, PR #137). Each fix closed one call
site; this is the 5th recurrence of the same failure class (#74/#80/#82/#128/
#137) because any *future* dispatch surface (a new agent CLI, a new headless
spawn shape, a new orchestrator entrypoint) can silently reintroduce the
identical gap by simply not calling the corrector.

This module closes the failure class structurally instead of catching another
call site: a periodic sweep across every worktrail-managed repo's open PRs,
reusing the exact same `ensure_pr_risk_label`/`_current_pr_labels` correction
every other call site already uses, so it is a safety net behind all of them
rather than a 6th place that also has to remember.

Risk level provenance: the correction only ever ADDS `go:risk-<level>` to a PR
carrying no `go:risk-*` label at all (see `pr_labels.py` docstring — never
removes/replaces, never touches `go:no-automerge`). The level itself comes
from the GO run record that produced the PR (`--repo`/`--request`/`--risk` at
Phase 6 `run_record.py start`), matched by the PR's URL — the same source
`pr_labels.py`'s CLI entrypoint already reads for a single run. A PR with no
matching run record (created outside GO, or whose run record was pruned) is
left alone and reported as `unreconciled`; this never guesses a risk level.

Usage:
  reconcile_pr_labels.py --repo /path/to/repo [--dir ~/.go/runs] [--dry-run] [--json]
  reconcile_pr_labels.py --repos-root ~/projects [--dir ~/.go/runs] [--dry-run] [--json]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .policy import POLICY_RELPATH
from .policy_selfcheck import discover_repo_names
from .pr_labels import ensure_pr_risk_label
from .run_record import _load as load_run_record


def discover_managed_repos(repos_root: Path) -> List[str]:
    """Every immediate subdirectory of `repos_root` that has a
    `docs/specs/go-policy.yaml` — i.e. has opted into GO/worktrail."""
    return [
        name for name in discover_repo_names(repos_root)
        if (repos_root / name / POLICY_RELPATH).is_file()
    ]


def _pr_urls_from_field(value: Any) -> List[str]:
    """Normalize a run record's `pull_request` field into 0+ PR URLs.

    Usually a scalar string, but a run that produces more than one PR (the
    parallel orchestrator's multi-repo group-PR path, or any run whose
    `pull_request` was appended to more than once) records it as a list —
    observed in production run records as bare URLs, `"<repo>: <url>"`
    entries, and empty-string placeholders. Extracting the URL substring
    from each handles all three without assuming a bare URL.
    """
    if not value:
        return []
    items = value if isinstance(value, list) else [value]
    urls = []
    for item in items:
        if not isinstance(item, str):
            continue
        idx = item.find("https://")
        if idx != -1:
            urls.append(item[idx:])
    return urls


def load_risk_index(runs_dir: Path) -> Dict[str, str]:
    """Map `pull_request` URL -> `risk_level` across every run record under
    `runs_dir`, recursively. A PR URL is globally unique, so this is keyed on
    it directly rather than on the (fragmented, sometimes worktree-basename)
    per-repo subdirectory layout `run_record.py` writes into.

    Records with no `risk_level` are skipped (nothing to index). On a
    duplicate PR URL across records, the later one wins (sorted path order)
    — not expected in practice, but no ambiguity if it happens.
    """
    index: Dict[str, str] = {}
    if not runs_dir.is_dir():
        return index
    for path in sorted(runs_dir.glob("**/*.yaml")):
        record = load_run_record(path)
        risk_level = record.get("risk_level")
        if not risk_level:
            continue
        for pr_url in _pr_urls_from_field(record.get("pull_request")):
            index[pr_url] = risk_level
    return index


def _open_prs(repo: Path) -> Optional[List[Dict[str, Any]]]:
    """Open PRs (url, labels) for repo's GitHub remote, via `gh pr list`.

    None on any `gh` failure (missing, unauthenticated, offline, no GitHub
    remote) — matches `_current_pr_labels()`'s own never-guess posture.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "list", "--state", "open", "--json", "url,labels",
             "--limit", "200"],
            capture_output=True, text=True, timeout=30, cwd=str(repo),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def reconcile_repo(repo: Path, risk_index: Dict[str, str], dry_run: bool) -> Dict[str, Any]:
    """Self-heal every open PR in `repo` missing a `go:risk-*` label. Empty
    `applied`/`unreconciled` = clean (either no open PRs, or all already
    labeled)."""
    result: Dict[str, Any] = {
        "repo": repo.name, "path": str(repo),
        "applied": [], "unreconciled": [], "checked": 0,
    }
    prs = _open_prs(repo)
    if prs is None:
        result["error"] = "gh pr list failed or unavailable"
        return result
    for pr in prs:
        pr_url = pr.get("url")
        if not pr_url:
            continue
        labels = [label.get("name", "") for label in pr.get("labels", [])]
        if any(label.startswith("go:risk-") for label in labels):
            continue
        result["checked"] += 1
        risk_level = risk_index.get(pr_url)
        if not risk_level:
            result["unreconciled"].append(pr_url)
            continue
        if dry_run:
            result["applied"].append({"pr": pr_url, "label": f"go:risk-{risk_level}",
                                       "dry_run": True})
            continue
        applied = ensure_pr_risk_label(str(repo), pr_url, risk_level)
        if applied:
            result["applied"].append({"pr": pr_url, "label": applied})
        else:
            result["unreconciled"].append(pr_url)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="single repo to reconcile")
    p.add_argument("--repos-root", help="sweep every go-policy.yaml repo under this directory")
    p.add_argument("--dir", default="~/.go/runs", help="GO run records directory")
    p.add_argument("--dry-run", action="store_true",
                    help="report drift without editing any PR")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not args.repo and not args.repos_root:
        p.error("one of --repo or --repos-root is required")

    runs_dir = Path(args.dir).expanduser()
    risk_index = load_risk_index(runs_dir)

    if args.repos_root:
        root = Path(args.repos_root).expanduser()
        if args.repo:
            targets = [Path(args.repo).expanduser()]
        else:
            targets = [root / name for name in discover_managed_repos(root)]
    else:
        targets = [Path(args.repo).expanduser()]

    results = [reconcile_repo(repo, risk_index, args.dry_run) for repo in targets]
    total_applied = sum(len(r["applied"]) for r in results)
    total_unreconciled = sum(len(r["unreconciled"]) for r in results)

    if args.json:
        print(json.dumps({"results": results, "applied": total_applied,
                           "unreconciled": total_unreconciled}, indent=2))
    else:
        for r in results:
            if r.get("error"):
                print(f"{r['repo']}: ERROR {r['error']}")
            elif r["applied"] or r["unreconciled"]:
                print(f"{r['repo']}: applied={len(r['applied'])} "
                      f"unreconciled={len(r['unreconciled'])}")
        print(f"reconcile_pr_labels: {total_applied} label(s) applied, "
              f"{total_unreconciled} PR(s) left unreconciled (no matching run record)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
