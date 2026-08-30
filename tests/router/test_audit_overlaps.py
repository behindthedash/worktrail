#!/usr/bin/env python3
"""Unit tests for audit_overlaps.py — stdlib unittest, no pytest needed."""

import json
import tempfile
import unittest
from pathlib import Path

from worktrail.router.audit_overlaps import _jaccard, _score_label, _tokenise, audit


def _make_spec(root: Path, spec_id: str, summary: str) -> None:
    d = root / spec_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "2025-01-01--spec.md").write_text(f"**Feature Summary**: {summary}\n")


class TestTokenise(unittest.TestCase):
    def test_lowercases_and_splits(self):
        tokens = _tokenise("Donors Search Nonprofits")
        self.assertIn("donors", tokens)
        self.assertIn("search", tokens)
        self.assertIn("nonprofits", tokens)

    def test_removes_stopwords(self):
        tokens = _tokenise("the user can search for a nonprofit")
        self.assertNotIn("the", tokens)
        self.assertNotIn("for", tokens)
        self.assertNotIn("can", tokens)
        self.assertIn("search", tokens)
        self.assertIn("nonprofit", tokens)

    def test_removes_short_words(self):
        tokens = _tokenise("an AI to do it")
        # "AI" → "ai" is 2 chars, filtered; "do" is 2 chars, filtered
        self.assertNotIn("ai", tokens)
        self.assertNotIn("do", tokens)

    def test_empty_string(self):
        self.assertEqual(_tokenise(""), set())


class TestJaccard(unittest.TestCase):
    def test_identical_sets(self):
        s = {"search", "donor", "nonprofit"}
        self.assertAlmostEqual(_jaccard(s, s), 1.0)

    def test_disjoint_sets(self):
        self.assertAlmostEqual(_jaccard({"a", "b"}, {"c", "d"}), 0.0)

    def test_partial_overlap(self):
        a = {"search", "donor", "nonprofit"}
        b = {"search", "donor", "payment"}
        # intersection=2, union=4 → 0.5
        self.assertAlmostEqual(_jaccard(a, b), 0.5)

    def test_empty_sets(self):
        self.assertAlmostEqual(_jaccard(set(), {"a"}), 0.0)
        self.assertAlmostEqual(_jaccard(set(), set()), 0.0)


class TestScoreLabel(unittest.TestCase):
    def test_high(self):
        self.assertEqual(_score_label(0.4), "high")
        self.assertEqual(_score_label(0.9), "high")

    def test_medium(self):
        self.assertEqual(_score_label(0.25), "medium")
        self.assertEqual(_score_label(0.39), "medium")

    def test_low(self):
        self.assertEqual(_score_label(0.15), "low")
        self.assertEqual(_score_label(0.24), "low")


