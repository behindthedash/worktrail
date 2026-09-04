#!/usr/bin/env python3
"""The one shared PR-landing pipeline: commit, compile-marker gate, preflight
gate + labels, push, create/update the PR, watch CI to a terminal outcome,
and finish (or checkpoint) the run record.

Every current PR-opening call site (`queue_triage.py`, `drain.py`,
`orchestrator/integrate.py`'s group-PR step, and the agent-executed prose in
`worktrail-sdd-workflow`/`worktrail-go`) reimplements a subset of this
sequence, each missing a different piece -- see `design.md`'s inventory
table. `land_pr()` is the single implementation every caller composes with
instead, so a fix here (e.g. the compile-marker gap PR #902 hit) closes for
every caller at once rather than needing a matching patch per call site.

Ordered steps (design.md D2 -- this docstring is the authoritative list; the
executable order differs from the request's own step numbering because
`worktrail-preflight run` refuses on a dirty tree, and the compile marker
itself is an uncommitted file until step 1 commits it):

1. `_commit_pending` -- commit pending work if the tree is dirty (refuses
   without a `commit_message`; PR #902 shipped 112 files of gate-verified
   fixes that were never committed before `git push`).
2. `_ensure_compile_markers` -- for every OpenSpec change whose `tasks.md`
   changed relative to `base_branch`, compile it in-process and require a
   fresh `.compile-ok` marker (PR #902's root cause: a stale/missing marker
   reached `gh pr create` undetected).
3. `_run_preflight_and_labels` -- run the pre-PR gate in-process; labels are
   read back from the pass marker so they are byte-identical to what the
   PreToolUse hook will independently check.
4. `_push` -- push the (now clean, gate-passed) branch.
5. `open_or_update_pull_request` -- find or create the PR; ensure labels on
   an existing OPEN PR rather than re-creating it (idempotent re-invocation).
6. `_ensure_run_record` -- start a run record for a caller that has none
   (queue-triage, drain), or reuse the caller's; record the PR immediately
   so a crash mid-watch still leaves it discoverable.
7. `_watch_ci` -> `_merge_state_guard` -> `_review_thread_gate` ->
   `_finish_or_checkpoint` -- watch CI to a terminal outcome, guard the
   merge state and review threads, then finish the run record (or, in
   checkpoint mode, append a decision instead of finishing).

Refusal (steps 1-4) never touches the remote: no commit beyond the local
tree, no push, no PR -- a failed push attempt (step 4) is included because a
failed push, by definition, put nothing new on the remote. A step 5 PR-create
failure is different: it always follows a successful push, so the remote is
already mutated and a plain `refused` would misrepresent that; it is reported
as `ceiling` instead (needs reconciliation, not a clean retry). A code defect
or blocked review-thread gate (step 7) leaves the PR open and the run record
unfinished -- the caller's turn to repair and re-invoke with the same `run`
path.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from worktrail.conductor import compile as conductor_compile

from . import check_compile_markers, check_review_threads, pr_labels, preflight
from . import run_record as run_record_module
from .run_record import _load as _load_run_record

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

# `gh pr checks --watch` re-issue budget (ci-watch-loop.md's own discipline):
# a watch that times out is re-issued, not treated as a settled failure,
# until the budget below is spent.
WATCH_REISSUE_MAX = 3

# `gh run rerun --failed` budget for a transient infra failure (case 2) --
# bounded so a persistently-flaky runner doesn't loop forever.
TRANSIENT_RERUN_MAX = 3

# `gh run rerun <id>` budget for the merge-state guard's CANCELLED/SUCCESS
# same-name pair (design.md D7) -- distinct from TRANSIENT_RERUN_MAX because
# it fires post-watch, against a specific check pair, not the whole run.
MERGE_STATE_RERUN_MAX = 2

# `ci_patch_iterations` value at which a new code defect becomes a ceiling
# (`failed_recoverable`) instead of another `code_defect` outcome.
CI_PATCH_ITERATION_CEILING = 5

# Failing check names/log excerpts that mean "the runner infra hiccuped",
# never "the patch is wrong" -- ci-watch-loop.md case 2.
_TRANSIENT_CHECK_NAME_MARKERS = ("Initialize containers", "Set up job")
_TRANSIENT_LOG_MARKERS = ("Error response from daemon",)

_LOG_EXCERPT_LINES = 200

_EXIT_LANDED = 0
_EXIT_REFUSED = 2
_EXIT_CODE_DEFECT = 3
_EXIT_CEILING = 4


@dataclass(frozen=True)
class LandRequest:
    """Everything `land_pr()` needs to land one PR. See module docstring for
    the step each field feeds."""

    repo: str
    base_branch: str
    title: str
    summary: str
    route: str
    risk: str = "low"
    gates: list[str] = field(default_factory=list)
    run: str | None = None
    request_summary: str = ""
    spec_lineage: str = ""
    commit_message: str | None = None
    checkpoint: bool = False
    watch_timeout_s: int = 600
    runner: Runner = subprocess.run


@dataclass(frozen=True)
class LandOutcome:
    """`outcome` is one of `landed`, `code_defect`, `review_threads_blocking`,
    `ceiling`, `refused`. See module docstring step 7 for how each arises."""

    outcome: str
    pr_url: str | None = None
    pr_number: int | None = None
    labels: list[str] = field(default_factory=list)
    run: str | None = None
    final_status: str | None = None
    merge_result: str | None = None
    failing_checks: list[str] = field(default_factory=list)
    log_excerpt: str | None = None
    patch_iteration: int = 0
    refused_step: str | None = None
    detail: str | None = None


def _git(repo: Path, runner: Runner, *args: str, timeout: int = 60):
    cmd = ["git", "-C", str(repo), *args]
    try:
        return runner(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(exc))


def _gh(repo: Path, runner: Runner, *args: str, timeout: int = 30):
    cmd = ["gh", *args]
    try:
        return runner(
            cmd, cwd=str(repo), capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=str(exc))


def _current_branch(repo: Path, runner: Runner) -> str | None:
    result = _git(repo, runner, "symbolic-ref", "--short", "HEAD")
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def render_pr_body(
    summary: str,
    route: str,
    epic_feature_spec: str,
    gate_evidence: str,
    risk: str,
    labels: Sequence[str],
    automerge_recommendation: str,
) -> str:
    """Standard PR body (routes.md's PR template, §"summary, route, spec
    lineage, pre-PR gate evidence, risk, labels, auto-merge recommendation")
    -- every `land_pr()`-opened PR carries the same sections regardless of
    caller, so a reviewer never has to guess which subset a given route
    included."""
    label_line = ", ".join(labels) if labels else "(none)"
    return (
        "## Summary\n"
        f"{summary}\n\n"
        "## Route\n"
        f"{route}\n\n"
        "## Epic / Feature / Specification\n"
        f"{epic_feature_spec}\n\n"
        "## Pre-PR Gate Evidence\n"
        f"{gate_evidence}\n\n"
        "## Risk Assessment\n"
        f"{risk}\n\n"
        "## Labels\n"
        f"{label_line}\n\n"
        "## Auto-Merge Recommendation\n"
        f"{automerge_recommendation}\n"
    )


def _commit_pending(
    repo: Path, commit_message: str | None, runner: Runner
) -> str | None:
    """Commit a dirty tree, or refuse (`"dirty_tree"`) when no
    `commit_message` was supplied -- see module docstring step 1. Never
    touches a clean tree. Returns the refused-step name, or None on success."""
    status = _git(repo, runner, "status", "--porcelain")
    if status.returncode != 0 or not status.stdout.strip():
        return None
    if not commit_message:
        return "dirty_tree"
    add = _git(repo, runner, "add", "-A")
    if add.returncode != 0:
        return "dirty_tree"
    commit = _git(repo, runner, "commit", "-m", commit_message)
    if commit.returncode != 0:
        return "dirty_tree"
    return None


def _ensure_compile_markers(
    repo: Path, base_branch: str, runner: Runner
) -> tuple[str | None, Any]:
    """Compile-marker gate -- see module docstring step 2. Returns
    `(refused_step, detail)`; `(None, None)` when nothing needed checking or
    every touched change carries a fresh marker.

    Fails closed on every git failure along the way: a failed `fetch` means
    `changed_change_dirs()`'s empty-list result (its documented behavior when
    the base ref can't be resolved) cannot be trusted as "nothing changed",
    and a failed `add`/`commit` of a freshly-written marker must not let the
    branch be pushed as if that marker were tracked."""
    fetch = _git(repo, runner, "fetch", "origin", base_branch)
    if fetch.returncode != 0:
        return "compile_marker", (
            f"git fetch origin {base_branch} failed: "
            f"{(fetch.stderr or fetch.stdout).strip()}"
        )

    dirs = check_compile_markers.changed_change_dirs(repo, f"origin/{base_branch}")
    if not dirs:
        return None, None

    for change_dir in dirs:
        conductor_compile.main([str(change_dir)])

    results = [check_compile_markers.check_marker(d) for d in dirs]
    failing = [r for r in results if r["status"] != "ok"]
    if failing:
        return "compile_marker", failing

    marker_paths = [
        str(conductor_compile.marker_path(d).relative_to(repo)) for d in dirs
    ]
    add = _git(repo, runner, "add", "--", *marker_paths)
    if add.returncode != 0:
        return "compile_marker", (
            f"git add of compile marker(s) failed: {(add.stderr or add.stdout).strip()}"
        )
    staged = _git(repo, runner, "diff", "--cached", "--name-only", "--", *marker_paths)
    if staged.returncode == 0 and staged.stdout.strip():
        change_names = ", ".join(d.name for d in dirs)
        commit = _git(
            repo,
            runner,
            "commit",
            "-m",
            f"chore({change_names}): record compile marker",
        )
        if commit.returncode != 0:
            return "compile_marker", (
                f"commit of compile marker(s) failed: "
                f"{(commit.stderr or commit.stdout).strip()}"
            )
    return None, None


def _run_preflight_and_labels(
    repo: Path,
    base_branch: str,
    risk: str,
    gates: Sequence[str],
    route: str,
    run_path: str | None,
) -> tuple[str | None, list[str]]:
    """Preflight gate + labels -- see module docstring step 3. Returns
    `(refused_step, labels)`; labels are only meaningful when
    `refused_step` is None."""
    argv = ["run", "--repo", str(repo), "--risk", risk, "--target-branch", base_branch]
    if gates:
        argv += ["--gates", ",".join(gates)]
    if route:
        argv += ["--route", route]
    if run_path:
        argv += ["--run", run_path]
    exit_code = preflight.main(argv)
    if exit_code != 0:
        return "preflight", []
    marker = preflight.read_marker(repo)
    labels = list((marker or {}).get("labels") or [])
    return None, labels


def _push(repo: Path, branch: str, runner: Runner) -> str | None:
    """Push the branch -- see module docstring step 4. Returns a
    refused-step name (`"push"`) on failure, else None."""
    upstream = _git(
        repo, runner, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"
    )
    if upstream.returncode == 0:
        result = _git(repo, runner, "push")
    else:
        result = _git(repo, runner, "push", "-u", "origin", branch)
    return None if result.returncode == 0 else "push"


def open_or_update_pull_request(
    repo: Path,
    base_branch: str,
    head_branch: str,
    title: str,
    body: str,
    risk: str,
    labels: Sequence[str],
    route: str,
    runner: Runner,
) -> dict[str, Any]:
    """Find-or-create the PR -- see module docstring step 5. Returns
    `{"pr_url", "pr_number", "refused_step", "detail"}`; `pr_url`/`pr_number`
    are None only when `refused_step` is set.

    Shared with `orchestrator/integrate.py`'s group-PR open/update step
    (design.md D6) -- this is the only place a `gh pr create` literal for a
    non-sandbox caller may appear (see
    `test_pr_creation_callsite_enforcement_coverage.py`)."""
    view = _gh(
        repo,
        runner,
        "pr",
        "view",
        head_branch,
        "--json",
        "url,number,state,labels",
    )
    if view.returncode == 0:
        try:
            data = json.loads(view.stdout)
        except json.JSONDecodeError:
            data = {}
        if data.get("state") == "OPEN":
            pr_url = data.get("url")
            pr_number = data.get("number")
            current_labels = {entry.get("name") for entry in data.get("labels", [])}
            # Apply every preflight-computed label the PR doesn't already
            # carry -- not `ensure_pr_risk_label()`, which deliberately no-ops
            # whenever ANY go:risk-* label is already present (see its own
            # docstring), so it can't be trusted to guarantee the exact
            # preflight-computed label set lands on an update.
            for label in labels:
                if label in current_labels:
                    continue
                applied = pr_labels._add_label(str(repo), pr_url, label, runner=runner)
                if applied is None:
                    return {
                        "pr_url": None,
                        "pr_number": None,
                        "refused_step": "pr_labels",
                        "detail": f"failed to apply label {label!r} to {pr_url}",
                    }
            return {
                "pr_url": pr_url,
                "pr_number": pr_number,
                "refused_step": None,
                "detail": None,
            }

    cmd = [
        "gh",
        "pr",
        "create",
        "--base",
        base_branch,
        "--head",
        head_branch,
        "--title",
        title,
        "--body",
        body,
    ]
    for label in labels:
        cmd += ["--label", label]
    result = pr_labels._run_gh_cmd(cmd, str(repo), runner)
    out = (
        (result.stdout or result.stderr).strip().splitlines()[-1]
        if (result.stdout or result.stderr)
        else "(no output)"
    )
    if result.returncode != 0 or not out.startswith("http"):
        return {
            "pr_url": None,
            "pr_number": None,
            "refused_step": "pr_create",
            "detail": f"gh pr create failed for branch {head_branch}: {out}",
        }

    number_result = _gh(repo, runner, "pr", "view", out, "--json", "number")
    pr_number = None
    if number_result.returncode == 0:
        try:
            pr_number = json.loads(number_result.stdout).get("number")
        except json.JSONDecodeError:
            pr_number = None
    return {
        "pr_url": out,
        "pr_number": pr_number,
        "refused_step": None,
        "detail": None,
    }


def _run_record_main(argv: list[str]) -> tuple[int, str]:
    """`run_record.main(argv)`, capturing whatever it printed to stdout."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        exit_code = run_record_module.main(argv)
    return exit_code, buf.getvalue()


def _ensure_run_record(
    repo: Path,
    route: str,
    risk: str,
    request_summary: str,
    run_path: str | None,
) -> str | None:
    """Start a run record for a caller that has none -- see module docstring
    step 6. Returns the (possibly newly-started) run-record path, or None if
    starting one failed."""
    if run_path:
        return run_path
    argv = [
        "start",
        "--repo",
        str(repo),
        "--route",
        route,
        "--risk",
        risk,
        "--request",
        request_summary,
    ]
    exit_code, out = _run_record_main(argv)
    if exit_code != 0:
        return None
    try:
        return json.loads(out.strip().splitlines()[-1]).get("path")
    except (json.JSONDecodeError, IndexError):
        return None


def _is_transient_check(name: str, log_excerpt: str) -> bool:
    if any(marker in name for marker in _TRANSIENT_CHECK_NAME_MARKERS):
        return True
    return any(marker in log_excerpt for marker in _TRANSIENT_LOG_MARKERS)


def _log_excerpt(repo: Path, runner: Runner, run_id: str) -> str:
    result = _gh(repo, runner, "run", "view", run_id, "--log-failed")
    if result.returncode != 0:
        return ""
    lines = result.stdout.splitlines()
    return "\n".join(lines[-_LOG_EXCERPT_LINES:])


def _watch_ci(
    repo: Path, pr_number: int, watch_timeout_s: int, runner: Runner
) -> dict[str, Any]:
    """CI watch -- ci-watch-loop.md cases 1/2/3/5, implemented in code (see
    module docstring step 7 / design.md D7). Returns `{"settled": bool,
    "failing_checks": [...], "log_excerpt": str, "budget_exhausted": bool}`.
    """
    reruns = 0
    for _ in range(WATCH_REISSUE_MAX + 1):
        watch = _gh(
            repo,
            runner,
            "pr",
            "checks",
            str(pr_number),
            "--watch",
            "--fail-fast",
            timeout=watch_timeout_s,
        )
        if watch.returncode == 0:
            return {
                "settled": True,
                "failing_checks": [],
                "log_excerpt": "",
                "budget_exhausted": False,
            }

        checks = _gh(
            repo,
            runner,
            "pr",
            "checks",
            str(pr_number),
            "--json",
            "name,bucket,workflowRunId",
        )
        if checks.returncode != 0:
            continue
        try:
            rows = json.loads(checks.stdout)
        except json.JSONDecodeError:
            rows = []
        failing = [r for r in rows if r.get("bucket") == "fail"]
        if not failing:
            continue

        transient = []
        real = []
        for row in failing:
            run_id = str(row.get("workflowRunId") or "")
            excerpt = _log_excerpt(repo, runner, run_id) if run_id else ""
            if _is_transient_check(row.get("name", ""), excerpt):
                transient.append(row)
            else:
                real.append((row, excerpt))

        if real:
            name, excerpt = real[0][0].get("name", ""), real[0][1]
            return {
                "settled": False,
                "failing_checks": [r.get("name", "") for r, _ in real],
                "log_excerpt": excerpt,
                "budget_exhausted": False,
            }

        for row in transient:
            run_id = str(row.get("workflowRunId") or "")
            if run_id and reruns < TRANSIENT_RERUN_MAX:
                _gh(repo, runner, "run", "rerun", run_id, "--failed")
                reruns += 1

    return {
        "settled": False,
        "failing_checks": [],
        "log_excerpt": "",
        "budget_exhausted": True,
    }


def _merge_state_guard(repo: Path, pr_number: int, runner: Runner) -> dict[str, Any]:
    """Merge-state guard -- design.md D7. Returns the settled `gh pr view`
    JSON payload (possibly after up to `MERGE_STATE_RERUN_MAX` reruns of a
    CANCELLED/SUCCESS same-name check pair)."""
    reruns = 0
    while True:
        status = _gh(
            repo,
            runner,
            "pr",
            "view",
            str(pr_number),
            "--json",
            "state,mergedAt,autoMergeRequest,headRefOid,mergeStateStatus,"
            "statusCheckRollup",
        )
        if status.returncode != 0:
            return {}
        try:
            data = json.loads(status.stdout)
        except json.JSONDecodeError:
            return {}
        if data.get("mergeStateStatus") != "BLOCKED" or reruns >= MERGE_STATE_RERUN_MAX:
            return data

        rollup = data.get("statusCheckRollup") or []
        by_name: dict[str, list[str]] = {}
        for entry in rollup:
            name = entry.get("name") or entry.get("context")
            state = entry.get("conclusion") or entry.get("state")
            if name:
                by_name.setdefault(name, []).append(state)
        pair_found = False
        for entry in rollup:
            name = entry.get("name") or entry.get("context")
            state = entry.get("conclusion") or entry.get("state")
            states = by_name.get(name, [])
            if state == "CANCELLED" and "SUCCESS" in states:
                database_id = entry.get("databaseId")
                if database_id:
                    _gh(repo, runner, "run", "rerun", str(database_id))
                    pair_found = True
        if not pair_found:
            return data
        reruns += 1


def _review_thread_gate(
    repo: Path, pr_number: int, run_path: str | None, runner: Runner
) -> dict[str, Any]:
    """Review-thread gate -- design.md D7. Thin pass-through to
    `check_review_threads.check`."""
    return check_review_threads.check(
        repo,
        pr_number,
        Path(run_path) if run_path else None,
        runner=runner,
    )


def _automerge_mechanism(status: dict[str, Any]) -> str:
    auto = status.get("autoMergeRequest")
    if not auto:
        return "no auto-merge armed"
    enabled_by = (auto.get("enabledBy") or {}).get("login", "unknown")
    method = auto.get("mergeMethod", "unknown")
    return f"auto-merge armed ({method}) by {enabled_by}"


def _finish_or_checkpoint(
    run_path: str,
    status_value: str,
    pr_url: str,
    merge_result: str,
    checkpoint: bool,
) -> None:
    """Finish the run record, or (checkpoint mode, all-pass case only)
    append a decision instead -- design.md D7's checkpoint substitution."""
    if checkpoint:
        _run_record_main(
            [
                "append",
                run_path,
                "decisions",
                f"{status_value}: {merge_result}",
            ]
        )
        return
    _run_record_main(
        [
            "finish",
            run_path,
            "--status",
            status_value,
            "--pr",
            pr_url,
            "--merge-result",
            merge_result,
        ]
    )


def land_pr(request: LandRequest) -> LandOutcome:
    """Run the full pipeline for `request`. See module docstring for the
    ordered steps this composes."""
    repo = Path(request.repo).resolve()
    runner = request.runner

    refused = _commit_pending(repo, request.commit_message, runner)
    if refused:
        return LandOutcome(outcome="refused", refused_step=refused)

    refused, detail = _ensure_compile_markers(repo, request.base_branch, runner)
    if refused:
        return LandOutcome(outcome="refused", refused_step=refused, detail=detail)

    refused, labels = _run_preflight_and_labels(
        repo,
        request.base_branch,
        request.risk,
        request.gates,
        request.route,
        request.run,
    )
    if refused:
        return LandOutcome(outcome="refused", refused_step=refused)

    branch = _current_branch(repo, runner)
    if not branch:
        return LandOutcome(
            outcome="refused", refused_step="push", detail="no current branch"
        )
    refused = _push(repo, branch, runner)
    if refused:
        return LandOutcome(outcome="refused", refused_step=refused)

    body = render_pr_body(
        summary=request.summary,
        route=request.route,
        epic_feature_spec=request.spec_lineage or "none",
        gate_evidence="worktrail-preflight run: PASS",
        risk=request.risk,
        labels=labels,
        automerge_recommendation="eligible"
        if "go:no-automerge" not in labels
        else "ineligible",
    )
    pr_result = open_or_update_pull_request(
        repo,
        request.base_branch,
        branch,
        request.title,
        body,
        request.risk,
        labels,
        request.route,
        runner,
    )
    if pr_result["refused_step"]:
        # Not `refused`: the branch is already pushed at this point, so the
        # remote is already mutated and a plain `refused` (which promises an
        # untouched remote) would misrepresent that -- see module docstring.
        return LandOutcome(
            outcome="ceiling",
            refused_step=pr_result["refused_step"],
            final_status="failed_recoverable",
            merge_result=pr_result["detail"],
            detail=pr_result["detail"],
        )
    pr_url = pr_result["pr_url"]
    pr_number = pr_result["pr_number"]

    run_path = _ensure_run_record(
        repo, request.route, request.risk, request.request_summary, request.run
    )
    if run_path is None:
        # The branch is pushed and the PR is open, but no run record exists
        # to track it -- can't complete a run record that was never created
        # (Requirement: run record is completed with a real state), so this
        # can't be reported as `landed`.
        return LandOutcome(
            outcome="ceiling",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            final_status="failed_recoverable",
            merge_result="run record could not be started; PR is open but unrecorded",
            detail="run_record start failed",
        )
    _run_record_main(["set", run_path, "pull_request", pr_url])

    watch = (
        _watch_ci(repo, pr_number, request.watch_timeout_s, runner)
        if pr_number
        else {
            "settled": False,
            "failing_checks": [],
            "log_excerpt": "",
            "budget_exhausted": True,
        }
    )

    if watch["budget_exhausted"]:
        if run_path:
            _run_record_main(
                [
                    "finish",
                    run_path,
                    "--status",
                    "failed_recoverable",
                    "--pr",
                    pr_url or "",
                    "--merge-result",
                    "checks still pending at watch budget",
                ]
            )
        return LandOutcome(
            outcome="ceiling",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            run=run_path,
            final_status="failed_recoverable",
            merge_result="checks still pending at watch budget",
        )

    if not watch["settled"]:
        current_iteration = 0
        if run_path:
            record = _load_run_record(Path(run_path))
            current_iteration = int(record.get("ci_patch_iterations") or 0)
        next_iteration = current_iteration + 1
        if next_iteration >= CI_PATCH_ITERATION_CEILING:
            merge_result = (
                f"code defect ceiling reached after {current_iteration} patch "
                "iteration(s)"
            )
            if run_path:
                _run_record_main(
                    [
                        "finish",
                        run_path,
                        "--status",
                        "failed_recoverable",
                        "--pr",
                        pr_url or "",
                        "--merge-result",
                        merge_result,
                    ]
                )
            return LandOutcome(
                outcome="ceiling",
                pr_url=pr_url,
                pr_number=pr_number,
                labels=labels,
                run=run_path,
                final_status="failed_recoverable",
                merge_result=merge_result,
                failing_checks=watch["failing_checks"],
                log_excerpt=watch["log_excerpt"],
                patch_iteration=current_iteration,
            )
        if run_path:
            _run_record_main(
                ["set", run_path, "ci_patch_iterations", str(next_iteration)]
            )
        return LandOutcome(
            outcome="code_defect",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            run=run_path,
            failing_checks=watch["failing_checks"],
            log_excerpt=watch["log_excerpt"],
            patch_iteration=next_iteration,
        )

    status = _merge_state_guard(repo, pr_number, runner)

    if not status:
        # `_merge_state_guard()` returns `{}` when `gh pr view` failed or
        # returned unparseable data -- an unavailable guard is not a passed
        # guard (Requirement: merge-state guard before completion), so this
        # cannot fall through to `landed`.
        merge_result = "merge-state guard unavailable (gh pr view failed or returned malformed data)"
        _run_record_main(
            [
                "finish",
                run_path,
                "--status",
                "failed_recoverable",
                "--pr",
                pr_url or "",
                "--merge-result",
                merge_result,
            ]
        )
        return LandOutcome(
            outcome="ceiling",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            run=run_path,
            final_status="failed_recoverable",
            merge_result=merge_result,
        )

    if status.get("state") == "MERGED":
        merge_result = "merged externally"
        _finish_or_checkpoint(
            run_path,
            "completed_and_merged",
            pr_url,
            merge_result,
            request.checkpoint,
        )
        return LandOutcome(
            outcome="landed",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            run=run_path,
            final_status="completed_and_merged",
            merge_result=merge_result,
        )

    threads = _review_thread_gate(repo, pr_number, run_path, runner)
    if threads.get("blocking"):
        return LandOutcome(
            outcome="review_threads_blocking",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            run=run_path,
            detail=json.dumps(threads.get("unaddressed") or []),
        )

    if not threads.get("checked"):
        # `check_review_threads.check()` documents `checked: false` as "no
        # signal" for its prose-driven caller (ci-watch-loop.md), which
        # proceeds rather than blocking every PR-owning route forever. This
        # code-enforced pipeline holds itself to a stricter bar: an
        # unavailable review-thread gate is not a passed gate (Requirement:
        # review-thread gate before completion), so it cannot complete here.
        merge_result = (
            "review-thread gate unavailable (gh unauthenticated/missing or "
            "malformed response)"
        )
        _run_record_main(
            [
                "finish",
                run_path,
                "--status",
                "failed_recoverable",
                "--pr",
                pr_url or "",
                "--merge-result",
                merge_result,
            ]
        )
        return LandOutcome(
            outcome="ceiling",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            run=run_path,
            final_status="failed_recoverable",
            merge_result=merge_result,
        )

    if status.get("mergeStateStatus") == "BLOCKED":
        merge_result = "blocked on branch protection after merge-state guard and review-thread gate"
        if run_path:
            _finish_or_checkpoint(
                run_path, "blocked_product_decision", pr_url, merge_result, False
            )
        return LandOutcome(
            outcome="landed",
            pr_url=pr_url,
            pr_number=pr_number,
            labels=labels,
            run=run_path,
            final_status="blocked_product_decision",
            merge_result=merge_result,
        )

    merge_result = _automerge_mechanism(status)
    if run_path:
        _finish_or_checkpoint(
            run_path, "completed_pr_open", pr_url, merge_result, request.checkpoint
        )
    return LandOutcome(
        outcome="landed",
        pr_url=pr_url,
        pr_number=pr_number,
        labels=labels,
        run=run_path,
        final_status="completed_pr_open",
        merge_result=merge_result,
    )


def _outcome_exit_code(outcome: str) -> int:
    return {
        "landed": _EXIT_LANDED,
        "refused": _EXIT_REFUSED,
        "code_defect": _EXIT_CODE_DEFECT,
        "review_threads_blocking": _EXIT_CODE_DEFECT,
        "ceiling": _EXIT_CEILING,
    }.get(outcome, 1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base", required=True, dest="base_branch")
    ap.add_argument("--title", required=True)
    summary_group = ap.add_mutually_exclusive_group(required=True)
    summary_group.add_argument("--summary")
    summary_group.add_argument("--summary-file")
    ap.add_argument("--run", default=None)
    ap.add_argument("--route", required=True)
    ap.add_argument("--spec-lineage", default="", dest="spec_lineage")
    ap.add_argument("--risk", default="low")
    ap.add_argument("--gates", default="")
    ap.add_argument("--commit-message", default=None)
    ap.add_argument("--checkpoint", action="store_true")
    ap.add_argument("--watch-timeout", type=int, default=600, dest="watch_timeout_s")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    summary = args.summary
    if args.summary_file:
        summary = Path(args.summary_file).read_text(encoding="utf-8")

    request = LandRequest(
        repo=args.repo,
        base_branch=args.base_branch,
        title=args.title,
        summary=summary,
        route=args.route,
        risk=args.risk,
        gates=[g for g in args.gates.split(",") if g],
        run=args.run,
        request_summary=summary,
        spec_lineage=args.spec_lineage,
        commit_message=args.commit_message,
        checkpoint=args.checkpoint,
        watch_timeout_s=args.watch_timeout_s,
    )
    outcome = land_pr(request)
    payload = {
        "outcome": outcome.outcome,
        "pr_url": outcome.pr_url,
        "pr_number": outcome.pr_number,
        "labels": outcome.labels,
        "run": outcome.run,
        "final_status": outcome.final_status,
        "merge_result": outcome.merge_result,
        "failing_checks": outcome.failing_checks,
        "log_excerpt": outcome.log_excerpt,
        "patch_iteration": outcome.patch_iteration,
        "refused_step": outcome.refused_step,
        "detail": outcome.detail,
    }
    print(json.dumps(payload))
    return _outcome_exit_code(outcome.outcome)


if __name__ == "__main__":
    sys.exit(main())
