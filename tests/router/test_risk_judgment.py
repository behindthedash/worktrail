#!/usr/bin/env python3
"""Offline tests for the risk-tier judgment backend.

Nothing here touches the network or needs an API key: ``compose_tier`` is pure,
and the end-to-end cases replay the recorded answers in
``tests/fixtures/risk_probe_answers.json`` through an injected asker. That is
the point of recording them -- the tier mapping is the reviewable, regressable
part, and it has to stay testable on a machine with no key, which is every CI
runner.
"""

from __future__ import annotations

import json
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from worktrail.router import risk_judgment as rj
from worktrail.router.classify import classify, classify_risk

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
PROBES = json.loads((FIXTURES / "risk_probes.json").read_text(encoding="utf-8"))[
    "items"
]
ANSWERS = json.loads(
    (FIXTURES / "risk_probe_answers.json").read_text(encoding="utf-8")
)["answers"]

GATED = {"high", "critical"}


def _recorded_response(probe_id: str) -> dict:
    """The recorded answer for ``probe_id`` in the API's own response shape."""
    recorded = ANSWERS[probe_id]
    answers = {"blast_radius": {"score": recorded["blast_radius_score"]}}
    for key, value in recorded["nouls"].items():
        answers[key] = {"noul": value}
    return {"answers": answers}


class ComposeTierTest(unittest.TestCase):
    """The pure mapping from one answer set onto RISK_ORDER."""

    def test_score_alone_sets_the_baseline_tier(self):
        for score, expected in (
            (0.0, "low"),
            (1.0, "medium"),
            (2.0, "high"),
            (3.0, "critical"),
        ):
            tier, labels = rj.compose_tier(score, {})
            self.assertEqual(tier, expected)
            self.assertEqual(labels, [f"{expected}:blast-radius"])

    def test_score_is_rounded_and_clamped(self):
        self.assertEqual(rj.compose_tier(0.4, {})[0], "low")
        self.assertEqual(rj.compose_tier(0.6, {})[0], "medium")
        self.assertEqual(rj.compose_tier(-5.0, {})[0], "low")
        self.assertEqual(rj.compose_tier(99.0, {})[0], "critical")

    def test_a_hard_red_line_forces_critical_from_any_baseline(self):
        for key in ("irreversible_data_loss", "weakens_access_control", "moves_money"):
            with self.subTest(red_line=key):
                tier, labels = rj.compose_tier(0.0, {key: 0.95})
                self.assertEqual(tier, "critical")
                self.assertIn(f"critical:{key.replace('_', '-')}", labels)

    def test_a_weakened_safeguard_forces_high_not_critical(self):
        tier, labels = rj.compose_tier(0.0, {"disables_a_safeguard": 0.95})
        self.assertEqual(tier, "high")
        self.assertIn("high:disables-a-safeguard", labels)

    def test_a_red_line_never_lowers_a_higher_baseline(self):
        # "Severe" blast radius with only the safeguard line crossed must stay
        # critical -- a red line is a floor, not an assignment.
        tier, _ = rj.compose_tier(3.0, {"disables_a_safeguard": 0.95})
        self.assertEqual(tier, "critical")

    def test_a_noul_below_the_red_line_is_not_a_red_line(self):
        tier, labels = rj.compose_tier(
            0.0, {"irreversible_data_loss": rj.RED_LINE - 0.01}
        )
        self.assertEqual(tier, "low")
        self.assertEqual(labels, ["low:blast-radius"])

    def test_a_noul_exactly_at_the_red_line_counts(self):
        self.assertEqual(
            rj.compose_tier(0.0, {"irreversible_data_loss": rj.RED_LINE})[0], "critical"
        )

    def test_labels_keep_classify_risks_tier_prefixed_shape(self):
        _tier, labels = rj.compose_tier(1.0, {"moves_money": 0.99})
        self.assertEqual(labels, ["medium:blast-radius", "critical:moves-money"])


class ParseAnswersTest(unittest.TestCase):
    """A malformed response must fail into the fallback, not into a tier."""

    def test_a_well_formed_response_parses(self):
        score, nouls = rj.parse_answers(_recorded_response("kw-fp-1"))
        self.assertEqual(score, 0.0)
        self.assertEqual(set(nouls), set(rj._NOUL_KEYS))

    def test_missing_answers_mapping_raises(self):
        with self.assertRaises(ValueError):
            rj.parse_answers({})

    def test_missing_blast_radius_raises(self):
        response = _recorded_response("kw-fp-1")
        del response["answers"]["blast_radius"]
        with self.assertRaises(ValueError):
            rj.parse_answers(response)

    def test_a_missing_noul_raises_rather_than_reading_as_zero(self):
        response = _recorded_response("ok-crit-1")
        del response["answers"]["irreversible_data_loss"]
        with self.assertRaises(ValueError):
            rj.parse_answers(response)

    def test_a_non_numeric_score_raises(self):
        response = _recorded_response("kw-fp-1")
        response["answers"]["blast_radius"]["score"] = "high"
        with self.assertRaises(ValueError):
            rj.parse_answers(response)


