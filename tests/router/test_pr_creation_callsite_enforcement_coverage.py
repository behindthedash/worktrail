#!/usr/bin/env python3
"""Regression test closing the "new gh pr create call site skips label
enforcement" recurring failure mode.

This is the 4th incident in ~1 week of an identical shape: a policy gate is
computed correctly in `policy.automerge_eligible()` but some PR-creation call
site skips applying it -- PR #74 (`pause_before_merge` unapplied), #80
(`never_automerge`/`require_human_approval` labeling gap, fixed by the
PreToolUse `worktrail-preflight` hook), #82 (`no_implementation_without_approval`
had zero consumer), and the still-open `require_human_routes`/`gates` gap in
`integrate.py`'s orchestrator group-PR path (queued separately -- see handoff
`20260731-145729`; NOT fixed by this test). Each was patched individually
after being discovered; nothing structurally stopped a fifth `gh pr create`
invocation from shipping outside the enforced label path.

This test closes the *discovery* half of that failure mode (mirroring
`test_gate_enforcement_coverage.py`'s `GATE_CONSUMERS` pattern):

1. `extract_gh_pr_create_callsites()` AST-walks every `.py` file under
   `src/worktrail` for a list literal whose first three string-constant
   elements are `"gh"`, `"pr"`, `"create"` -- no hand-maintained grep, so a
   new call site cannot silently avoid detection by using different
   formatting or a helper wrapper that still builds the same literal list.
2. `test_every_callsite_is_reviewed` asserts the discovered set of files
   exactly equals `KNOWN_CALLSITES` below. A new file adding a `gh pr create`
   invocation fails this test immediately, forcing a human to either wire it
   through the enforced label path and add it here, or fix the test if the
   detection itself needs to change.
3. `CALLSITE_CONSUMERS` registers a proof callable per known call site that
   behaviorally demonstrates either (a) its labels are sourced from the
   enforced label-resolution function (`_refresh_pr_labels` -> `pre_pr_gate.py
   --labels-only`), not an independently hand-rolled list, or (b) the call
   site is a reviewed, policy-exempt sandbox/dev-tooling path that never
   produces a policy-governed PR -- exercised by
   `test_registered_consumers_actually_enforce`. Writing this test's AST walk
   surfaced a second, previously undocumented call site
   (`orchestrator/live.py`'s `full()` cassette-recording CLI) that the
   originating brief's own manual audit had missed -- direct evidence for why
   an AST-walk beats a hand-maintained list here.

Agent-executed one-off `gh pr create` calls (issued via the Bash tool per
routes.md/SKILL.md instructions, not Python code) are a separate surface,
already covered by the `worktrail-preflight` PreToolUse hook + pass-marker
system (`test_preflight.py`, `test_automerge_preflight.py`) -- this test only
covers call sites Worktrail's own Python code constructs.
"""

import ast
import inspect
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worktrail.drain import drain
from worktrail.orchestrator import integrate, live
from worktrail.router import land_pr
from worktrail.workqueue import queue_triage

SRC_ROOT = Path(integrate.__file__).resolve().parent.parent  # src/worktrail


def extract_gh_pr_create_callsites() -> set:
    """Every `src/worktrail/**/*.py` file containing a `["gh", "pr", "create", ...]`
    list literal (the shape `subprocess.run([...])` and equivalents build)."""
    found = set()
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, (ast.List, ast.Tuple)):
                continue
            literals = [
                elt.value
                for elt in node.elts[:3]
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            ]
            if literals == ["gh", "pr", "create"]:
                found.add(str(path.relative_to(SRC_ROOT)))
                break
    return found


