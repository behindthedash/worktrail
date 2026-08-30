#!/usr/bin/env python3
"""End-to-end front-door integration tests: classify -> policy -> merge gate.

The unit suites pin each script in isolation; these tests pin the COMPOSED
workflow outcome for every routing-cassette scenario, so a change to either
classify.py or policy.py that individually stays green but shifts the final
merge decision fails CI.

Run: python3 -m pytest tests/router/test_front_door.py -q
"""

import json
import tempfile
import unittest
from pathlib import Path

from worktrail.router import classify as _classify_mod
from worktrail.router.classify import classify
from worktrail.router.policy import automerge_eligible, load_policy

_CASSETTE = (
    Path(_classify_mod.__file__).resolve().parent
    / "cassettes"
    / "routing_cassette.json"
)

# Gates that must veto auto-merge no matter how permissive the policy is.
_BLOCKING_GATES = {"require_human_approval", "never_automerge"}


def _scenarios():
    return json.loads(_CASSETTE.read_text())["scenarios"]


def _permissive_policy(tmp, target="dev"):
    """The most permissive policy the loader accepts (max_risk tops out at medium)."""
    d = Path(tmp) / ".worktrail"
    d.mkdir(parents=True, exist_ok=True)
    (d / "policy.yaml").write_text(
        "automerge:\n  enabled: true\n  max_risk: medium\n"
        f"  target_branches:\n    - {target}\n"
    )
    return load_policy(Path(tmp))


class TestMergeGateEndToEnd(unittest.TestCase):
    """classify -> policy -> automerge_eligible, per cassette scenario."""

    def test_default_policy_blocks_every_scenario(self):
        """Safe default (no go-policy.yaml): nothing ever auto-merges."""
        default = load_policy(Path(tempfile.mkdtemp()))
        for s in _scenarios():
            with self.subTest(scenario=s["id"]):
                res = classify(s["request"], s.get("state"))
                ok, why = automerge_eligible(default, res["risk"], res["gates"], "main")
                self.assertFalse(ok, f"{s['id']} auto-merged under defaults: {why}")

    def test_permissive_policy_honors_classifier_gates(self):
        """Even fully-enabled policy: blocking gates and high+ risk still veto;
        everything else is eligible — pinning the composed decision per scenario."""
        pol = _permissive_policy(tempfile.mkdtemp())
        for s in _scenarios():
            with self.subTest(scenario=s["id"]):
                res = classify(s["request"], s.get("state"))
                ok, why = automerge_eligible(pol, res["risk"], res["gates"], "dev")
                gated = bool(_BLOCKING_GATES & set(res["gates"]))
                risky = res["risk"] in ("high", "critical")
                if gated or risky:
                    self.assertFalse(
                        ok,
                        f"{s['id']} must not auto-merge (gates={res['gates']}, "
                        f"risk={res['risk']}) but was eligible",
                    )
                else:
                    self.assertTrue(
                        ok,
                        f"{s['id']} should be eligible under permissive policy "
                        f"(gates={res['gates']}, risk={res['risk']}): {why}",
                    )

    def test_protected_operation_never_automerges(self):
        """The non-overridable invariant: protected ops veto regardless of policy."""
        pol = _permissive_policy(tempfile.mkdtemp())
        res = classify("drop the legacy_users table in production")
        self.assertTrue(res["protected_operations"])
        ok, why = automerge_eligible(pol, "low", res["gates"], "dev")
        self.assertFalse(ok)
        self.assertIn("protected", why)


if __name__ == "__main__":
    unittest.main(verbosity=2)
