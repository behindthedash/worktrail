#!/usr/bin/env python3
"""Post-hoc `go:risk-*` PR label correction, shared across every headless
one-shot spawn -- not just drain.py's queue-drain loop.

A spawned agent process (`claude -p` / `codex exec` / `opencode run`) issues
its own `gh pr create` -- a raw subprocess call, never reachable by the
Claude Code PreToolUse label-enforcement hook (behindthedash/devops#71):
Codex/OpenCode have no equivalent hook mechanism at all, and even a headless
`claude -p` session is not guaranteed to load the interactive hook config.
If that PR skips Phase 8's labeling step, `automerge_eligibility.sh`'s
fail-closed check (worktrail PR #107) stalls a PR that should have been
eligible -- see docs/specs/research/classify-gate-enforcement-audit.md and
docs/specs/research/go-dispatch-one-shot-pr-label-gap.md.

Originally defined only in drain/drain.py (worktrail PR #128), which applies
it after its own spawned one-shot exits. Extracted here so go's own Phase 7
headless-dispatch poll-exit path (router/poll_run.py) and the CLI entrypoint
below (invoked from sdd-workflow's own Phase 8, covering interactive Claude
and Codex in-session dispatch, which never go through poll_run.py at all)
can apply the identical correction.

`gh pr edit <pr_url> --add-label <label>` routes through a GraphQL mutation
that also touches the PR's classic-Projects fields. On a repo/org with a
legacy Projects (classic) board still attached, that mutation fails outright
("Projects (classic) is being deprecated") -- confirmed live 2026-08-07 on
behindthedash/devops during PR #124's auto-merge label application, where
`gh api repos/OWNER/REPO/issues/N/labels -X POST` (the REST endpoint, no
Projects-classic fields in its payload) worked as a manual fallback. The two
label-add call sites below use that REST endpoint instead of `gh pr edit` for
this reason -- `_current_pr_labels`'s read-only `gh pr view` is unaffected and
unchanged.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .automerge_preflight import owner_repo_from_git
from .run_record import _load as load_run_record

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

_PR_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")


def _parse_pr_url(pr_url: str) -> Optional[Tuple[str, str, str]]:
    """(owner, repo, issue_number) from a `https://github.com/OWNER/REPO/pull/N`
    URL, or None if it doesn't match that shape."""
    match = _PR_URL_RE.match(pr_url)
    if not match:
        return None
    owner, repo_name, number = match.groups()
    return owner, repo_name, number


def _owner_repo_number(repo: str, pr_url: str,
                       runner: Optional[Runner] = None) -> Optional[Tuple[str, str, str]]:
    """(owner, repo, issue_number) from either shape callers pass as `pr_url`:
    a full `https://github.com/OWNER/REPO/pull/N` URL (`poll_run.py`,
    `drain.py`, `reconcile_pr_labels.py`, this module's own `main()`), or a
    bare PR number (`check_review_threads.py` -- `gh pr edit <number>` needs
    no owner/repo since it resolves the repo from `cwd`, but the REST
    endpoint below always needs both explicitly). For the bare-number case,
    owner/repo is resolved from `repo`'s own `origin` remote."""
    parsed = _parse_pr_url(pr_url)
    if parsed is not None:
        return parsed
    if not pr_url.strip().isdigit():
        return None
    owner_repo = owner_repo_from_git(Path(repo), runner or subprocess.run)
    if owner_repo is None or "/" not in owner_repo:
        return None
    owner, repo_name = owner_repo.split("/", 1)
    return owner, repo_name, pr_url.strip()


def _add_label(repo: str, pr_url: str, label: str,
               runner: Optional[Runner] = None) -> Optional[str]:
    """Add `label` to the PR via the REST issues-labels endpoint (see module
    docstring for why not `gh pr edit --add-label`). Returns `label` on
    success, None on any failure -- logging a warning to stderr first so a
    swallowed failure is never silent."""
    parsed = _owner_repo_number(repo, pr_url, runner)
    if parsed is None:
        print(f"warning: pr_labels: could not resolve owner/repo/number for "
              f"PR {pr_url!r}; skipping label add", file=sys.stderr)
        return None
    owner, repo_name, number = parsed
    result = (runner or subprocess.run)(
        ["gh", "api", f"repos/{owner}/{repo_name}/issues/{number}/labels",
         "-X", "POST", "-f", f"labels[]={label}"],
        capture_output=True, text=True, cwd=repo, timeout=30,
    )
    if result.returncode != 0:
        print(f"warning: pr_labels: failed to add label {label!r} to "
              f"{pr_url}: {result.stderr.strip()}", file=sys.stderr)
        return None
    return label