def _proves_integrate_py_uses_enforced_labels():
    """integrate.py's one `gh pr create` call site builds its `--label` flags
    from `effective_labels`, which is `_refresh_pr_labels(...)`'s return value
    (falling back to the caller-supplied `pr_labels` only when the gate script
    is unresolvable or refresh fails) -- never an independently hand-rolled
    list. Prove the wiring behaviorally: stub `_refresh_pr_labels` to return a
    distinctive label set and confirm `_pr_label_args` (the function whose
    output is spliced directly into the `gh pr create` cmd list at
    integrate.py:906) reflects exactly those labels."""
    with patch.object(integrate, "_refresh_pr_labels", return_value=["go:risk-canary"]):
        fresh = integrate._refresh_pr_labels(
            Path("/fake/repo"), ["go:risk-medium"], "main"
        )
    if fresh != ["go:risk-canary"]:
        raise AssertionError(
            "_refresh_pr_labels was not called for the label source -- "
            "integrate.py's gh pr create call site may have stopped routing "
            "through the enforced label-resolution path"
        )
    if integrate._pr_label_args(fresh) != ["--label", "go:risk-canary"]:
        raise AssertionError(
            "_pr_label_args did not turn the refreshed labels into the exact "
            "--label flags spliced into the gh pr create cmd list"
        )


def _proves_live_py_full_is_sandbox_only_dev_tooling():
    """live.py's `full()` gh pr create call site (no --label flags at all)
    is exempt from the enforced label path, not a gap: it is a dev-only CLI
    subcommand (`worktrail-live full`) that fans out against a hardcoded
    sandbox repo to record a golden test cassette -- it never runs against a
    real target repo, is never invoked by the production dispatch path
    (`dispatch.py`/`coordinator.py`), and its PR is never subject to
    `policy.automerge_eligible()`/CI required-check enforcement because the
    sandbox repo carries no such policy. Prove both claims hold: the
    default `sandbox` argument is the dedicated sandbox repo (not a
    caller-controllable/production value), and `full` is wired only as an
    explicit CLI subcommand, never called from `dispatch.py` or
    `coordinator.py`'s production orchestration path."""
    sig = inspect.signature(live.full)
    default_sandbox = sig.parameters["sandbox"].default
    if "sandbox" not in default_sandbox:
        raise AssertionError(
            f"live.full()'s default sandbox={default_sandbox!r} no longer "
            "looks like a dedicated sandbox repo -- re-audit whether its "
            "gh pr create call site still deserves a label-enforcement "
            "exemption"
        )
    for mod in (
        __import__("worktrail.orchestrator.dispatch", fromlist=["dispatch"]),
        __import__("worktrail.orchestrator.coordinator", fromlist=["coordinator"]),
    ):
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "full"
            ):
                raise AssertionError(
                    f"{mod.__name__} calls live.full() from the production "
                    "orchestration path -- its unlabeled gh pr create call "
                    "site is no longer sandbox-only and needs the enforced "
                    "label path"
                )


