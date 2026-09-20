#!/usr/bin/env python3
"""Offline tests for the brief-relatedness judgment backend.

No network and no credential: `should_edge` is pure, and every end-to-end case
injects the verdict. The measured pairs this is built on are quoted in the
fixtures below so the cases assert the behaviour the evidence actually showed,
not a made-up one.
"""

from __future__ import annotations

import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

import yaml

from worktrail.router import cluster_detect as cd
from worktrail.router import cluster_telemetry
from worktrail.router import relatedness_judgment as rjm


def _parse_fm(text: str) -> dict:
    """The frontmatter loader `compute_clusters` takes by injection."""
    body = text.split("---", 2)
    return yaml.safe_load(body[1]) if len(body) > 2 else {}


# Measured 2026-09-19: identical work, 0.00-0.12 lexical overlap, 0.76-0.86
# same_work. The lexical rule cannot see this pair at all.
PARAPHRASE_A = (
    "The nightly drain digest never reached the operator because the digest "
    "script was missing from the bin directory after the deploy."
)
PARAPHRASE_B = (
    "Operators get no end-of-night summary: the scheduled roll-up job cannot "
    "find its executable on the box, so the mail is silently skipped."
)

# High lexical overlap, unrelated work -- the other direction, 7 of the 14
# highest-overlap corpus pairs.
LOOKALIKE_A = (
    "The admin reject dialog closes before the rejection reason is persisted, "
    "so the operator's note is lost on every reject action in the console."
)
LOOKALIKE_B = (
    "The admin console's vitest coverage is below the configured threshold, so "
    "the reject dialog and its sibling components are effectively untested."
)


def _answers(same_work: float, should_cluster: float) -> dict:
    return {
        "answers": {
            "same_work": {"noul": same_work},
            "should_cluster": {"noul": should_cluster},
        }
    }


class ShouldEdgeTest(unittest.TestCase):
    """The pure rule turning two Nouls into an edge."""

    def test_same_work_alone_makes_an_edge(self):
        self.assertTrue(rjm.should_edge(0.85, 0.10))

    def test_should_cluster_alone_makes_an_edge(self):
        """Distinct work that must land together is the case the second
        question exists for -- a conjunction would drop it."""
        self.assertTrue(rjm.should_edge(0.10, 0.85))

    def test_neither_makes_no_edge(self):
        self.assertFalse(rjm.should_edge(0.10, 0.10))

    def test_the_threshold_is_inclusive(self):
        self.assertTrue(rjm.should_edge(rjm.JUDGMENT_THRESHOLD, 0.0))
        self.assertFalse(rjm.should_edge(rjm.JUDGMENT_THRESHOLD - 0.01, 0.0))


class JudgePairTest(unittest.TestCase):
    """Every failure mode returns None -- "keep the lexical decision"."""

    def test_no_credential_returns_none_without_calling_out(self):
        with (
            mock.patch.dict("os.environ", {rjm.typesafe.API_KEY_ENV: ""}, clear=False),
            mock.patch.object(rjm, "ask", side_effect=AssertionError("called")),
        ):
            self.assertIsNone(rjm.judge_pair("a", "b"))

    def test_http_error_returns_none(self):
        def boom(_a, _b):
            raise urllib.error.HTTPError("u", 500, "boom", {}, None)

        self.assertIsNone(rjm.judge_pair("a", "b", asker=boom))

    def test_transport_error_returns_none(self):
        def boom(_a, _b):
            raise urllib.error.URLError("no route")

        self.assertIsNone(rjm.judge_pair("a", "b", asker=boom))

    def test_timeout_returns_none(self):
        def boom(_a, _b):
            raise TimeoutError("timed out")

        self.assertIsNone(rjm.judge_pair("a", "b", asker=boom))

    def test_unparseable_body_returns_none(self):
        def boom(_a, _b):
            raise json.JSONDecodeError("bad", "", 0)

        self.assertIsNone(rjm.judge_pair("a", "b", asker=boom))

    def test_a_missing_noul_returns_none_rather_than_reading_as_zero(self):
        self.assertIsNone(
            rjm.judge_pair(
                "a", "b", asker=lambda _a, _b: {"answers": {"same_work": {"noul": 0.9}}}
            )
        )

    def test_a_good_answer_returns_the_verdict_and_both_values(self):
        verdict = rjm.judge_pair("a", "b", asker=lambda _a, _b: _answers(0.83, 0.77))
        self.assertEqual(verdict, (True, 0.83, 0.77))