class TestAudit(unittest.TestCase):
    def test_no_specs(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = audit(Path(tmp))
            self.assertEqual(result["pairs"], [])
            self.assertEqual(result["total_specs"], 0)

    def test_missing_root(self):
        result = audit(Path("/nonexistent/path"))
        self.assertEqual(result["pairs"], [])

    def test_overlapping_pair_detected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(
                root,
                "001-donor-search",
                "Donors search nonprofits by cause location rating before donating.",
            )
            _make_spec(
                root,
                "002-nonprofit-search",
                "Donors search nonprofits by cause category and location.",
            )
            result = audit(root, min_score=0.1)
            self.assertEqual(len(result["pairs"]), 1)
            pair = result["pairs"][0]
            ids = {pair["spec_a"]["spec_id"], pair["spec_b"]["spec_id"]}
            self.assertIn("001-donor-search", ids)
            self.assertIn("002-nonprofit-search", ids)
            self.assertGreater(pair["token_score"], 0.1)

    def test_non_overlapping_pair_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(
                root,
                "001-auth",
                "Administrators manage login passwords sessions expiry.",
            )
            _make_spec(
                root,
                "002-billing",
                "Stripe payment invoices subscriptions checkout credit cards.",
            )
            result = audit(root, min_score=0.15)
            self.assertEqual(len(result["pairs"]), 0)

    def test_pairs_ranked_by_score(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(
                root,
                "001-search",
                "Donors search nonprofits cause location rating filter browse.",
            )
            _make_spec(
                root,
                "002-filter",
                "Donors search nonprofits cause location rating browse.",
            )
            _make_spec(
                root,
                "003-payments",
                "Stripe payment invoices subscriptions checkout credit cards.",
            )
            _make_spec(
                root,
                "004-checkout",
                "Payment checkout invoices billing subscriptions Stripe cards.",
            )
            result = audit(root, min_score=0.1)
            scores = [p["token_score"] for p in result["pairs"]]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_spec_without_summary_is_unscored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(root, "001-auth", "Login sessions passwords expiry management.")
            # spec with no summary (placeholder only)
            d = root / "002-empty"
            d.mkdir()
            (d / "2025-01-01--spec.md").write_text(
                "**Feature Summary**: ${FEATURE_SUMMARY}\n"
            )
            result = audit(root, min_score=0.1)
            unscored_ids = [u["spec_id"] for u in result["unscored"]]
            self.assertIn("002-empty", unscored_ids)

    def test_output_fields_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(
                root, "001-auth", "Login sessions passwords expiry management tokens."
            )
            _make_spec(
                root,
                "002-login",
                "Login authentication passwords sessions tokens expiry.",
            )
            result = audit(root, min_score=0.1)
            self.assertIn("pairs", result)
            self.assertIn("unscored", result)
            self.assertIn("total_specs", result)
            if result["pairs"]:
                pair = result["pairs"][0]
                for key in ("spec_a", "spec_b", "token_score", "score_label"):
                    self.assertIn(key, pair)
                for key in ("spec_id", "stage", "title", "feature_summary"):
                    self.assertIn(key, pair["spec_a"])

    def test_json_serialisable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(root, "001-auth", "Login sessions passwords expiry.")
            _make_spec(root, "002-login", "Login passwords sessions authentication.")
            result = audit(root, min_score=0.1)
            # Must not raise
            payload = json.loads(json.dumps(result))
            self.assertIn("pairs", payload)

    def test_extra_root_candidates_are_merged_into_the_same_corpus(self):
        """Same bug class as check_spec_collision.py/classify_handoff.py
        (brief 20260830-005833): `audit()` only ever scanned one root, so an
        overlap between a devkit-format spec and an OpenSpec-format spec was
        silently invisible."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "docs" / "specs"
            _make_spec(
                root,
                "001-donor-search",
                "Donors search nonprofits by cause location rating before donating.",
            )
            openspec_specs = Path(tmp) / "openspec" / "specs"
            (openspec_specs / "nonprofit-search").mkdir(parents=True)
            (openspec_specs / "nonprofit-search" / "spec.md").write_text(
                "# Nonprofit Search\n\n## Purpose\n\n"
                "Donors search nonprofits by cause category and location.\n"
            )
            result = audit(root, min_score=0.1, extra_roots=[Path(tmp) / "openspec"])
            self.assertEqual(result["total_specs"], 2)
            ids = {
                result["pairs"][0]["spec_a"]["spec_id"],
                result["pairs"][0]["spec_b"]["spec_id"],
            }
            self.assertEqual(ids, {"001-donor-search", "nonprofit-search"})

    def test_no_extra_roots_matches_prior_single_root_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(root, "001-auth", "Login sessions passwords expiry management.")
            result = audit(root, min_score=0.1)
            self.assertEqual(result["total_specs"], 1)

    def test_nonexistent_extra_root_is_a_silent_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(root, "001-auth", "Login sessions passwords expiry management.")
            result = audit(root, min_score=0.1, extra_roots=[Path(tmp) / "openspec"])
            self.assertEqual(result["total_specs"], 1)

    def test_three_specs_pairwise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _make_spec(
                root, "001-a", "Donation payment checkout credit cards invoices."
            )
            _make_spec(
                root, "002-b", "Payment donation checkout credit invoices billing."
            )
            _make_spec(
                root,
                "003-c",
                "Donation payment checkout credit invoices billing cards.",
            )
            result = audit(root, min_score=0.1)
            # 3 specs → up to 3 pairs (001-002, 001-003, 002-003)
            self.assertLessEqual(len(result["pairs"]), 3)
            self.assertGreater(len(result["pairs"]), 0)


if __name__ == "__main__":
    unittest.main()