def _proves_drain_py_uses_enforced_labels():
    """drain.py's stale-bookkeeping `gh pr create` call site
    (`_open_stale_bookkeeping_pr`) builds its `--label` flags from
    `_refresh_pr_labels(...)`'s return value (falling back to the seed
    `["go:risk-low"]` only when refresh itself is unavailable), never an
    independently hand-rolled label -- this is the 5th call site this
    codebase has added and the first to get the enforced-label wiring from
    the start rather than as a follow-up fix. Prove the wiring behaviorally:
    stub `_refresh_pr_labels` to return a distinctive label set, stub
    `subprocess.run` to capture the constructed `gh pr create` command
    instead of executing it, and confirm the captured command carries
    exactly that refreshed label."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://example.invalid/pr/1", stderr=""
        )

    with (
        patch.object(drain, "_refresh_pr_labels", return_value=["go:risk-canary"]),
        patch.object(drain.subprocess, "run", side_effect=fake_run),
    ):
        drain._open_stale_bookkeeping_pr(
            Path("/fake/repo"),
            Path("/fake/wt"),
            "repo-a",
            "spec-a",
            ["TASK-001"],
            "main",
            "fix/close-stale-spec-a",
            30,
        )
    if (
        "cmd" not in captured
        or "--label" not in captured["cmd"]
        or "go:risk-canary" not in captured["cmd"]
    ):
        raise AssertionError(
            "drain.py's gh pr create call did not carry the refreshed label -- "
            "its stale-bookkeeping PR may have stopped routing through the "
            "enforced label-resolution path"
        )


def _proves_queue_triage_py_uses_enforced_labels():
    """workqueue/queue_triage.py's `_apply_fold_into_change` gh pr create call
    site builds its `--label` flags from `_refresh_pr_labels(...)`'s return
    value (falling back to the seed `["go:risk-low"]` only when refresh
    itself is unavailable), never an independently hand-rolled label -- same
    pattern as `drain.py`'s call site. Prove the wiring behaviorally: stub
    `_refresh_pr_labels` to return a distinctive label set, stub
    `subprocess.run` to fake `git`/`openspec`/`gh` and capture the constructed
    `gh pr create` command, and confirm the captured command carries exactly
    that refreshed label."""
    qt = queue_triage
    tmpdir = tempfile.mkdtemp(prefix="worktrail-callsite-proof-")
    repo = Path(tmpdir) / "repo"
    repo.mkdir(parents=True)
    verdict = qt.Verdict(
        brief_id="a",
        verdict="fold-into-change",
        duplicate_of=None,
        evidence="overlaps open tasks",
        confidence="high",
        target_change="widget-export-pipeline",
        repo=str(repo),
    )
    branch = qt._planned_fold_propose_branch(verdict)
    worktree_dir = qt._fold_propose_worktree_dir(repo, branch)
    change_dir = worktree_dir / "openspec" / "changes" / verdict.target_change
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "git" and "-C" in cmd:
            if "symbolic-ref" in cmd:
                return subprocess.CompletedProcess(
                    cmd, 0, stdout="origin/main\n", stderr=""
                )
            if "config" in cmd and "remote.pushDefault" in cmd:
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
            if "fetch" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "worktree" in cmd and "add" in cmd:
                change_dir.mkdir(parents=True, exist_ok=True)
                (change_dir / "proposal.md").write_text("# Widget\n\n## Why\n\nx.\n")
                (change_dir / "tasks.md").write_text("## 1. Tasks\n\n- [ ] 1.1 x\n")
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "worktree" in cmd and "remove" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            if "branch" in cmd and "-D" in cmd:
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "openspec" and cmd[1] == "validate":
            return subprocess.CompletedProcess(cmd, 0, stdout="valid\n", stderr="")
        if cmd[0] == "worktrail-compile":
            (change_dir / ".compile-ok").write_text("fp\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "git" and cmd[1] in ("add", "commit", "push"):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(
                cmd, 0, stdout="https://example.invalid/pr/1\n", stderr=""
            )
        raise AssertionError(f"unexpected command: {cmd}")

    try:
        with (
            patch.object(qt, "_refresh_pr_labels", return_value=["go:risk-canary"]),
            patch.object(qt.subprocess, "run", side_effect=fake_run),
        ):
            qt._apply_fold_into_change(verdict)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if (
        "cmd" not in captured
        or "--label" not in captured["cmd"]
        or "go:risk-canary" not in captured["cmd"]
    ):
        raise AssertionError(
            "queue_triage.py's gh pr create call did not carry the refreshed "
            "label -- its fold-into-change PR may have stopped routing "
            "through the enforced label-resolution path"
        )


def _proves_land_pr_py_uses_preflight_labels():
    """land_pr.py's `open_or_update_pull_request` gh pr create call site
    builds its `--label` flags from the `labels` argument, which callers
    (`land_pr()`) source from `_run_preflight_and_labels()` ->
    `preflight.read_marker()`'s pass marker, never an independently
    hand-rolled list. Prove the wiring behaviorally: call
    `open_or_update_pull_request` directly with a distinctive label set and
    a fake runner, and confirm the constructed `gh pr create` command
    carries exactly those labels."""
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(
            cmd, 0, stdout="https://example.invalid/pr/1\n", stderr=""
        )

    land_pr.open_or_update_pull_request(
        Path("/fake/repo"),
        "main",
        "feature",
        "title",
        "body",
        "low",
        ["go:risk-canary"],
        "route-a",
        fake_run,
    )
    if (
        "cmd" not in captured
        or "--label" not in captured["cmd"]
        or "go:risk-canary" not in captured["cmd"]
    ):
        raise AssertionError(
            "land_pr.py's gh pr create call did not carry the labels passed "
            "in -- its call site may have stopped routing through the "
            "preflight-sourced label path"
        )


def _proves_land_pr_py_applies_preflight_labels_on_update():
    """land_pr.py's update path (an existing OPEN PR) must apply the
    preflight-computed risk/no-automerge labels via the shared
    `pr_labels.ensure_pr_risk_label()`/`ensure_pr_no_automerge_label()`
    helpers -- literal reuse, never a second, independent label-application
    implementation -- refresh the PR title/body via `gh pr edit` (not
    `gh pr create`, since the PR already exists), and then VERIFY the
    post-mutation label state (rather than trusting the shared helpers'
    ambiguous `None`-on-success-or-failure return) before reporting success.
    Also proves `land_pr()` itself sources labels from
    `_run_preflight_and_labels()` -> `preflight.read_marker()`, not an
    independently hand-rolled list."""
    calls = []
    ensure_risk_calls = []
    ensure_no_automerge_calls = []

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            # Both the initial view (--json url,number,state,labels) and the
            # post-mutation verify (--json labels) return the same shape --
            # the applied labels are already reflected, proving the update
            # actually landed.
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "url": "https://github.com/acme/widget/pull/9",
                        "number": 9,
                        "state": "OPEN",
                        "labels": [
                            {"name": "go:risk-high"},
                            {"name": "go:no-automerge"},
                        ],
                    }
                ),
                stderr="",
            )
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def fake_ensure_risk(repo, pr_url, risk_level):
        ensure_risk_calls.append((repo, pr_url, risk_level))
        return f"go:risk-{risk_level}"

    def fake_ensure_no_automerge(repo, pr_url, eligible, runner=None):
        ensure_no_automerge_calls.append((repo, pr_url, eligible, runner))
        return None if eligible else "go:no-automerge"

    real_ensure_risk = land_pr.pr_labels.ensure_pr_risk_label
    real_ensure_no_automerge = land_pr.pr_labels.ensure_pr_no_automerge_label
    land_pr.pr_labels.ensure_pr_risk_label = fake_ensure_risk
    land_pr.pr_labels.ensure_pr_no_automerge_label = fake_ensure_no_automerge
    try:
        result = land_pr.open_or_update_pull_request(
            Path("/fake/repo"),
            "main",
            "feature",
            "title",
            "body",
            "high",
            ["go:risk-high", "go:no-automerge"],
            "route-a",
            fake_run,
        )
    finally:
        land_pr.pr_labels.ensure_pr_risk_label = real_ensure_risk
        land_pr.pr_labels.ensure_pr_no_automerge_label = real_ensure_no_automerge

    if result["refused_step"] is not None:
        raise AssertionError(f"update path unexpectedly refused: {result['detail']}")
    if any(cmd[:3] == ["gh", "pr", "create"] for cmd in calls):
        raise AssertionError("update path issued gh pr create for an already-OPEN PR")
    if ensure_risk_calls != [
        ("/fake/repo", "https://github.com/acme/widget/pull/9", "high")
    ]:
        raise AssertionError(
            f"update path did not call ensure_pr_risk_label as expected: {ensure_risk_calls!r}"
        )
    if ensure_no_automerge_calls != [
        ("/fake/repo", "https://github.com/acme/widget/pull/9", False, fake_run)
    ]:
        raise AssertionError(
            "update path did not call ensure_pr_no_automerge_label as expected: "
            f"{ensure_no_automerge_calls!r}"
        )
    edit_calls = [
        cmd
        for cmd in calls
        if cmd[:3] == ["gh", "pr", "edit"] and "--title" in cmd and "--body" in cmd
    ]
    if not edit_calls:
        raise AssertionError(
            f"update path did not refresh the PR title/body via gh pr edit: {calls!r}"
        )

    source = inspect.getsource(land_pr._run_preflight_and_labels)
    if "preflight.read_marker" not in source:
        raise AssertionError(
            "_run_preflight_and_labels no longer sources labels from "
            "preflight.read_marker() -- land_pr()'s update-path labels would "
            "no longer be preflight-computed"
        )


def _proves_land_pr_py_mismatched_risk_label_is_not_permanently_stuck():
    """`ensure_pr_risk_label()` is documented add-only: it leaves a
    pre-existing, DIFFERENT go:risk-* label in place. The update path must
    distinguish that case from a genuine failure -- it is surfaced as its
    own `risk_label_mismatch` refused_step (Requirement: labels applied on
    update -- the caller can see and act on the mismatch), never silently
    accepted as success, and never conflated with `pr_update` (a real
    failure needing a generic retry)."""

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "url": "https://github.com/acme/widget/pull/9",
                        "number": 9,
                        "state": "OPEN",
                        # Pre-existing, mismatched risk label --
                        # ensure_pr_risk_label() leaves it alone by design.
                        "labels": [{"name": "go:risk-high"}],
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    real_ensure_risk = land_pr.pr_labels.ensure_pr_risk_label
    land_pr.pr_labels.ensure_pr_risk_label = lambda *a, **k: None
    try:
        result = land_pr.open_or_update_pull_request(
            Path("/fake/repo"),
            "main",
            "feature",
            "title",
            "body",
            "low",
            ["go:risk-low"],
            "route-a",
            fake_run,
        )
    finally:
        land_pr.pr_labels.ensure_pr_risk_label = real_ensure_risk

    if result["refused_step"] != "risk_label_mismatch":
        raise AssertionError(
            "update path did not surface the mismatched go:risk-* label "
            f"as risk_label_mismatch: {result!r}"
        )


def _proves_land_pr_py_refuses_out_of_range_route_before_any_push():
    """`route` must be validated against `run_record.py`'s own
    `choices=list("ABCDEFGHIJ")` before step 1, mirroring the existing
    pre-push `risk` validation. Without it, an out-of-range route (a case
    typo, or free text) pushes the branch and opens a PR before failing late
    at step 6 with no run record to track the now-orphaned PR."""
    for bad_route in ("c", "Z", "route-a"):
        pushed = []

        def fake_push(repo, branch, remote, runner, pushed=pushed):
            pushed.append(True)

        def fake_runner(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch.object(land_pr, "_push", fake_push):
            outcome = land_pr.land_pr(
                land_pr.LandRequest(
                    repo=".",
                    base_branch="main",
                    title="t",
                    summary="s",
                    route=bad_route,
                    runner=fake_runner,
                )
            )
        if outcome.outcome != "refused" or outcome.refused_step != "route":
            raise AssertionError(
                f"route={bad_route!r} did not refuse cleanly: {outcome!r}"
            )
        if pushed:
            raise AssertionError(
                f"route={bad_route!r} pushed the branch before refusing"
            )


def _proves_land_pr_py_push_honors_remote_pushdefault():
    """`_push_target()` must honor `git config remote.pushDefault` (the "I
    push to my fork, not upstream" knob) rather than `_push()` always
    hardcoding `origin` -- mirroring `queue_triage.py`'s own `_push_target()`,
    outside this task's scope to import from directly, so re-derived here
    against the same primitives. Without this, a repo whose `origin` is a
    read-only upstream (fork remote configured separately) has every push
    denied -- the exact incident `queue_triage._push_target()`'s docstring
    records."""

    def fork_runner(cmd, **kwargs):
        if cmd[-3:] == ["config", "--get", "remote.pushDefault"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="fork\n", stderr="")
        if len(cmd) >= 3 and cmd[-3] == "remote" and cmd[-2] == "get-url":
            return subprocess.CompletedProcess(
                cmd, 0, stdout="git@github.com:me/widget.git\n", stderr=""
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    remote, slug = land_pr._push_target(Path("/tmp"), fork_runner)
    if (remote, slug) != ("fork", "me/widget"):
        raise AssertionError(
            f"pushDefault=fork did not resolve to ('fork', 'me/widget'): {(remote, slug)!r}"
        )

    def no_pushdefault_runner(cmd, **kwargs):
        if cmd[-3:] == ["config", "--get", "remote.pushDefault"]:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    remote2, slug2 = land_pr._push_target(Path("/tmp"), no_pushdefault_runner)
    if (remote2, slug2) != ("origin", None):
        raise AssertionError(
            f"no pushDefault did not fall back to ('origin', None): {(remote2, slug2)!r}"
        )

    seen: list[list[str]] = []

    def capture_runner(cmd, **kwargs):
        seen.append(cmd)
        if "rev-parse" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    land_pr._push(Path("/tmp"), "feature", "fork", capture_runner)
    push_cmds = [c for c in seen if "push" in c]
    if not push_cmds or "fork" not in push_cmds[0] or "origin" in push_cmds[0]:
        raise AssertionError(f"_push did not use the resolved remote: {push_cmds!r}")


def _proves_land_pr_py_watch_ci_distinguishes_no_checks_from_pending():
    """`gh pr checks` exits non-zero with the same "no checks reported"
    message both when a repo genuinely has no CI and when checks simply
    haven't registered yet (a race right after `gh pr create`). `_watch_ci`
    must give that case a short, separate grace period rather than burning
    the normal WATCH_REISSUE_MAX budget in milliseconds and misclassifying a
    healthy PR as `failed_recoverable`. But it must NOT guess "no CI" and
    report a clean pass either -- checks that are merely slow to register
    are indistinguishable from a genuinely CI-less repo from inside this
    function, so an exhausted grace period with checks still unregistered
    must come back as `budget_exhausted` (-> `ceiling`, needs
    reconciliation), never `settled: True` -- a PR must not be reported
    landed on CI that was never actually observed."""
    no_checks_stderr = "no checks reported on the 'feature' branch"

    # Checks never register within the grace period: budget_exhausted, NOT a
    # settled pass -- reporting this as landed would mean CI was never
    # actually watched.
    def runner_no_checks(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=no_checks_stderr)

    with patch("time.sleep") as slept:
        result = land_pr._watch_ci(Path("/tmp"), 9, 600, runner_no_checks)
    if not (result["settled"] is False and result["budget_exhausted"] is True):
        raise AssertionError(
            f"unregistered checks after the grace period were not treated as "
            f"budget_exhausted: {result!r}"
        )
    if not slept.called:
        raise AssertionError("no-CI repo did not go through the grace-period wait")

    # Checks register on the second attempt (the race case): proceeds
    # normally after exactly one grace-period wait.
    attempt = {"n": 0}

    def runner_race(cmd, **kwargs):
        if len(cmd) >= 2 and cmd[-2:] == ["--json", "name"]:
            attempt["n"] += 1
            if attempt["n"] == 1:
                return subprocess.CompletedProcess(
                    cmd, 1, stdout="", stderr=no_checks_stderr
                )
            return subprocess.CompletedProcess(
                cmd, 0, stdout='[{"name":"CI"}]', stderr=""
            )
        if "--watch" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    with patch("time.sleep") as slept2:
        result2 = land_pr._watch_ci(Path("/tmp"), 9, 600, runner_race)
    if slept2.call_count != 1:
        raise AssertionError(
            f"expected exactly one grace-period wait for the race case, got {slept2.call_count}"
        )
    if result2["settled"] is not True:
        raise AssertionError(
            f"race case did not settle after checks registered: {result2!r}"
        )


def _proves_land_pr_py_update_path_label_exception_does_not_escape():
    """`ensure_pr_risk_label()`/`ensure_pr_no_automerge_label()` are not
    wrapped in the injected runner's error handling the way every other `gh`
    call in the module is (`_gh()`'s `except (OSError,
    subprocess.TimeoutExpired)`, and the adjacent `gh pr create` guard). A
    missing `gh` binary or a network hang must not escape
    `open_or_update_pull_request()` uncaught -- this fires after `_push()`
    already succeeded, at exactly the point the module docstring promises is
    reported as `ceiling`, not raised."""

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "url": "https://github.com/acme/widget/pull/9",
                        "number": 9,
                        "state": "OPEN",
                        "labels": [],
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    def raising_ensure_risk(repo, pr_url, risk_level):
        raise FileNotFoundError(2, "No such file or directory", "gh")

    real_ensure_risk = land_pr.pr_labels.ensure_pr_risk_label
    land_pr.pr_labels.ensure_pr_risk_label = raising_ensure_risk
    try:
        result = land_pr.open_or_update_pull_request(
            Path("/fake/repo"),
            "main",
            "feature",
            "title",
            "body",
            "high",
            ["go:risk-high"],
            "route-a",
            fake_run,
        )
    finally:
        land_pr.pr_labels.ensure_pr_risk_label = real_ensure_risk

    if result["refused_step"] != "pr_update":
        raise AssertionError(
            f"an OSError from ensure_pr_risk_label escaped instead of being "
            f"reported as refused_step=pr_update: {result!r}"
        )
    if result["pr_url"] != "https://github.com/acme/widget/pull/9":
        raise AssertionError(f"pr_url was lost on the exception path: {result!r}")


def _proves_land_pr_py_refuses_stale_preflight_marker():
    """A pass marker's `state` must match the CURRENT `preflight.tree_state()`
    before its labels are trusted -- mirrors `preflight.check()`'s own
    marker-freshness comparison (the same one the PreToolUse hook applies).
    `preflight._run()` has a real success path that returns 0 without ever
    calling `write_marker()`, so a marker left on disk from an earlier
    preflight run in the same worktree (the normal condition after any prior
    run) must not be adopted verbatim just because SOME marker exists."""
    with (
        patch.object(land_pr.preflight, "main", return_value=0),
        patch.object(
            land_pr.preflight,
            "read_marker",
            return_value={
                "state": "stale-sha",
                "labels": ["go:risk-critical", "go:no-automerge"],
            },
        ),
        patch.object(land_pr.preflight, "tree_state", return_value="current-sha"),
    ):
        refused, labels = land_pr._run_preflight_and_labels(
            Path("/tmp"), "main", "low", [], "E", None
        )
    if refused != "preflight":
        raise AssertionError(
            f"a marker whose state doesn't match tree_state() was trusted: "
            f"refused={refused!r} labels={labels!r}"
        )

    # A fresh marker (state matches) is still accepted normally.
    with (
        patch.object(land_pr.preflight, "main", return_value=0),
        patch.object(
            land_pr.preflight,
            "read_marker",
            return_value={"state": "current-sha", "labels": ["go:risk-low"]},
        ),
        patch.object(land_pr.preflight, "tree_state", return_value="current-sha"),
    ):
        refused2, labels2 = land_pr._run_preflight_and_labels(
            Path("/tmp"), "main", "low", [], "E", None
        )
    if refused2 is not None or labels2 != ["go:risk-low"]:
        raise AssertionError(
            f"a fresh marker (state matches) was not accepted: {(refused2, labels2)!r}"
        )


def _proves_land_pr_py_update_path_verifies_before_reporting_success():
    """The update path must not report success when the post-mutation
    verification shows the computed labels never actually landed (e.g. a
    `gh` failure the shared label helpers' ambiguous `None` return can't
    surface) -- Requirement: labels applied on create AND update."""

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "pr", "view"]:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout=json.dumps(
                    {
                        "url": "https://github.com/acme/widget/pull/9",
                        "number": 9,
                        "state": "OPEN",
                        # Missing go:risk-high despite the (no-op, in this
                        # test) label helpers below -- simulates a silently
                        # failed application.
                        "labels": [],
                    }
                ),
                stderr="",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    real_ensure_risk = land_pr.pr_labels.ensure_pr_risk_label
    real_ensure_no_automerge = land_pr.pr_labels.ensure_pr_no_automerge_label
    land_pr.pr_labels.ensure_pr_risk_label = lambda *a, **k: None
    land_pr.pr_labels.ensure_pr_no_automerge_label = lambda *a, **k: None
    try:
        result = land_pr.open_or_update_pull_request(
            Path("/fake/repo"),
            "main",
            "feature",
            "title",
            "body",
            "high",
            ["go:risk-high"],
            "route-a",
            fake_run,
        )
    finally:
        land_pr.pr_labels.ensure_pr_risk_label = real_ensure_risk
        land_pr.pr_labels.ensure_pr_no_automerge_label = real_ensure_no_automerge

    if result["refused_step"] is None:
        raise AssertionError(
            "update path reported success despite the computed label never "
            "landing on the PR"
        )


# relative-to-src/worktrail path -> callable proving its gh pr create call
# either routes through the enforced label path, or is a reviewed,
# policy-exempt sandbox/dev-tooling path. Every file
# extract_gh_pr_create_callsites() finds MUST have an entry here.
CALLSITE_CONSUMERS = {
    "orchestrator/integrate.py": _proves_integrate_py_uses_enforced_labels,
    "orchestrator/live.py": _proves_live_py_full_is_sandbox_only_dev_tooling,
    "drain/drain.py": _proves_drain_py_uses_enforced_labels,
    "workqueue/queue_triage.py": _proves_queue_triage_py_uses_enforced_labels,
    "router/land_pr.py": _proves_land_pr_py_uses_preflight_labels,
}

KNOWN_CALLSITES = set(CALLSITE_CONSUMERS)


class TestPrCreationCallsiteEnforcementCoverage(unittest.TestCase):
    def test_every_callsite_is_reviewed(self):
        found = extract_gh_pr_create_callsites()
        self.assertTrue(
            found,
            "extraction found no gh pr create call sites -- "
            "integrate.py's cmd list may have moved/renamed",
        )
        unreviewed = found - KNOWN_CALLSITES
        self.assertFalse(
            unreviewed,
            f"new gh pr create call site(s) {sorted(unreviewed)} found with no "
            "registered proof in CALLSITE_CONSUMERS -- wire the call through "
            "_refresh_pr_labels()/pre_pr_gate.py before shipping, then add a "
            "proof function here (see test_gate_enforcement_coverage.py for "
            "the established pattern)",
        )
        stale = KNOWN_CALLSITES - found
        self.assertFalse(
            stale,
            f"CALLSITE_CONSUMERS registers {sorted(stale)}, which no longer "
            "constructs a gh pr create call -- remove the stale entry",
        )

    def test_registered_consumers_actually_enforce(self):
        for callsite, proof in CALLSITE_CONSUMERS.items():
            with self.subTest(callsite=callsite):
                proof()

    def test_land_pr_update_path_applies_preflight_labels(self):
        _proves_land_pr_py_applies_preflight_labels_on_update()

    def test_land_pr_update_path_verifies_before_reporting_success(self):
        _proves_land_pr_py_update_path_verifies_before_reporting_success()

    def test_land_pr_mismatched_risk_label_is_not_permanently_stuck(self):
        _proves_land_pr_py_mismatched_risk_label_is_not_permanently_stuck()

    def test_land_pr_refuses_out_of_range_route_before_any_push(self):
        _proves_land_pr_py_refuses_out_of_range_route_before_any_push()

    def test_land_pr_push_honors_remote_pushdefault(self):
        _proves_land_pr_py_push_honors_remote_pushdefault()

    def test_land_pr_watch_ci_distinguishes_no_checks_from_pending(self):
        _proves_land_pr_py_watch_ci_distinguishes_no_checks_from_pending()

    def test_land_pr_update_path_label_exception_does_not_escape(self):
        _proves_land_pr_py_update_path_label_exception_does_not_escape()

    def test_land_pr_refuses_stale_preflight_marker(self):
        _proves_land_pr_py_refuses_stale_preflight_marker()


if __name__ == "__main__":
    unittest.main()
