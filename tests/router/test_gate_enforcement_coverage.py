#!/usr/bin/env python3
"""Regression test closing the classify.py gate-enforcement coverage gap.

docs/specs/research/classify-gate-enforcement-audit.md found that classify.py
can emit a bounded-autonomy gate string with zero code anywhere reacting to
it. Three separate incidents recurred within one week from this exact shape:
`pause_before_merge` (PR #74 auto-merged a high-risk change unreviewed),
`never_automerge`/`require_human_approval` (computed correctly but the PR
label application was agent-executed and skippable, fixed in PR #80), and
`no_implementation_without_approval` (had no consumer at all, fixed in
PR #82). Nothing structurally stopped a fourth `gates.append("new_gate")`
from shipping the same way.

This test closes the whole failure mode:

1. `extract_emitted_gate_strings()` AST-walks classify.py for every literal
   string passed to `gates.append(...)` -- no hand-maintained list to go
   stale -- and `test_every_emitted_gate_has_a_registered_consumer` asserts
   each one has an entry in `GATE_CONSUMERS` below. A future gate string
   added without a registered consumer fails this test immediately.
2. Each `GATE_CONSUMERS` entry is a callable that behaviorally proves real
   enforcement exists (not just a comment claiming it does) -- exercised by
   `test_registered_consumers_actually_enforce`.
"""

import ast
import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router import classify as classify_mod
from worktrail.router import policy as policy_mod
from worktrail.router.run_record import main as run_record_main

CLASSIFY_SRC = Path(classify_mod.__file__).resolve()
CASSETTE = CLASSIFY_SRC.parent / "cassettes" / "routing_cassette.json"


def extract_emitted_gate_strings() -> set:
    """Every literal string classify.py's `classify()` appends to `gates`."""
    tree = ast.parse(CLASSIFY_SRC.read_text())
    found = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "gates"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.add(node.args[0].value)
    return found


def _policy(max_risk="low", enabled=True):
    return {
        "automerge": {"enabled": enabled, "max_risk": max_risk, "target_branches": []},
        "_meta": {"external_automerge": {"detected": False}},
    }


def _proves_require_human_approval():
    """policy.automerge_eligible() denies eligibility whenever this gate is
    present, independent of risk (max_risk set to the loosest tier so only
    the gate itself can be responsible for the denial)."""
    eligible, _ = policy_mod.automerge_eligible(
        _policy(max_risk="critical"), "low", ["require_human_approval"], "main"
    )
    if eligible is not False:
        raise AssertionError(
            "require_human_approval did not block automerge eligibility"
        )


def _proves_never_automerge():
    """Same consumer as require_human_approval: automerge_eligible()'s first
    check ORs the two gate strings together."""
    eligible, _ = policy_mod.automerge_eligible(
        _policy(max_risk="critical"), "low", ["never_automerge"], "main"
    )
    if eligible is not False:
        raise AssertionError("never_automerge did not block automerge eligibility")


def _proves_pause_before_merge():
    """classify.py only ever appends this gate from the `elif risk == "high"`
    branch (never independently of risk), so its real consumer is
    automerge_eligible()'s risk-vs-policy-max_risk ceiling check, not a
    literal 'pause_before_merge' membership test anywhere -- confirmed absent
    by direct inspection of policy.py/run_record.py/pre_pr_gate.py. Prove the
    risk pairing this gate always carries is itself denied under a default
    (max_risk=low) policy."""
    eligible, reason = policy_mod.automerge_eligible(
        _policy(max_risk="low"), "high", ["pause_before_merge"], "main"
    )
    if eligible is not False:
        raise AssertionError(
            "risk='high' (pause_before_merge's invariant pairing) was not "
            f"denied by the policy max_risk ceiling: {reason}"
        )


def _proves_routing_cassette_required():
    """Route J's own playbook requires cassette coverage for routing changes;
    the mechanical enforcement is test_classify.py's TestRoutingCassette,
    which replays scripts/cassettes/routing_cassette.json and fails if a
    Route-J scenario's `gates_include: ["routing_cassette_required"]`
    expectation stops holding. Prove such a scenario currently exists."""
    data = json.loads(CASSETTE.read_text())
    matches = [
        sc
        for sc in data["scenarios"]
        if sc.get("expect", {}).get("route") == "J"
        and "routing_cassette_required" in sc.get("expect", {}).get("gates_include", [])
    ]
    if not matches:
        raise AssertionError(
            "no routing_cassette.json scenario asserts route J + "
            "gates_include: ['routing_cassette_required']"
        )


def _proves_no_implementation_without_approval():
    """run_record.py's cmd_finish() (PR #82) refuses to finish a Route-A run
    on an implementation-completion state unless a decision was recorded
    first -- keyed on selected_route=='A', which is exactly the condition
    under which classify.py emits this gate (route == 'A')."""
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
                    "A",
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
            return
        raise AssertionError(
            "Route A finished on an implementation-completion state with no "
            "recorded decision -- no_implementation_without_approval is unenforced"
        )


# gate string -> callable proving a real, behavior-changing consumer exists.
# Every string classify.py's `classify()` can emit MUST have an entry here.
GATE_CONSUMERS = {
    "require_human_approval": _proves_require_human_approval,
    "never_automerge": _proves_never_automerge,
    "pause_before_merge": _proves_pause_before_merge,
    "routing_cassette_required": _proves_routing_cassette_required,
    "no_implementation_without_approval": _proves_no_implementation_without_approval,
}


class TestGateEnforcementCoverage(unittest.TestCase):
    def test_every_emitted_gate_has_a_registered_consumer(self):
        emitted = extract_emitted_gate_strings()
        self.assertTrue(
            emitted,
            "extraction found no gates.append(...) calls -- "
            "classify.py's gate logic may have moved/renamed",
        )
        registered = set(GATE_CONSUMERS)
        missing = emitted - registered
        self.assertFalse(
            missing,
            f"classify.py emits gate string(s) {sorted(missing)} with no "
            "registered consumer in GATE_CONSUMERS -- add a proof function "
            "before shipping (see classify-gate-enforcement-audit.md)",
        )
        stale = registered - emitted
        self.assertFalse(
            stale,
            f"GATE_CONSUMERS registers {sorted(stale)}, which classify.py no "
            "longer emits -- remove the stale entry",
        )

    def test_registered_consumers_actually_enforce(self):
        for gate, proof in GATE_CONSUMERS.items():
            with self.subTest(gate=gate):
                proof()


if __name__ == "__main__":
    unittest.main()
