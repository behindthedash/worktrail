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

Risk level provenance: the `go:risk-<level>` correction only ever ADDS the
label to a PR carrying no `go:risk-*` label at all (see `pr_labels.py`
docstring — never removes/replaces). The level comes from the GO run record
that produced the PR (`--repo`/`--request`/`--risk` at Phase 6
`run_record.py start`), matched by the PR's URL — the same source
`pr_labels.py`'s CLI entrypoint already reads for a single run. A PR with no
matching run record (created outside GO, or whose run record was pruned) is
left alone and reported as `unreconciled`; this never guesses a risk level.

`go:no-automerge` provenance: this sweep also recomputes full
`policy.automerge_eligible()` per PR — risk, `gates`, and `route` from the
matching run record, plus the repo's own `go-policy.yaml` and the PR's live
base branch — and ADDS `go:no-automerge` when ineligible and the label isn't
already present. Never removed once present (same additive posture as
`go:risk-*`). This recompute is only possible for run records that persisted
a `gates` field: an older record (started before `run_record.py start`
gained `--gates`) has no `gates` key at all, which is deliberately NOT
treated as "no gates" (that would let a PR that actually failed a gate read
as falsely eligible) — such a PR only gets the `go:risk-*` correction, same
as before this recompute existed.

Usage:
  reconcile_pr_labels.py --repo /path/to/repo [--dir ~/.worktrail/runs] [--dry-run] [--json]
  reconcile_pr_labels.py --repos-root ~/projects [--dir ~/.worktrail/runs] [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..shared.homedir import worktrail_home
from .policy import automerge_eligible, has_policy_file, load_policy
from .policy_selfcheck import discover_repo_names
from .pr_labels import ensure_pr_no_automerge_label, ensure_pr_risk_label
from .run_record import _load as load_run_record


def discover_managed_repos(repos_root: Path) -> list[str]:
    """Every immediate subdirectory of `repos_root` that has a
    `docs/specs/worktrail-go-policy.yaml` (or the pre-rename `go-policy.yaml`)
    — i.e. has opted into GO/worktrail."""
    return [
        name
        for name in discover_repo_names(repos_root)
        if has_policy_file(repos_root / name)
    ]


def _pr_urls_from_field(value: Any) -> list[str]:
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


def load_run_index(runs_dir: Path) -> dict[str, dict[str, Any]]:
    """Map `pull_request` URL -> `{risk_level, gates, route}` across every run
    record under `runs_dir`, recursively. A PR URL is globally unique, so
    this is keyed on it directly rather than on the (fragmented, sometimes
    worktree-basename) per-repo subdirectory layout `run_record.py` writes
    into.

    Records with no `risk_level` are skipped (nothing to index — every
    correction below needs at least a risk level). `gates` is `None` when
    the record predates `run_record.py start --gates` (see module docstring
    — this must never be treated as "no gates"). `route` is `selected_route`,
    always present on any real record. On a duplicate PR URL across records,
    the later one wins (sorted path order) — not expected in practice, but
    no ambiguity if it happens.
    """
    index: dict[str, dict[str, Any]] = {}
    if not runs_dir.is_dir():
        return index
    for path in sorted(runs_dir.glob("**/*.yaml")):
        record = load_run_record(path)
        risk_level = record.get("risk_level")
        if not risk_level:
            continue
        gates = record.get("gates")
        if not isinstance(gates, list):
            gates = None
        entry = {
            "risk_level": risk_level,
            "gates": gates,
            "route": record.get("selected_route"),
        }
        for pr_url in _pr_urls_from_field(record.get("pull_request")):
            index[pr_url] = entry
    return index


def _open_prs(repo: Path) -> list[dict[str, Any]] | None:
    """Open PRs (url, labels, baseRefName) for repo's GitHub remote, via
    `gh pr list`.

    None on any `gh` failure (missing, unauthenticated, offline, no GitHub
    remote) — matches `_current_pr_labels()`'s own never-guess posture.
    """
    try:
        result = subprocess.run(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "url,labels,baseRefName",
                "--limit",
                "200",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
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


def reconcile_repo(
    repo: Path, run_index: dict[str, dict[str, Any]], dry_run: bool
) -> dict[str, Any]:
    """Self-heal every open PR in `repo` missing a `go:risk-*` label, or
    missing a `go:no-automerge` label a full `automerge_eligible()` recompute
    says it should carry (only possible for run records that persisted
    `gates` — see module docstring). Empty `applied`/`unreconciled` = clean
    (either no open PRs, or all already reconciled)."""
    result: dict[str, Any] = {
        "repo": repo.name,
        "path": str(repo),
        "applied": [],
        "unreconciled": [],
        "checked": 0,
    }
    prs = _open_prs(repo)
    if prs is None:
        result["error"] = "gh pr list failed or unavailable"
        return result
    policy: dict[str, Any] | None = None
    for pr in prs:
        pr_url = pr.get("url")
        if not pr_url:
            continue
        labels = [label.get("name", "") for label in pr.get("labels", [])]
        missing_risk = not any(label.startswith("go:risk-") for label in labels)
        has_no_automerge = "go:no-automerge" in labels
        entry = run_index.get(pr_url)

        wants_no_automerge = False
        if entry is not None and not has_no_automerge and entry["gates"] is not None:
            if policy is None:
                policy = load_policy(repo)
            eligible, _ = automerge_eligible(
                policy,
                entry["risk_level"],
                entry["gates"],
                pr.get("baseRefName") or "main",
                route=entry.get("route"),
            )
            wants_no_automerge = not eligible

        if not missing_risk and not wants_no_automerge:
            continue
        result["checked"] += 1
        if entry is None:
            result["unreconciled"].append(pr_url)
            continue

        applied_labels: list[str] = []
        failed = False
        if missing_risk:
            if dry_run:
                applied_labels.append(f"go:risk-{entry['risk_level']}")
            else:
                applied = ensure_pr_risk_label(str(repo), pr_url, entry["risk_level"])
                if applied:
                    applied_labels.append(applied)
                else:
                    failed = True
        if wants_no_automerge:
            if dry_run:
                applied_labels.append("go:no-automerge")
            else:
                applied = ensure_pr_no_automerge_label(
                    str(repo), pr_url, eligible=False
                )
                if applied:
                    applied_labels.append(applied)
                else:
                    failed = True

        for label in applied_labels:
            entry_result = {"pr": pr_url, "label": label}
            if dry_run:
                entry_result["dry_run"] = True
            result["applied"].append(entry_result)
        if failed:
            result["unreconciled"].append(pr_url)
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", help="single repo to reconcile")
    p.add_argument(
        "--repos-root", help="sweep every go-policy.yaml repo under this directory"
    )
    p.add_argument(
        "--dir",
        default=None,
        help="GO run records directory (default worktrail_home()/runs)",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="report drift without editing any PR"
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    if not args.repo and not args.repos_root:
        p.error("one of --repo or --repos-root is required")

    runs_dir = Path(args.dir).expanduser() if args.dir else worktrail_home() / "runs"
    run_index = load_run_index(runs_dir)

    if args.repos_root:
        root = Path(args.repos_root).expanduser()
        if args.repo:
            targets = [Path(args.repo).expanduser()]
        else:
            targets = [root / name for name in discover_managed_repos(root)]
    else:
        targets = [Path(args.repo).expanduser()]

    results = [reconcile_repo(repo, run_index, args.dry_run) for repo in targets]
    total_applied = sum(len(r["applied"]) for r in results)
    total_unreconciled = sum(len(r["unreconciled"]) for r in results)

    if args.json:
        print(
            json.dumps(
                {
                    "results": results,
                    "applied": total_applied,
                    "unreconciled": total_unreconciled,
                },
                indent=2,
            )
        )
    else:
        for r in results:
            if r.get("error"):
                print(f"{r['repo']}: ERROR {r['error']}")
            elif r["applied"] or r["unreconciled"]:
                print(
                    f"{r['repo']}: applied={len(r['applied'])} "
                    f"unreconciled={len(r['unreconciled'])}"
                )
        print(
            f"reconcile_pr_labels: {total_applied} label(s) applied, "
            f"{total_unreconciled} PR(s) left unreconciled (no matching run record)"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
