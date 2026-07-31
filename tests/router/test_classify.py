#!/usr/bin/env python3
"""Tests for classify.py — unit rules + the golden routing cassette.

The cassette (cassettes/routing_cassette.json) is the adverse-effect gate for
Route J (workflow) changes: any change to GO's routing must keep it green.
Run: python3 test_classify.py
"""
import json
import unittest
from pathlib import Path

from worktrail.router import classify as _classify_mod
from worktrail.router.classify import classify, classify_risk, protected_operations

CASSETTE = Path(_classify_mod.__file__).resolve().parent / "cassettes" / "routing_cassette.json"


class TestRoutingCassette(unittest.TestCase):
    """Replay every golden scenario and assert its expectations exactly."""

    def test_cassette_scenarios(self):
        data = json.loads(CASSETTE.read_text())
        self.assertGreaterEqual(len(data["scenarios"]), 20)
        for sc in data["scenarios"]:
            with self.subTest(scenario=sc["id"]):
                result = classify(sc["request"], state=sc.get("state"),
                                   handoff_route=sc.get("handoff_route"),
                                   pr_states=sc.get("pr_states"))
                exp = sc["expect"]
                if "route" in exp:
                    self.assertEqual(result["route"], exp["route"],
                                     f"{sc['id']}: {result['reason']}")
                if "risk" in exp:
                    self.assertEqual(result["risk"], exp["risk"], sc["id"])
                if "confidence" in exp:
                    self.assertEqual(result["confidence"], exp["confidence"],
                                     f"{sc['id']}: {result['reason']}")
                if "route_source" in exp:
                    self.assertEqual(result["route_source"], exp["route_source"],
                                     f"{sc['id']}: {result['reason']}")
                if "ambiguous_between" in exp:
                    self.assertEqual(result["ambiguous_between"],
                                     exp["ambiguous_between"], sc["id"])
                    self.assertTrue(result["question"], sc["id"])
                if "secondary_includes" in exp:
                    for r in exp["secondary_includes"]:
                        self.assertIn(r, result["secondary"], sc["id"])
                if "gates_include" in exp:
                    for g in exp["gates_include"]:
                        self.assertIn(g, result["gates"], sc["id"])
                if "gates_exactly" in exp:
                    self.assertEqual(result["gates"], exp["gates_exactly"], sc["id"])

    def test_every_scenario_has_assertions(self):
        data = json.loads(CASSETTE.read_text())
        for sc in data["scenarios"]:
            self.assertTrue(sc.get("expect"), f"{sc['id']} asserts nothing")