class _SignalFixture(unittest.TestCase):
    """Builds real Cluster Signals through `_extract_signal`, so these tests
    exercise the same dict shape production does rather than a hand-rolled
    subset that silently omits a key (`blocked_by`, `slug`, ...)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self._n = 0

    def _sig(self, stem: str, focus: str, repo: str | None = "/r", **extra):
        self._n += 1
        path = self.dir / f"2026010{self._n % 10}-00000{self._n % 10}-{stem}.md"
        lines = [f"focus: |-\n  {focus}", f"repo: {repo if repo else 'null'}"]
        for key, value in extra.items():
            lines.append(f"{key}: {value}")
        path.write_text("---\n" + "\n".join(lines) + "\n---\n", encoding="utf-8")
        sig = cd._extract_signal(path, _parse_fm)
        assert sig is not None
        sig["stem"] = stem  # stable, readable ids for the assertions below
        return sig

    def _edges(self, signals):
        return [
            (a["stem"], b["stem"], cd._signal_matches(a, b))
            for i, a in enumerate(signals)
            for b in signals[i + 1 :]
        ]


class JudgePairsBatchTest(unittest.TestCase):
    """The batch entry point: order-preserving, bounded, and fail-safe."""

    def test_verdicts_are_positionally_aligned_with_the_input(self):
        pairs = [("a1", "b1"), ("a2", "b2"), ("a3", "b3")]
        scores = {"a1": 0.9, "a2": 0.1, "a3": 0.8}

        def asker(a, _b):
            return _answers(scores[a], 0.0)

        verdicts = rjm.judge_pairs(pairs, asker=asker)

        self.assertEqual([v[0] for v in verdicts], [True, False, True])
        self.assertEqual([v[1] for v in verdicts], [0.9, 0.1, 0.8])

    def test_one_unavailable_pair_does_not_sink_the_others(self):
        def asker(a, _b):
            if a == "bad":
                raise TimeoutError("timed out")
            return _answers(0.9, 0.9)

        verdicts = rjm.judge_pairs(
            [("ok", "x"), ("bad", "y"), ("ok", "z")], asker=asker
        )

        self.assertEqual([v is None for v in verdicts], [False, True, False])

    def test_an_empty_batch_makes_no_call(self):
        self.assertEqual(
            rjm.judge_pairs(
                [], asker=lambda *_: (_ for _ in ()).throw(AssertionError())
            ),
            [],
        )

    def test_no_credential_returns_all_none_without_calling_out(self):
        with mock.patch.object(rjm, "is_configured", return_value=False):
            self.assertEqual(rjm.judge_pairs([("a", "b"), ("c", "d")]), [None, None])


class JudgmentCandidateSelectionTest(_SignalFixture):
    """Which pairs are offered to the judgment, and in what order."""

    def test_a_cross_repo_pair_is_never_offered(self):
        signals = [
            self._sig("a", PARAPHRASE_A, repo="/one"),
            self._sig("b", PARAPHRASE_A, repo="/two"),
        ]
        self.assertEqual(cd._judgment_candidates(signals, self._edges(signals)), [])

    def test_a_pair_with_a_thin_brief_is_never_offered(self):
        """A brief too short for the lexical rule to read is one the judgment
        cannot read either -- the MIN_FOCUS_TOKENS abstention still holds."""
        signals = [
            self._sig("a", "canonical checkout drift"),
            self._sig("b", PARAPHRASE_A),
        ]
        self.assertEqual(cd._judgment_candidates(signals, self._edges(signals)), [])

    def test_a_pair_below_the_prefilter_floor_is_not_offered(self):
        signals = [
            self._sig(
                "a", "The nightly drain digest never reached the operator after deploy"
            ),
            self._sig(
                "b",
                "Syncfusion grid columns render misaligned whenever virtual scrolling is enabled",
            ),
        ]
        candidates = cd._judgment_candidates(signals, self._edges(signals))
        self.assertEqual(candidates, [])

    def test_candidates_are_ranked_by_overlap_descending(self):
        signals = [
            self._sig("a", PARAPHRASE_A),
            self._sig("b", PARAPHRASE_A + " and also the mail is skipped entirely"),
            self._sig("c", PARAPHRASE_B),
        ]
        candidates = cd._judgment_candidates(signals, self._edges(signals))
        overlaps = [score for _index, score in candidates]
        self.assertEqual(overlaps, sorted(overlaps, reverse=True))

    def test_the_per_run_cap_is_enforced(self):
        signals = [
            self._sig(
                f"b{n:03d}", PARAPHRASE_A + f" variant {n} of the same digest failure"
            )
            for n in range(40)
        ]
        candidates = cd._judgment_candidates(signals, self._edges(signals))
        self.assertEqual(len(candidates), cd.MAX_JUDGED_PAIRS)


class ApplyRelatednessJudgmentTest(_SignalFixture):
    """How a verdict rewrites the pair's matches."""

    def test_a_paraphrase_pair_invisible_to_the_lexical_rule_gains_an_edge(self):
        signals = [self._sig("a", PARAPHRASE_A), self._sig("b", PARAPHRASE_B)]
        edges = self._edges(signals)
        self.assertEqual(
            edges[0][2], [], "premise: no lexical match on a paraphrase pair"
        )

        updated = cd._apply_relatedness_judgment(
            signals,
            edges,
            judge_pairs_fn=lambda pairs: [(True, 0.83, 0.77)] * len(pairs),
            log_judged_fn=lambda _r: None,
        )

        self.assertEqual(updated[0][2], [(cd.JUDGED_MATCH, 0.83)])

    def test_a_high_overlap_pair_judged_unrelated_loses_its_edge(self):
        focus = PARAPHRASE_A
        signals = [self._sig("a", focus), self._sig("b", focus)]
        edges = self._edges(signals)
        self.assertEqual([m[0] for m in edges[0][2]], ["focus-overlap"], "premise")

        updated = cd._apply_relatedness_judgment(
            signals,
            edges,
            judge_pairs_fn=lambda pairs: [(False, 0.05, 0.08)] * len(pairs),
            log_judged_fn=lambda _r: None,
        )

        self.assertEqual(updated[0][2], [])

    def test_structural_matches_are_never_removed(self):
        """duplicate-slug / same-target-spec / related-link are facts about the
        briefs, not a guess about their text."""
        a = self._sig("a", PARAPHRASE_A)
        b = self._sig("b", PARAPHRASE_A)
        a["target_spec"] = b["target_spec"] = "some-change"
        edges = [("a", "b", [("same-target-spec", None), ("focus-overlap", 1.0)])]

        updated = cd._apply_relatedness_judgment(
            [a, b],
            edges,
            judge_pairs_fn=lambda pairs: [(False, 0.0, 0.0)] * len(pairs),
            log_judged_fn=lambda _r: None,
        )

        self.assertEqual(updated[0][2], [("same-target-spec", None)])

    def test_no_injected_judgment_leaves_every_edge_untouched(self):
        """What a caller with no credential passes: the lexical threshold stays
        the whole rule, exactly as before this capability existed."""
        signals = [self._sig("a", PARAPHRASE_A), self._sig("b", PARAPHRASE_A)]
        edges = self._edges(signals)

        self.assertEqual(
            cd._apply_relatedness_judgment(signals, edges, judge_pairs_fn=None), edges
        )

    def test_everything_after_the_first_unavailable_verdict_is_ignored(self):
        """Applying a later verdict past a gap would make the clusters depend on
        which round trip failed, which is not something a render may vary on."""
        signals = [
            self._sig(f"b{n}", PARAPHRASE_A + f" variant {n} of the digest failure")
            for n in range(4)
        ]
        edges = self._edges(signals)
        # First verdict good, second unavailable, the rest good-but-ignored.
        verdicts = [(True, 0.9, 0.9), None, (True, 0.9, 0.9), (True, 0.9, 0.9)]
        recorded: list[list[dict]] = []

        cd._apply_relatedness_judgment(
            signals,
            edges,
            judge_pairs_fn=lambda pairs: verdicts[: len(pairs)],
            log_judged_fn=recorded.append,
        )

        self.assertEqual(len(recorded[0]), 1, "only the pre-gap verdict is applied")

    def test_the_whole_batch_is_offered_in_one_call(self):
        """The batch shape is what lets the caller run the round trips
        concurrently -- this module must not call once per pair."""
        signals = [
            self._sig(f"b{n}", PARAPHRASE_A + f" variant {n} of the digest failure")
            for n in range(4)
        ]
        calls: list[int] = []

        def batch(pairs):
            calls.append(len(pairs))
            return [(False, 0.0, 0.0)] * len(pairs)

        cd._apply_relatedness_judgment(
            signals,
            self._edges(signals),
            judge_pairs_fn=batch,
            log_judged_fn=lambda _r: None,
        )

        self.assertEqual(len(calls), 1)
        self.assertGreater(calls[0], 1)

    def test_every_judged_pair_is_recorded_for_later_tuning(self):
        signals = [self._sig("a", PARAPHRASE_A), self._sig("b", PARAPHRASE_B)]
        recorded: list[list[dict]] = []

        cd._apply_relatedness_judgment(
            signals,
            self._edges(signals),
            judge_pairs_fn=lambda pairs: [(True, 0.83, 0.77)] * len(pairs),
            log_judged_fn=recorded.append,
        )

        self.assertEqual(len(recorded), 1)
        record = recorded[0][0]
        self.assertEqual(record["members"], ["a", "b"])
        self.assertEqual(record["same_work"], 0.83)
        self.assertEqual(record["should_cluster"], 0.77)
        self.assertTrue(record["edge"])
        self.assertIn("overlap", record)


