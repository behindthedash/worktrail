#!/usr/bin/env python3
"""Regression test closing the one-off-vs-orchestrator gate-parity coverage gap.

docs/specs/research/go-orchestrator-gate-parity-audit.md found three prior incidents of one
shape: a check gets wired into `pre_pr_gate.py`'s one-off path (`main()`), and
`integrate.py`'s separate orchestrator group-PR path silently does not inherit it --
`require_human_routes`/`gates` threading (brief 20260731-145729, PR #90), the four
deterministic drift checks (brief 20260815-134144, PR #424/#439), and `scope_review_failures()`
(found by that same audit, fixed by brief 20260815-172721-worktrail-orchestrator-group-pr-path:
`run_record.py finish()` now code-enforces it directly, unconditional on route, rather than
depending on `integrate.py` threading `--run` through its per-group `pre_pr_gate.py` calls).
Nothing structurally stopped a fourth recurrence: a new call added directly to `main()` with no
orchestrator-path decision.

This test closes the whole failure mode, mirroring `test_gate_enforcement_coverage.py`'s
AST-registry convention:

1. `extract_main_direct_calls()` AST-walks `pre_pr_gate.py`'s `main()` for every direct call to
   a function defined elsewhere in the same module -- no hand-maintained list to go stale --
   and `test_every_main_gate_call_has_a_parity_verdict` asserts each one has an entry in
   `GATE_PARITY` below. A future call added to `main()` with no registered verdict fails this
   test immediately, forcing the same explicit orchestrator-path decision the three prior
   incidents each skipped.
2. Every `"shared"` verdict carries a callable that behaviorally/structurally proves the
   orchestrator path really does reach the same check (not just a comment claiming it does),
   exercised by `test_shared_verdicts_actually_reach_the_orchestrator_path`.
3. Every `"exempt"` or `"known-gap"` verdict must carry a non-empty written reason, checked by
   `test_exempt_and_known_gap_entries_have_reasons` -- a silent skip is exactly what this test
   exists to prevent.
"""

import ast
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import worktrail.orchestrator.integrate as integrate_mod
import worktrail.router.pre_pr_gate as pre_pr_gate_mod
from worktrail.router.run_record import main as run_record_main

GATE_SRC = Path(pre_pr_gate_mod.__file__).resolve()
INTEGRATE_SRC = Path(integrate_mod.__file__).resolve()


def _main_function_node(tree: ast.Module) -> ast.FunctionDef:
    return next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"
    )


def extract_main_direct_calls() -> set:
    """Every function, defined elsewhere in pre_pr_gate.py, that main() calls directly."""
    tree = ast.parse(GATE_SRC.read_text())
    module_funcnames = {
        n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
    }
    main_node = _main_function_node(tree)
    found = set()
    for node in ast.walk(main_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in module_funcnames
        ):
            found.add(node.func.id)
    return found


def _branch_calls(main_node: ast.FunctionDef, flag_attr: str, target_call: str) -> bool:
    """True if an `if args.<flag_attr>:` branch in main() calls `target_call(...)`."""
    for node in ast.walk(main_node):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Attribute)
            and node.test.attr == flag_attr
        ):
            for sub in ast.walk(node):
                if (
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Name)
                    and sub.func.id == target_call
                ):
                    return True
    return False


def _proves_run_drift_checks_shared():
    """`--checks-only` returns `run_drift_checks()` directly (pre_pr_gate.py's main()), and
    integrate.py's `_run_drift_gate()` invokes the gate script with `--checks-only` before
    every group's push (integrate.py:_run_drift_gate, call site inside the per-group
    integration loop) -- the identical function, not a re-implementation."""
    tree = ast.parse(GATE_SRC.read_text())
    main_node = _main_function_node(tree)
    if not _branch_calls(main_node, "checks_only", "run_drift_checks"):
        raise AssertionError(
            "pre_pr_gate.py's --checks-only branch no longer returns run_drift_checks() -- "
            "orchestrator drift-check parity broken (was fixed by brief 20260815-134144)"
        )
    integrate_src = INTEGRATE_SRC.read_text()
    if "--checks-only" not in integrate_src:
        raise AssertionError(
            "integrate.py no longer passes --checks-only to pre_pr_gate.py -- orchestrator "
            "drift-check parity broken (was fixed by brief 20260815-134144)"
        )


def _proves_resolve_pr_labels_shared():
    """`--labels-only` returns `resolve_pr_labels()` directly (pre_pr_gate.py's main()), and
    integrate.py's `_refresh_pr_labels()` invokes the gate script with `--labels-only
    --gates --route`, threading the same route/gates context the one-off path passes (brief
    20260731-145729, PR #90) -- required for automerge_eligible()'s require_human_routes and
    classifier-gates checks to apply identically to orchestrator group PRs."""
    tree = ast.parse(GATE_SRC.read_text())
    main_node = _main_function_node(tree)
    if not _branch_calls(main_node, "labels_only", "resolve_pr_labels"):
        raise AssertionError(
            "pre_pr_gate.py's --labels-only branch no longer returns resolve_pr_labels() -- "
            "orchestrator label parity broken"
        )
    integrate_src = INTEGRATE_SRC.read_text()
    for required in ("--labels-only", "--gates", "--route"):
        if required not in integrate_src:
            raise AssertionError(
                f"integrate.py no longer threads {required} to pre_pr_gate.py -- orchestrator "
                "automerge-eligibility parity broken (was fixed by brief 20260731-145729)"
            )


