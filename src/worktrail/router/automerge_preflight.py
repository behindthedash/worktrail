#!/usr/bin/env python3
"""automerge_preflight.py — live required-checks gate for GO's auto-merge label.

`policy.automerge_eligible()` is deliberately deterministic and offline: it
answers "does this repo's *policy* allow automerge" from `worktrail-go-policy.yaml`
alone. It cannot see whether the base branch's GitHub-side protection
actually enforces anything. That gap let GO mark a PR automerge-eligible on
a repo whose base branch had zero required status checks (continuum PR #4:
`automerge.enabled: true`, `gh pr merge --auto --squash` merged the PR
instantly with no required check to wait on; `allow_auto_merge` was even
`false`, which makes `gh pr merge --auto` fall back to an immediate merge
instead of erroring). The repo-side gap is fixed per-repo by adding a
required check (brief 20260719-234147), but GO itself still trusted
`automerge.enabled: true` blindly for every other repo.

This module is the live half of the gate: it queries the base branch's
*actual* GitHub-side protection before GO calls a PR automerge-eligible.
`GET /repos/{owner}/{repo}/rules/branches/{branch}` returns the branch's
effective rules from repository rulesets (verified live against
behindthedash/continuum and briankudera/developer-kit, both ruleset-protected)
and GitHub represents classic branch protection the same way, so one call
covers both mechanisms.

Refuses by default (matches `policy.py`'s "never let an unknown state widen
autonomy" posture): any unresolvable signal -- no GitHub remote, `gh api`
failure, zero required check contexts, or `allow_auto_merge` disabled --
means "not eligible", never "assume yes."

Usage: automerge_preflight.py --repo /path/to/repo --branch main [--json]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Sleeper = Callable[[float], None]

# Reason-string markers for a failed *read* (gh api unreachable/erroring after
# retries), as opposed to a confirmed policy state (zero required checks,
# allow_auto_merge disabled, unresolved git remote). See
# `is_preflight_query_error` for how callers use this distinction.
_QUERY_ERROR_MARKER = "gh api failed"


def owner_repo_from_git(repo: Path, runner: Runner = subprocess.run) -> str | None:
    """`owner/repo` parsed from the `origin` remote, or None if unresolvable.

    Handles both the HTTPS form (`https://github.com/owner/repo.git`) and the
    SCP-like SSH form (`git@github.com:owner/repo.git`) -- the latter has no
    slash after the host, so splitting on a literal "github.com/" (as
    parallel-orchestrator/scripts/verify.py's `_derive_gh_repo` does) silently
    fails to strip the host for SSH remotes; splitting on bare "github.com"
    and stripping both separators handles either form.
    """
    result = runner(
        ["git", "remote", "get-url", "origin"],
        cwd=str(repo),
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    url = result.stdout.strip().rstrip("/").removesuffix(".git")
    if "github.com" not in url:
        return None
    return url.split("github.com", 1)[-1].lstrip(":/")


def required_status_check_contexts(
    owner_repo: str, branch: str, runner: Runner = subprocess.run
) -> list[str] | None:
    """Required status check contexts for `branch`, or None if the query failed.

    An empty list is a real, meaningful answer: the branch has zero required
    checks configured. That is distinct from None (the query itself failed),
    which the caller must also treat as "not eligible" -- see module docstring.
    """
    result = runner(
        ["gh", "api", f"repos/{owner_repo}/rules/branches/{branch}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    try:
        rules = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    contexts: list[str] = []
    for rule in rules:
        if rule.get("type") != "required_status_checks":
            continue
        for check in rule.get("parameters", {}).get("required_status_checks", []):
            context = check.get("context")
            if context:
                contexts.append(context)
    return contexts


def repo_settings(
    owner_repo: str, runner: Runner = subprocess.run
) -> dict[str, Any] | None:
    """`GET /repos/{owner}/{repo}` as a dict, or None if the query failed."""
    result = runner(
        ["gh", "api", f"repos/{owner_repo}"], capture_output=True, text=True
    )
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def is_preflight_query_error(reason: str) -> bool:
    """True when a `required_checks_gate` failure `reason` reflects a failed
    *read* (gh api unreachable/erroring after retries), not a confirmed
    policy state (zero required checks, allow_auto_merge disabled, or an
    unresolved git remote).

    Callers may treat a query-error reason as "unknown, not confirmed-unsafe"
    and fall back to `gh pr merge --auto` -- GitHub itself still enforces
    required checks/rulesets at merge time even when this preflight couldn't
    read them -- instead of blocking (or quarantining) an otherwise-green PR
    over a transient read failure. Reserve blocking for the confirmed-unsafe
    states, where proceeding would be a real regression, not a retry away.
    """
    return _QUERY_ERROR_MARKER in reason


def required_checks_gate(
    repo: Path,
    branch: str,
    runner: Runner = subprocess.run,
    retries: int = 3,
    backoff_seconds: float = 2.0,
    sleep: Sleeper = time.sleep,
) -> tuple[bool, str]:
    """True only when `branch` has at least one required status check AND the
    repo allows auto-merge. Fails closed on every unresolvable signal.

    The two `gh api` reads (required-status-check contexts, repo settings)
    are each retried up to `retries` times with linear backoff before being
    treated as failed -- a single flaky call must not immediately read as a
    confirmed "zero protection" state. If a read still fails after all
    retries, the returned reason carries the `is_preflight_query_error`
    marker so callers can distinguish "we couldn't tell" from "we checked and
    it's unsafe."
    """
    owner_repo = owner_repo_from_git(repo, runner)
    if owner_repo is None:
        return False, "could not resolve a GitHub owner/repo from the git origin remote"

    contexts = None
    for attempt in range(retries):
        contexts = required_status_check_contexts(owner_repo, branch, runner)
        if contexts is not None:
            break
        if attempt < retries - 1:
            sleep(backoff_seconds * (attempt + 1))
    if contexts is None:
        return False, (
            f"could not query required status checks for {owner_repo}@{branch} "
            f"({_QUERY_ERROR_MARKER} after {retries} attempts)"
        )
    if not contexts:
        return False, (
            f"{owner_repo}@{branch} has zero required status checks -- gh pr merge --auto "
            "(native or workflow-driven) would merge with no validation waited on. Add a "
            "required check via a ruleset (.github/rulesets/protect-<branch>.json) first."
        )

    settings = None
    for attempt in range(retries):
        settings = repo_settings(owner_repo, runner)
        if settings is not None:
            break
        if attempt < retries - 1:
            sleep(backoff_seconds * (attempt + 1))
    if settings is None:
        return False, (
            f"could not query repo settings for {owner_repo} "
            f"({_QUERY_ERROR_MARKER} after {retries} attempts)"
        )
    if not settings.get("allow_auto_merge"):
        return False, (
            f"{owner_repo} has allow_auto_merge=false -- gh pr merge --auto falls back to an "
            f"immediate merge instead of waiting on required checks ({', '.join(contexts)}). "
            "Enable 'Allow auto-merge' in repo settings."
        )

    return (
        True,
        f"required checks present ({', '.join(contexts)}); allow_auto_merge enabled",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", required=True)
    p.add_argument("--branch", default="main")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    ok, reason = required_checks_gate(Path(args.repo).expanduser(), args.branch)
    if args.json:
        print(json.dumps({"ok": ok, "reason": reason}, indent=2))
    else:
        print(f"{'PASS' if ok else 'FAIL'}: {reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
