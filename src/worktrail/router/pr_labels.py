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
import time
from collections.abc import Callable
from pathlib import Path

from .automerge_preflight import owner_repo_from_git
from .run_record import _load as load_run_record

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

_PR_URL_RE = re.compile(r"^https://github\.com/([^/]+)/([^/]+)/pull/(\d+)/?$")

# Transient TLS verification failures from `gh`'s Go client -- the GitHub API
# edge intermittently serves an untrusted/mismatched certificate on a subset
# of connections (reproduced ~1-in-8 to api.github.com, 2026-08-11; both Go and
# Python fail identically, github.com/other hosts never do). These are
# network flakiness, not a config or payload error, so a small bounded retry is
# safe. Every OTHER failure (422, auth, GraphQL drift, ...) is a real error and
# must NOT be retried -- a retry loop would mask it, not fix it.
_TRANSIENT_TLS_MARKERS = (
    "x509: certificate is not valid for any names",
    "failed to verify certificate",
    "certificate verify failed",
    "tls: failed to verify certificate",
)
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_BASE_S = 1.0


def _is_transient_tls(stderr: str) -> bool:
    return any(marker in stderr for marker in _TRANSIENT_TLS_MARKERS)


def _run_gh_cmd(cmd, repo, runner=None, timeout=30) -> subprocess.CompletedProcess[str]:
    """Run a `gh` subprocess, retrying (with backoff) ONLY on transient TLS
    failures. Non-transient failures and successes run once and return
    immediately. Returns the last CompletedProcess."""
    attempt = 0
    while True:
        result = (runner or subprocess.run)(
            cmd,
            capture_output=True,
            text=True,
            cwd=repo,
            timeout=timeout,
        )
        if result.returncode == 0 or not _is_transient_tls(result.stderr):
            return result
        attempt += 1
        if attempt >= _RETRY_ATTEMPTS:
            return result
        delay = _RETRY_BACKOFF_BASE_S * attempt
        print(
            f"warning: pr_labels: transient TLS failure on {cmd[0]} '...' "
            f"({result.stderr.strip()}); retrying in {delay:.1f}s "
            f"({attempt}/{_RETRY_ATTEMPTS})",
            file=sys.stderr,
        )
        time.sleep(delay)


def _parse_pr_url(pr_url: str) -> tuple[str, str, str] | None:
    """(owner, repo, issue_number) from a `https://github.com/OWNER/REPO/pull/N`
    URL, or None if it doesn't match that shape."""
    match = _PR_URL_RE.match(pr_url)
    if not match:
        return None
    owner, repo_name, number = match.groups()
    return owner, repo_name, number


def _owner_repo_number(
    repo: str, pr_url: str, runner: Runner | None = None
) -> tuple[str, str, str] | None:
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


def _add_label(
    repo: str, pr_url: str, label: str, runner: Runner | None = None
) -> str | None:
    """Add `label` to the PR via the REST issues-labels endpoint (see module
    docstring for why not `gh pr edit --add-label`). Returns `label` on
    success, None on any failure -- logging a warning to stderr first so a
    swallowed failure is never silent."""
    parsed = _owner_repo_number(repo, pr_url, runner)
    if parsed is None:
        print(
            f"warning: pr_labels: could not resolve owner/repo/number for "
            f"PR {pr_url!r}; skipping label add",
            file=sys.stderr,
        )
        return None
    owner, repo_name, number = parsed
    result = _run_gh_cmd(
        [
            "gh",
            "api",
            f"repos/{owner}/{repo_name}/issues/{number}/labels",
            "-X",
            "POST",
            "-f",
            f"labels[]={label}",
        ],
        repo,
        runner,
    )
    if result.returncode != 0:
        print(
            f"warning: pr_labels: failed to add label {label!r} to "
            f"{pr_url}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return label


def _remove_label(
    repo: str, pr_url: str, label: str, runner: Runner | None = None
) -> str | None:
    """Remove `label` from the PR via the REST issues-labels endpoint (same
    endpoint family as `_add_label`, for the same Projects-classic reason).
    Returns `label` on success, None on any failure -- logging a warning to
    stderr first so a swallowed failure is never silent."""
    parsed = _owner_repo_number(repo, pr_url, runner)
    if parsed is None:
        print(
            f"warning: pr_labels: could not resolve owner/repo/number for "
            f"PR {pr_url!r}; skipping label remove",
            file=sys.stderr,
        )
        return None
    owner, repo_name, number = parsed
    result = _run_gh_cmd(
        [
            "gh",
            "api",
            f"repos/{owner}/{repo_name}/issues/{number}/labels/{label}",
            "-X",
            "DELETE",
        ],
        repo,
        runner,
    )
    if result.returncode != 0:
        print(
            f"warning: pr_labels: failed to remove label {label!r} from "
            f"{pr_url}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    return label


def _current_pr_labels(
    repo: str, pr_url: str, runner: Runner | None = None
) -> list[str] | None:
    """Live label names on a PR, or None if the `gh` call fails or is
    unparseable (never guess from stale/absent data).

    `runner` defaults to a live lookup of `subprocess.run` at call time (not
    a def-time-bound default) so existing callers that monkeypatch
    `subprocess.run` globally keep working unchanged.
    """
    result = _run_gh_cmd(
        ["gh", "pr", "view", pr_url, "--json", "labels"],
        repo,
        runner,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return [label.get("name", "") for label in data.get("labels", [])]


def ensure_pr_risk_label(
    repo: str | None, pr_url: str | None, risk_level: str | None
) -> str | None:
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


def correct_pr_risk_label(
    repo: str | None,
    pr_url: str | None,
    risk_level: str | None,
    runner: Runner | None = None,
) -> str | None:
    """Ensure the PR carries exactly `go:risk-<risk_level>`, removing any
    OTHER `go:risk-*` label first.

    Deliberately separate from `ensure_pr_risk_label()`, not a behavior
    change to it: that function's "only add when none exists" posture is a
    pinned contract for its post-hoc-correction callers (drain.py, poll_run.py,
    reconcile_pr_labels.py, run_record.py's finish path) which run at a point
    where any existing label is presumed already correct or human-set, so
    changing it there risks silently removing a deliberately-set label for
    callers that were never reviewed against that risk. `land_pr()`'s
    existing-PR update path is different: it always has a freshly-computed,
    authoritative `risk_level` from THIS invocation's own preflight run, so a
    stale `go:risk-*` label there is provably wrong (a bug, not someone
    else's deliberate override) and should be corrected, not preserved.
    Never touches `go:no-automerge` -- same posture as `ensure_pr_risk_label`.
    """
    if not repo or not pr_url or not risk_level:
        return None
    target = f"go:risk-{risk_level}"
    labels = _current_pr_labels(repo, pr_url, runner=runner)
    if labels is None or target in labels:
        return None
    for stale in labels:
        if stale.startswith("go:risk-") and stale != target:
            _remove_label(repo, pr_url, stale, runner=runner)
    return _add_label(repo, pr_url, target, runner=runner)


def ensure_pr_no_automerge_label(
    repo: str | None, pr_url: str | None, eligible: bool, runner: Runner | None = None
) -> str | None:
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
        description="Apply the go:risk-* PR label post-hoc correction from a run record."
    )
    parser.add_argument("--run", required=True, help="Path to run record YAML file")
    args = parser.parse_args(argv)
    record = load_run_record(Path(args.run))
    pr_url = record.get("pull_request")
    applied = (
        ensure_pr_risk_label(record.get("repository"), pr_url, record.get("risk_level"))
        if pr_url
        else None
    )
    print(json.dumps({"applied": applied}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
