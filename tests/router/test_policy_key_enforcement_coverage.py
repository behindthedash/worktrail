#!/usr/bin/env python3
"""Regression test closing the protected_paths/require_human_routes
enforcement gap (docs/specs/research/classify-gate-enforcement-audit.md's
"Aside: dead documentation reference" flagged these two `go-policy.yaml` keys
as parsed-and-validated but never wired to a real consumer -- a repo author
configuring either got silent no-op protection, not the gate the key's name
implies. This is the identical unenforced-safety-gate shape as
test_gate_enforcement_coverage.py's classify.py gate strings (PR #74/#80/#82).

POLICY_KEY_CONSUMERS registers a proof callable per enforceable
`go-policy.yaml` key: each callable behaviorally demonstrates that setting the
key actually changes `automerge_eligible()`'s decision (not just that a
comment claims it does). A future policy key added with the same
"parse it, warn about it, never wire it" shape has no automatic detector here
(unlike classify.py's gates, these keys aren't extracted from an AST walk --
see this module's docstring in the sibling test file for why that approach
doesn't transfer) -- but `test_registered_consumers_actually_enforce` at least
guarantees the two keys named in this audit can never silently regress back to
parse-only.
"""
import unittest

from worktrail.router.policy import automerge_eligible


def _policy(**overrides):
    base = {
        "automerge": {"enabled": True, "max_risk": "critical", "target_branches": []},
        "protected_paths": [],
        "require_human_routes": [],
        "_meta": {"external_automerge": {"detected": False}},
    }
    base.update(overrides)
    return base


def _proves_protected_paths():
    """A changed path matching a configured protected_paths pattern denies
    automerge eligibility, independent of risk/gates."""
    policy = _policy(protected_paths=["migrations/"])
    eligible, _ = automerge_eligible(
        policy, "low", [], "main", changed_paths=["migrations/0001_init.sql"])
    if eligible is not False:
        raise AssertionError(
            "protected_paths did not block automerge eligibility for a matching "
            "changed path")
    # And a non-matching path must NOT be blocked by the same policy.
    eligible, _ = automerge_eligible(
        policy, "low", [], "main", changed_paths=["README.md"])
    if eligible is not True:
        raise AssertionError(
            "protected_paths blocked a changed path that does not match any "
            "configured pattern")


def _proves_require_human_routes():
    """A classified route matching a configured require_human_routes entry
    denies automerge eligibility, independent of risk/gates."""
    policy = _policy(require_human_routes=["D"])
    eligible, _ = automerge_eligible(policy, "low", [], "main", route="D")
    if eligible is not False:
        raise AssertionError(
            "require_human_routes did not block automerge eligibility for a "
            "listed route")
    # And an unlisted route must NOT be blocked by the same policy.
    eligible, _ = automerge_eligible(policy, "low", [], "main", route="F")
    if eligible is not True:
        raise AssertionError(
            "require_human_routes blocked a route that is not in the "
            "configured list")


# go-policy.yaml key -> callable proving a real, behavior-changing consumer
# exists. Add an entry here for any future key documented as a merge/PR gate.
POLICY_KEY_CONSUMERS = {
    "protected_paths": _proves_protected_paths,
    "require_human_routes": _proves_require_human_routes,
}


class TestPolicyKeyEnforcementCoverage(unittest.TestCase):

    def test_registered_consumers_actually_enforce(self):
        for key, proof in POLICY_KEY_CONSUMERS.items():
            with self.subTest(key=key):
                proof()


if __name__ == "__main__":
    unittest.main()