class JudgeRiskFailureModeTest(unittest.TestCase):
    """Every failure mode must return None -- the "use the keyword table" signal."""

    def test_no_api_key_returns_none_without_calling_out(self):
        with (
            mock.patch.dict("os.environ", {rj.API_KEY_ENV: ""}, clear=False),
            mock.patch.object(rj, "ask", side_effect=AssertionError("called")),
        ):
            self.assertIsNone(rj.judge_risk("anything"))

    def test_http_error_returns_none(self):
        def raise_http(_text):
            raise urllib.error.HTTPError("u", 500, "boom", {}, None)

        self.assertIsNone(rj.judge_risk("x", asker=raise_http))

    def test_transport_error_returns_none(self):
        def raise_url(_text):
            raise urllib.error.URLError("no route to host")

        self.assertIsNone(rj.judge_risk("x", asker=raise_url))

    def test_timeout_returns_none(self):
        def raise_timeout(_text):
            raise TimeoutError("timed out")

        self.assertIsNone(rj.judge_risk("x", asker=raise_timeout))

    def test_unparseable_body_returns_none(self):
        def bad_json(_text):
            raise json.JSONDecodeError("bad", "", 0)

        self.assertIsNone(rj.judge_risk("x", asker=bad_json))

    def test_unexpected_answer_shape_returns_none(self):
        self.assertIsNone(rj.judge_risk("x", asker=lambda _t: {"answers": {}}))

    def test_a_good_answer_returns_the_composed_tier(self):
        # kw-fn-1 is the "delete every row" probe: a crossed hard red line, so
        # it composes to critical regardless of its blast-radius score.
        judged = rj.judge_risk("x", asker=lambda _t: _recorded_response("kw-fn-1"))
        self.assertIsNotNone(judged)
        self.assertEqual(judged[0], "critical")
        self.assertIn("critical:irreversible-data-loss", judged[1])


class ClassifyRiskBackendTest(unittest.TestCase):
    """`classify_risk`'s two backends and the fallback between them."""

    DOCS_ONLY_BILLING = "Fix a typo in the billing FAQ markdown page."

    def test_default_is_the_keyword_table_and_never_calls_out(self):
        with mock.patch.object(
            rj, "judge_risk", side_effect=AssertionError("judgment must not run")
        ):
            tier, labels = classify_risk(self.DOCS_ONLY_BILLING)
        self.assertEqual(tier, "critical")
        self.assertIn("critical:billing", labels)

    def test_classify_never_enables_the_judgment_by_default(self):
        with mock.patch.object(
            rj, "judge_risk", side_effect=AssertionError("judgment must not run")
        ):
            self.assertEqual(classify(self.DOCS_ONLY_BILLING)["risk"], "critical")

    def test_judgment_result_is_used_when_enabled(self):
        with mock.patch.object(
            rj, "judge_risk", return_value=("low", ["low:blast-radius"])
        ):
            self.assertEqual(
                classify_risk(self.DOCS_ONLY_BILLING, judgment=True),
                ("low", ["low:blast-radius"]),
            )

    def test_judgment_unavailable_falls_back_to_the_keyword_table(self):
        with mock.patch.object(rj, "judge_risk", return_value=None):
            tier, labels = classify_risk(self.DOCS_ONLY_BILLING, judgment=True)
        self.assertEqual(tier, "critical")
        self.assertIn("critical:billing", labels)

    def test_classify_passes_the_flag_through(self):
        with mock.patch.object(
            rj, "judge_risk", return_value=("low", ["low:blast-radius"])
        ):
            result = classify(self.DOCS_ONLY_BILLING, risk_judgment_enabled=True)
        self.assertEqual(result["risk"], "low")
        self.assertEqual(result["risk_signals"], ["low:blast-radius"])


class RecordedProbeSetTest(unittest.TestCase):
    """Replay the whole recorded probe set through the real composition."""

    def _judged(self, probe_id: str) -> str:
        return rj.judge_risk(
            "unused", asker=lambda _t, pid=probe_id: _recorded_response(pid)
        )[0]

    def test_every_probe_has_a_recorded_answer(self):
        self.assertEqual({p["id"] for p in PROBES}, set(ANSWERS))

    def test_the_judgment_never_rates_a_gated_change_mergeable(self):
        """The direction that matters: auto-merge lands low/medium unattended."""
        unsafe = [
            p["id"]
            for p in PROBES
            if p["truth"] in GATED and self._judged(p["id"]) not in GATED
        ]
        self.assertEqual(unsafe, [])

    def test_the_judgment_never_gates_a_mergeable_change(self):
        false_gates = [
            p["id"]
            for p in PROBES
            if p["truth"] not in GATED and self._judged(p["id"]) in GATED
        ]
        self.assertEqual(false_gates, [])

    def test_the_keyword_table_still_fails_this_probe_set_in_both_directions(self):
        """The premise, pinned. If the keyword table is ever fixed to pass
        these, this test fails and the judgment's justification must be
        re-argued rather than silently inherited."""
        keyword = {p["id"]: classify_risk(p["focus"])[0] for p in PROBES}
        unsafe = [
            p["id"]
            for p in PROBES
            if p["truth"] in GATED and keyword[p["id"]] not in GATED
        ]
        false_gates = [
            p["id"]
            for p in PROBES
            if p["truth"] not in GATED and keyword[p["id"]] in GATED
        ]
        self.assertEqual(len(unsafe), 7, unsafe)
        self.assertEqual(len(false_gates), 7, false_gates)

    def test_every_judged_tier_error_is_off_by_one(self):
        order = list(rj._TIERS)
        for probe in PROBES:
            judged = self._judged(probe["id"])
            with self.subTest(probe=probe["id"]):
                self.assertLessEqual(
                    abs(order.index(judged) - order.index(probe["truth"])), 1
                )


if __name__ == "__main__":
    unittest.main()