class TestOverridesAndSignals(unittest.TestCase):
    def test_explicit_route_override(self):
        r = classify("route:H tidy up whatever you think is best")
        self.assertEqual(r["route"], "H")
        self.assertEqual(r["confidence"], "high")

    def test_handoff_recommended_route_boosts(self):
        r = classify("retry support for payments processing queue",
                     handoff_route="C")
        self.assertEqual(r["route"], "C")

    def test_handoff_route_invalid_letter_ignored(self):
        r = classify("fix the broken receipt date", handoff_route="Z")
        self.assertEqual(r["route"], "F")

    def test_handoff_route_overrides_low_confidence_disagreement(self):
        # Reproduces the 20260717-091439 incident: a spurious low-signal hit
        # for another route must not beat an explicit brief recommendation
        # when auto mode has no human present to catch it.
        r = classify("Extend the unit test suite for a platform component",
                     handoff_route="H")
        self.assertEqual(r["route"], "H")
        self.assertEqual(r["confidence"], "medium")
        self.assertEqual(r["route_source"], "handoff-recommended-override")
        self.assertEqual(r["ambiguous_between"], [])
        self.assertIsNone(r["question"])

    def test_handoff_route_does_not_override_high_confidence(self):
        # A high-confidence organic disagreement is a signal worth a fresh
        # look, not a silent override — repo state may have drifted since
        # the brief's recommended-route was written.
        r = classify("I have an idea — what if contributors could earn badges?",
                     handoff_route="H")
        self.assertEqual(r["confidence"], "high")
        self.assertEqual(r["route"], "A")
        self.assertEqual(r["route_source"], "classifier")

    def test_handoff_route_agreement_reports_classifier_source(self):
        r = classify("retry support for payments processing queue",
                     handoff_route="C")
        self.assertEqual(r["route"], "C")
        self.assertEqual(r["route_source"], "classifier")

    def test_no_handoff_route_reports_classifier_source(self):
        r = classify("fix the broken receipt date")
        self.assertEqual(r["route_source"], "classifier")

    def test_state_demotes_implementation_without_spec(self):
        with_spec = classify("implement the new widget",
                             state={"active_specs": 1})
        without = classify("implement the new widget",
                           state={"active_specs": 0})
        self.assertGreaterEqual(
            with_spec["scores"].get("D", 0), without["scores"].get("D", 0) + 2)

    def test_empty_request_defaults_to_dashboard(self):
        r = classify("")
        self.assertEqual(r["route"], "E")
        self.assertEqual(r["confidence"], "low")

    def test_investigation_beats_defect_when_cause_unknown(self):
        r = classify("diagnose why checkout intermittently errors out")
        self.assertEqual(r["route"], "I")

    def test_investigation_word_form_variants_still_score(self):
        # "investigation" (noun) previously missed the "investigate" (verb-only)
        # signal entirely, so an explicitly investigation-framed brief scored a
        # wrong-but-plausible route instead of I.
        r = classify("This is a 'Needs investigation' item. Do not fix findings "
                     "in this brief. Recommend per-repo follow-up briefs.")
        self.assertEqual(r["route"], "I")
        self.assertEqual(r["confidence"], "high")

    def test_investigate_mention_with_known_cause_stays_defect_repair(self):
        # Guards against over-correcting toward I: a passing "investigate"
        # mention should not outrank a request with an already-known cause
        # and an explicit fix ask.
        r = classify("The crash is caused by a missing null check. Fix it; "
                     "no need to investigate further.")
        self.assertEqual(r["route"], "F")

    def test_classify_py_self_reference_routes_to_workflow_evolution(self):
        r = classify("classify.py never scores Route I (Investigation) as a "
                     "candidate, causing medium-confidence misroutes into "
                     "spec-authoring/defect-repair pipelines for explicitly "
                     "investigation-framed requests")
        self.assertEqual(r["route"], "J")

    def test_ci_repair_forces_continue_route(self):
        r = classify("the bug is that CI is broken on my branch")
        self.assertEqual(r["route"], "E")
        self.assertIn("F", r["secondary"])

    def test_new_ci_gate_is_not_misclassified_as_ci_repair(self):
        r = classify("Add a new CI job that fails the PR when generated files drift")
        self.assertNotEqual(r["route"], "E")

    def test_pr_repair_signal_suppressed_for_merged_pr(self):
        # Reproduces the 20260730-203004 incident: a cited PR number alone
        # forced Route E even though the PR was already merged hours earlier.
        text = "PR #68 is broken and needs fixing"
        unknown = classify(text)
        self.assertEqual(unknown["route"], "E")
        merged = classify(text, pr_states={"68": "MERGED"})
        self.assertNotEqual(merged["route"], "E")

    def test_pr_repair_signal_still_fires_for_open_pr(self):
        text = "PR #68 is broken and needs fixing"
        r = classify(text, pr_states={"68": "OPEN"})
        self.assertEqual(r["route"], "E")

    def test_pr_repair_signal_fires_when_pr_state_unknown(self):
        # No pr_states info at all -> fail-open toward the original behavior.
        text = "PR #68 is broken and needs fixing"
        r = classify(text, pr_states={})
        self.assertEqual(r["route"], "E")

    def test_pr_repair_signal_requires_all_cited_prs_settled(self):
        # Two PRs cited; only one is confirmed merged -> signal still fires,
        # since the other citation is unresolved/still live.
        text = "PR #68 is broken; also check PR #70 which is failing"
        r = classify(text, pr_states={"68": "MERGED"})
        self.assertEqual(r["route"], "E")

    def test_other_ci_repair_signals_unaffected_by_pr_states(self):
        # pr_states only suppresses the pr-repair label; a real CI-failure
        # phrase with no PR citation must still force Route E.
        r = classify("the bug is that CI is broken on my branch",
                     pr_states={"68": "MERGED"})
        self.assertEqual(r["route"], "E")


