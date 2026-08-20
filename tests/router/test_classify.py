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
                                   pr_states=sc.get("pr_states"),
                                   resumable_state=sc.get("resumable_state"))
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

    def test_handoff_route_does_not_override_broad_organic_agreement(self):
        # Reproduces the 20260815-114628 incident: a stale/wrong brief
        # recommendation (B) absent from organic scores must not beat an
        # organic pick (J) that has independent corroboration from a nonzero
        # runner-up (F) — unlike test_handoff_route_overrides_low_confidence_
        # disagreement above, where the organic runner-up scored zero (a lone
        # spike, plausibly a keyword coincidence) rather than a genuine
        # second candidate.
        r = classify(
            "a small explicit-gh-path-resolution fix to dependabot-pullhook-"
            "dispatch.py classifier subprocess call",
            handoff_route="B")
        self.assertEqual(r["route"], "J")
        self.assertEqual(r["confidence"], "medium")
        self.assertEqual(r["route_source"], "classifier")

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

    def test_zero_signal_default_reports_distinct_route_source(self):
        # A content-free request that scores no route at all falls back to
        # E/low, but must be distinguishable from a real (if low-confidence)
        # classification so callers like create_handoff._route_for() can
        # suppress stamping it as a recommended-route (20260731-151701).
        r = classify("Standardize the shared helper used by two call sites")
        self.assertEqual(r["route"], "E")
        self.assertEqual(r["confidence"], "low")
        self.assertEqual(r["ambiguous_between"], [])
        self.assertEqual(r["route_source"], "no-signal-default")

    def test_zero_signal_default_still_overridable_by_handoff_route(self):
        # At dispatch time (handoff_route supplied), the zero-signal default
        # must still be overridable like any other low-confidence pick --
        # only brief-creation-time stamping (no handoff_route) is suppressed.
        r = classify("Standardize the shared helper used by two call sites",
                     handoff_route="H")
        self.assertEqual(r["route"], "H")
        self.assertEqual(r["route_source"], "handoff-recommended-override")

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


class TestResumableState(unittest.TestCase):
    """resumable_state=False (from check_resumable_state.py) disqualifies Route E
    outright, regardless of how strongly the text scores it. Reproduces the
    20260812-163747 incident: a brief *reporting* the E-default bug used the
    exact vocabulary ("continue/resume", "handoff", "worktree", "open PR")
    that the E signals themselves match, so the classifier organically scored
    its own bug report as a Route E resume."""

    INCIDENT_TEXT = (
        "classify-handoff's route hint defaults to E (continue/resume) for "
        "brand-new queue briefs with no prior run, driven by noisy token-overlap "
        "scoring rather than a real match. In an attended /go session this is "
        "recoverable manually, but worktrail-drain's unattended one-shots have "
        "no one to catch a bad E-route reconstruction. Harden classify-handoff/"
        "classify.py so a fresh claim (no existing run record, no worktree, no "
        "open PR) doesn't default to E, or add a mechanical pre-check in Route "
        "E's own reconstruction step that reroutes immediately when no "
        "resumable state is found."
    )

    def test_default_none_reproduces_the_incident(self):
        # Documents the bug this fix addresses: without a mechanical check,
        # the classifier's organic score for this exact text is E.
        r = classify(self.INCIDENT_TEXT, handoff_route="E")
        self.assertEqual(r["route"], "E")

    def test_resumable_state_false_disqualifies_e(self):
        r = classify(self.INCIDENT_TEXT, handoff_route="E", resumable_state=False)
        self.assertNotEqual(r["route"], "E")
        self.assertEqual(r["route"], "J")
        # scores omits non-positive entries by design; E's disqualification
        # (sentinel -1) means it no longer appears at all.
        self.assertNotIn("E", r["scores"])

    def test_resumable_state_false_survives_stale_handoff_route_hint(self):
        # The brief's own recommended-route: E frontmatter must not put E back
        # via the handoff-recommended-override path once the mechanical check
        # has ruled it out.
        r = classify(self.INCIDENT_TEXT, handoff_route="E", resumable_state=False)
        self.assertNotEqual(r["route_source"], "handoff-recommended-override")

    def test_resumable_state_true_preserves_organic_pick(self):
        r = classify(self.INCIDENT_TEXT, handoff_route="E", resumable_state=True)
        self.assertEqual(r["route"], "E")

    def test_resumable_state_none_is_unchanged_legacy_behavior(self):
        r_none = classify("fix the broken receipt date", resumable_state=None)
        r_default = classify("fix the broken receipt date")
        self.assertEqual(r_none, r_default)

    def test_zero_signal_text_does_not_default_to_e_when_not_resumable(self):
        r = classify("Standardize the shared helper used by two call sites",
                     resumable_state=False)
        self.assertNotEqual(r["route"], "E")
        self.assertNotEqual(r["route_source"], "no-signal-default")

    def test_zero_signal_text_still_defaults_to_e_without_the_check(self):
        r = classify("Standardize the shared helper used by two call sites",
                     resumable_state=None)
        self.assertEqual(r["route"], "E")
        self.assertEqual(r["route_source"], "no-signal-default")


class TestEpicWordIntent(unittest.TestCase):
    """The B ('epic-planning') scoring previously fired its 'epic-word' signal on
    any bare mention of the word "epic", not just a genuine epic-planning request.
    Reproduces the 20260819-212544 dispatch: a defect report about Route B's own
    epic-closure check (three incidental mentions of "epic" -- a compound
    descriptor and a numbered id reference, no planning intent) organically
    scored B=4 purely from keyword collision."""

    INCIDENT_TEXT = (
        "worktrail-go/sdd-workflow's Route B epic-closure has no check for "
        "unresolved PROVISIONAL decisions before marking status: completed. "
        "Pullhook epic 001 was closed by PR #72 while its own Feature C "
        "decision still said PROVISIONAL pending spec 002 shipping -- it sat "
        "stale across 3 doc locations for weeks, undetected. This can recur "
        "silently in any repo using Route B: add a check to the epic-closure "
        "step that scans an epic's linked decision/research docs for an "
        "unresolved PROVISIONAL marker and blocks status: completed until "
        "it's resolved."
    )

    def test_incidental_epic_mentions_do_not_score_b(self):
        r = classify(self.INCIDENT_TEXT)
        self.assertNotIn("B", r["scores"])

    def test_genuine_epic_planning_request_still_scores_b(self):
        r = classify("Plan an epic: a donor management platform with several "
                      "features delivered across phases")
        self.assertEqual(r["route"], "B")
        self.assertEqual(r["confidence"], "high")

    def test_break_down_into_epic_phrasing_scores_b(self):
        r = classify("Break this down into an epic with several independently "
                      "valuable features")
        self.assertIn("B", r["scores"])

    def test_create_epic_for_phrasing_scores_b(self):
        r = classify("Create an epic for the donor portal covering signup, "
                      "payments, and reporting")
        self.assertIn("B", r["scores"])


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