def _current_pr_labels(repo: str, pr_url: str, runner: Optional[Runner] = None) -> Optional[List[str]]:
    """Live label names on a PR, or None if the `gh` call fails or is
    unparseable (never guess from stale/absent data).

    `runner` defaults to a live lookup of `subprocess.run` at call time (not
    a def-time-bound default) so existing callers that monkeypatch
    `subprocess.run` globally keep working unchanged.
    """
    result = (runner or subprocess.run)(
        ["gh", "pr", "view", pr_url, "--json", "labels"],
        capture_output=True, text=True, cwd=repo, timeout=30,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return [label.get("name", "") for label in data.get("labels", [])]


def ensure_pr_risk_label(repo: Optional[str], pr_url: Optional[str],
                         risk_level: Optional[str]) -> Optional[str]:
    """Add a go:risk-<level> label to a PR that carries none.

    This is a minimal, safe correction, not a re-run of `automerge_eligible()`:
    the run record does not persist `gates`, so full eligibility can't be
    reconstructed here. It only ADDS `go:risk-<risk_level>` when the PR
    carries NO go:risk-* label at all, using the risk already recorded at
    Phase 6 start; it never touches go:no-automerge, which an agent may have
    legitimately added itself and must never be silently removed.
    """
    if not repo or not pr_url or not risk_level:
        return None
    labels = _current_pr_labels(repo, pr_url)
    if labels is None or any(label.startswith("go:risk-") for label in labels):
        return None
    return _add_label(repo, pr_url, f"go:risk-{risk_level}")


def ensure_pr_no_automerge_label(repo: Optional[str], pr_url: Optional[str],
                                 eligible: bool, runner: Optional[Runner] = None) -> Optional[str]:
    """Add a `go:no-automerge` label to a PR that carries none but is
    ineligible -- either per a full `automerge_eligible()` recompute the
    caller already performed (needs `gates`, which only run records
    persisting the classifier's gates array can supply -- see
    `reconcile_pr_labels.py`), or per any other caller-confirmed ineligible
    state, e.g. `check_review_threads.check()` finding unresolved unaddressed
    review threads (native `gh pr merge --auto` has no concept of
    reviewThreads and would otherwise race ahead of that gate).

    Same one-directional, additive posture as `ensure_pr_risk_label()`: it
    only ADDS the label when missing and ineligible. It never removes an
    existing `go:no-automerge` -- a human or agent may have added it
    deliberately for a reason policy doesn't model, and silently removing it
    would be a much worse failure mode than leaving a false positive in place.
    """
    if not repo or not pr_url or eligible:
        return None
    labels = _current_pr_labels(repo, pr_url, runner=runner)
    if labels is None or "go:no-automerge" in labels:
        return None
    return _add_label(repo, pr_url, "go:no-automerge", runner=runner)


def main(argv=None) -> int:
    """CLI entrypoint: apply the correction using a run record's own
    `repository` / `pull_request` / `risk_level` fields -- the same fields
    Phase 6 already recorded, no new plumbing required.

    Usage: worktrail-ensure-pr-label --run /path/to/run.yaml

    Intended for sdd-workflow's own Phase 8, immediately after
    `run_record.py finish --pr <url> ...`, so interactive and Codex
    in-session dispatch (which never spawn a subprocess for poll_run.py to
    observe) get the same correction poll_run.py applies for headless
    Claude/OpenCode workers. A no-op (prints `{"applied": null}`) when there
    is no PR, no recorded risk level, or the PR already carries a
    go:risk-* label.
    """
    parser = argparse.ArgumentParser(
        description="Apply the go:risk-* PR label post-hoc correction from a run record.")
    parser.add_argument("--run", required=True, help="Path to run record YAML file")
    args = parser.parse_args(argv)
    record = load_run_record(Path(args.run))
    pr_url = record.get("pull_request")
    applied = (ensure_pr_risk_label(record.get("repository"), pr_url, record.get("risk_level"))
               if pr_url else None)
    print(json.dumps({"applied": applied}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