class ComputeClustersWiringTest(_SignalFixture):
    """The injection actually reaches the pass, end to end."""

    def _queue(self, focuses: dict[str, str]) -> Path:
        for index, (stem, focus) in enumerate(sorted(focuses.items())):
            path = self.dir / f"2026010{index}-00000{index}-{stem}.md"
            path.write_text(
                f"---\nfocus: |-\n  {focus}\nrepo: /r\n---\n", encoding="utf-8"
            )
        return self.dir

    def test_a_paraphrase_pair_surfaces_as_a_cluster_through_the_public_entry(self):
        queue = self._queue({"alpha": PARAPHRASE_A, "beta": PARAPHRASE_B})

        without = cd.compute_clusters(queue, _parse_fm)
        with_judgment = cd.compute_clusters(
            queue,
            _parse_fm,
            judge_pairs_fn=lambda pairs: [(True, 0.83, 0.77)] * len(pairs),
            log_judged_fn=lambda _r: None,
        )

        self.assertEqual(without, [], "premise: lexical overlap cannot see this pair")
        self.assertEqual(len(with_judgment), 1)
        self.assertEqual(len(with_judgment[0]["members"]), 2)

    def test_a_lexical_cluster_disappears_when_the_judgment_rejects_it(self):
        queue = self._queue({"alpha": PARAPHRASE_A, "beta": PARAPHRASE_A})

        without = cd.compute_clusters(queue, _parse_fm)
        with_judgment = cd.compute_clusters(
            queue,
            _parse_fm,
            judge_pairs_fn=lambda pairs: [(False, 0.04, 0.06)] * len(pairs),
            log_judged_fn=lambda _r: None,
        )

        self.assertEqual(len(without), 1, "premise: identical focus clusters lexically")
        self.assertEqual(with_judgment, [])


class LogJudgedPairsTest(unittest.TestCase):
    """The telemetry sink for judged pairs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = Path(self._tmp.name) / "cluster-log.jsonl"
        self.addCleanup(self._tmp.cleanup)

    def test_one_record_per_pair_with_both_values(self):
        cluster_telemetry.log_judged_pairs(
            [
                {
                    "members": ["a", "b"],
                    "overlap": 0.12,
                    "same_work": 0.83,
                    "should_cluster": 0.77,
                    "edge": True,
                }
            ],
            self.log,
        )

        records = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "judged")
        self.assertEqual(records[0]["same_work"], 0.83)
        self.assertEqual(records[0]["should_cluster"], 0.77)
        self.assertIn("at", records[0])

    def test_an_unwritable_log_is_swallowed(self):
        """Telemetry is observability, not a control path -- the edges are
        already decided by the time this runs."""
        cluster_telemetry.log_judged_pairs(
            [{"members": ["a", "b"], "edge": False}],
            Path(self._tmp.name) / "no-such-dir" / "x" / "log.jsonl",
        )


if __name__ == "__main__":
    unittest.main()
