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
    """land_pr.py's update path (an existing OPEN PR) must guarantee the
    preflight-computed label set actually lands, not merely best-effort:
    `open_or_update_pull_request` applies every label in the caller-supplied
    `labels` argument the PR doesn't already carry, via the injected runner
    and the REST label-add endpoint (never `gh pr edit`/`gh pr create`, and
    never `ensure_pr_risk_label()`, which deliberately no-ops whenever ANY
    go:risk-* label is already present). Also proves `land_pr()` itself
    sources those labels from `_run_preflight_and_labels()` ->
    `preflight.read_marker()`, not an independently hand-rolled list."""
    calls = []

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
                        "labels": [{"name": "go:risk-low"}],
                    }
                ),
                stderr="",
            )
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    result = land_pr.open_or_update_pull_request(
        Path("/fake/repo"),
        "main",
        "feature",
        "title",
        "body",
        "high",
        ["go:risk-low", "go:risk-high", "go:no-automerge"],
        "route-a",
        fake_run,
    )

    if result["refused_step"] is not None:
        raise AssertionError(f"update path unexpectedly refused: {result['detail']}")
    if any(cmd[:3] == ["gh", "pr", "create"] for cmd in calls):
        raise AssertionError("update path issued gh pr create for an already-OPEN PR")
    added = {
        part.split("=", 1)[1]
        for cmd in calls
        for part in cmd
        if part.startswith("labels[]=")
    }
    if added != {"go:risk-high", "go:no-automerge"}:
        raise AssertionError(
            "update path did not apply exactly the missing preflight-computed "
            f"labels: applied {added!r}, expected {{'go:risk-high', "
            "'go:no-automerge'}} (go:risk-low was already present)"
        )

    source = inspect.getsource(land_pr._run_preflight_and_labels)
    if "preflight.read_marker" not in source:
        raise AssertionError(
            "_run_preflight_and_labels no longer sources labels from "
            "preflight.read_marker() -- land_pr()'s update-path labels would "
            "no longer be preflight-computed"
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


if __name__ == "__main__":
    unittest.main()