def _proves_scope_review_reaches_orchestrator_via_finish():
    """`scope_review_failures()` reaches the orchestrator path by a different mechanism than
    the two proofs above: not by `integrate.py` threading `--run` through its per-group
    `pre_pr_gate.py` calls (it doesn't, and doesn't need to), but because `run_record.py
    finish()` now code-enforces `scope_review_failures()` directly
    (`_enforce_scope_completeness_gate`) for every implementation-completion status,
    unconditional on route -- and `finish()` runs exactly once per run regardless of how many
    group PRs the orchestrator created along the way (brief
    20260815-172721-worktrail-orchestrator-group-pr-path). Prove behaviorally: a run with no
    scope-review entry cannot reach `completed_pr_open`, for a route (F) that reaches the
    orchestrator's `modify` pipeline just as readily as C/D."""
    with tempfile.TemporaryDirectory() as tmp:
        out = StringIO()
        with patch("sys.stdout", out):
            rc = run_record_main(
                [
                    "start",
                    "--repo",
                    "/tmp/fake-repo",
                    "--request",
                    "t",
                    "--route",
                    "F",
                    "--risk",
                    "low",
                    "--dir",
                    tmp,
                ]
            )
        if rc != 0:
            raise AssertionError("run_record.py start failed")
        run_path = json.loads(out.getvalue())["path"]
        try:
            run_record_main(["finish", run_path, "--status", "completed_pr_open"])
        except SystemExit:
            pass
        else:
            raise AssertionError(
                "run_record.py finish allowed an implementation-completion status with no "
                "scope-review entry -- scope_review_failures() is no longer reached by the "
                "orchestrator path (finish() is the shared mechanism this proof relies on)"
            )


# call name (as found directly inside main()) -> (verdict, detail)
#   "shared":     detail is a zero-arg callable proving the orchestrator path reaches the
#                 same check -- raises AssertionError on failure.
#   "exempt":     detail is a non-empty string explaining why no orchestrator-path
#                 equivalent is needed (safe-by-construction or explicitly policy-scoped).
#   "known-gap":  detail is a non-empty string naming the tracking brief -- an acknowledged,
#                 not-yet-fixed asymmetry (distinct from "exempt": this one SHOULD close).
GATE_PARITY = {
    "run_drift_checks": ("shared", _proves_run_drift_checks_shared),
    "resolve_pr_labels": ("shared", _proves_resolve_pr_labels_shared),
    "scope_review_failures": (
        "shared",
        _proves_scope_review_reaches_orchestrator_via_finish,
    ),
    "is_docs_only": (
        "exempt",
        (
            "skip-only mechanism (never fails the gate): the orchestrator's "
            "_run_integration_smoke() always runs integrate_smoke_cmd unconditionally, so it "
            "does strictly MORE work than the one-off path here, never less -- the safe "
            "direction for a skip to diverge in."
        ),
    ),
    "is_promotion_pr": (
        "exempt",
        (
            "skip-only mechanism (never fails the gate), same shape as is_docs_only above: the "
            "orchestrator's _run_integration_smoke() always runs integrate_smoke_cmd "
            "unconditionally regardless of this check, so it does strictly MORE work than the "
            "one-off path's promotion-PR skip, never less. Also structurally inapplicable to the "
            "orchestrator path: group PRs target the repo's own base branch (feature work), not a "
            "policy-declared promotion_pairs head/base pair, so is_promotion_pr() would never "
            "return True there even if invoked."
        ),
    ),
    "resolve_cmd": (
        "exempt",
        (
            "the orchestrator runs a deliberately separate, policy-documented command "
            "(integrate_smoke_cmd via _run_integration_smoke), not pre_pr_cmd -- both keys "
            "are explicit, independent entries in docs/specs/go-policy.yaml's schema, not a "
            "silent gap."
        ),
    ),
    "_warn_orphaned_tests": (
        "exempt",
        "prints a warning only; never affects the gate's return code on either path.",
    ),
}


class TestPrePrGateParity(unittest.TestCase):
    def test_every_main_gate_call_has_a_parity_verdict(self):
        found = extract_main_direct_calls()
        self.assertTrue(
            found,
            "extraction found no direct calls in main() -- "
            "pre_pr_gate.py's structure may have moved/renamed",
        )
        registered = set(GATE_PARITY)
        missing = found - registered
        self.assertFalse(
            missing,
            f"pre_pr_gate.py's main() calls {sorted(missing)} directly with no GATE_PARITY "
            "entry -- classify it 'shared' (with a proof the orchestrator path also reaches "
            "it), 'exempt' (with a reason no orchestrator equivalent is needed), or "
            "'known-gap' (with a tracking brief) before shipping. See "
            "docs/specs/research/go-orchestrator-gate-parity-audit.md",
        )
        stale = registered - found
        self.assertFalse(
            stale,
            f"GATE_PARITY registers {sorted(stale)}, which main() no longer calls directly "
            "-- remove the stale entry",
        )

    def test_shared_verdicts_actually_reach_the_orchestrator_path(self):
        for name, (verdict, detail) in GATE_PARITY.items():
            if verdict == "shared":
                with self.subTest(call=name):
                    detail()

    def test_exempt_and_known_gap_entries_have_reasons(self):
        for name, (verdict, detail) in GATE_PARITY.items():
            if verdict in ("exempt", "known-gap"):
                with self.subTest(call=name):
                    self.assertTrue(
                        isinstance(detail, str) and detail.strip(),
                        f"{name} is marked {verdict!r} but has no reason recorded",
                    )


if __name__ == "__main__":
    unittest.main()