class TestCitedPrStates(unittest.TestCase):
    """cited_pr_states/_pr_state — the only live-I/O boundary in this module,
    exercised here with an injected fake runner (no real `gh`/network calls)."""

    def test_no_repo_returns_empty(self):
        from worktrail.router.classify import cited_pr_states
        self.assertEqual(cited_pr_states("PR #68 is broken", None), {})

    def test_no_cited_pr_returns_empty_without_calling_runner(self):
        from worktrail.router.classify import cited_pr_states

        def _runner(*a, **kw):
            raise AssertionError("runner should not be called with no cited PR")

        self.assertEqual(cited_pr_states("fix the login bug", Path("."), _runner), {})

    def test_resolves_cited_pr_state(self):
        from worktrail.router.classify import cited_pr_states

        class _Result:
            returncode = 0
            stdout = json.dumps({"state": "MERGED"})

        calls = []

        def _runner(cmd, **kw):
            calls.append(cmd)
            return _Result()

        states = cited_pr_states("PR #68 is broken and needs fixing", Path("."), _runner)
        self.assertEqual(states, {"68": "MERGED"})
        self.assertEqual(calls[0][:3], ["gh", "pr", "view"])

    def test_failed_lookup_is_fail_open(self):
        from worktrail.router.classify import cited_pr_states

        class _Result:
            returncode = 1
            stdout = ""

        states = cited_pr_states("PR #68 is broken", Path("."), lambda *a, **kw: _Result())
        self.assertEqual(states, {})

    def test_runner_exception_is_fail_open(self):
        from worktrail.router.classify import cited_pr_states

        def _runner(*a, **kw):
            raise OSError("gh not found")

        states = cited_pr_states("PR #68 is broken", Path("."), _runner)
        self.assertEqual(states, {})


class TestRiskAndProtection(unittest.TestCase):
    def test_risk_tiers(self):
        self.assertEqual(classify_risk("update the readme docs")[0], "low")
        self.assertEqual(classify_risk("change the public api endpoint")[0], "medium")
        self.assertEqual(classify_risk("add a login permission check")[0], "high")
        self.assertEqual(classify_risk("rotate the stripe billing secrets")[0], "critical")

    def test_highest_tier_wins(self):
        risk, labels = classify_risk("docs for the billing migration")
        self.assertEqual(risk, "critical")
        self.assertTrue(any(l.startswith("critical:") for l in labels))

    def test_protected_operations(self):
        self.assertIn("destructive-migration",
                      protected_operations("drop table users"))
        self.assertIn("auth-weakening",
                      protected_operations("bypass auth checks for testing"))
        self.assertEqual(protected_operations("add a tooltip"), [])

    def test_protected_implies_gates(self):
        r = classify("implement spec 002-x and drop the old_events table",
                     state={"active_specs": 1})
        self.assertIn("require_human_approval", r["gates"])
        self.assertIn("never_automerge", r["gates"])

    def test_high_risk_gates_merge_pause(self):
        r = classify("fix the role permission check on the admin endpoint")
        self.assertIn("pause_before_merge", r["gates"])


class TestCompletionContract(unittest.TestCase):
    def test_result_shape(self):
        r = classify("add a feature")
        for key in ("route", "route_name", "secondary", "risk", "gates",
                    "confidence", "ambiguous_between", "question", "reason",
                    "scores", "protected_operations", "risk_signals"):
            self.assertIn(key, r)
        self.assertIn(r["route"], "ABCDEFGHIJ")
        self.assertIn(r["risk"], ("low", "medium", "high", "critical"))
        self.assertIn(r["confidence"], ("low", "medium", "high"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
