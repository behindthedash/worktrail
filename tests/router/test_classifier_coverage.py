#!/usr/bin/env python3
"""Tests for classifier_coverage.py.

Builds a throwaway work-queue + run-record layout with real brief and YAML
fixtures to pin the audit contract:
  - the actual route (run record) outranks the brief's recommended route
  - ``handoffs_consumed`` is honoured in both its string and list forms, and
    the newest run wins when several consumed the same brief
  - known route disagreements land in the right ``(expected, predicted)``
    cluster, with real classifier text (no stubbing of ``classify()``)
  - no-signal defaults are counted apart from mis-weighted signals
  - ``actionable`` requires high-confidence disagreements
  - the corpus is bounded by ``--limit`` / ``--since``
  - unparseable and missing route values are skipped, not coerced
  - the audit writes nothing and is byte-identical across repeated runs
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, List, Optional

from worktrail.router import classifier_coverage as cc

# Real focus strings whose organic classification is pinned by the assertions
# below. Chosen so each fires a distinct signal family in classify.py.
FOCUS_DEFECT = "Fix the broken upload timeout bug causing failed receipts"
FOCUS_WORKFLOW = "Update the go route classifier and its routing cassette scenarios"
FOCUS_NO_SIGNAL = "Zzz qqq wibble frobnicate the thing"


def _write_brief(
    directory: Path,
    brief_id: str,
    *,
    focus: Optional[str] = FOCUS_DEFECT,
    recommended: Optional[str] = "F",
    created: str = "2026-07-01T10:00:00-07:00",
    focus_in_body: bool = False,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"id: {brief_id}", f"created: '{created}'"]
    if focus is not None and not focus_in_body:
        lines.append(f"focus: {focus}")
    if recommended is not None:
        lines.append(f"recommended-route: {recommended}")
    lines.append("---")
    lines.append("")
    if focus is not None and focus_in_body:
        lines.append("## Focus")
        lines.append("")
        lines.append(focus)
        lines.append("")
        lines.append("## Discovery context")
        lines.append("")
        lines.append("irrelevant trailing prose")
    path = directory / f"{brief_id}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_run(
    runs_root: Path,
    repo: str,
    run_id: str,
    *,
    route: str,
    consumed: Any,
) -> Path:
    directory = runs_root / repo
    directory.mkdir(parents=True, exist_ok=True)
    record: Dict[str, Any] = {
        "run_id": run_id,
        "selected_route": route,
        "handoffs_consumed": consumed,
    }
    path = directory / f"{run_id}.yaml"
    path.write_text(json.dumps(record), encoding="utf-8")  # JSON is valid YAML
    return path


class ClassifierCoverageTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.queue_root = self.root / "work-queue"
        self.runs_root = self.root / "runs"
        (self.queue_root / "queue").mkdir(parents=True)
        (self.queue_root / "picked").mkdir(parents=True)
        self.runs_root.mkdir()

    def audit(self, **kwargs: Any) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "queue_root": self.queue_root,
            "runs_root": self.runs_root,
            "limit": 0,
        }
        params.update(kwargs)
        return cc.audit_coverage(**params)

    def cluster(self, report: Dict[str, Any], expected: str, predicted: str) -> Dict[str, Any]:
        for cluster in report["clusters"]:
            if cluster["expected"] == expected and cluster["predicted"] == predicted:
                return cluster
        self.fail(
            f"no {expected} -> {predicted} cluster in "
            f"{[(c['expected'], c['predicted']) for c in report['clusters']]}"
        )


class TestExpectedRouteResolution(ClassifierCoverageTestCase):
    def test_actual_route_outranks_recommended(self) -> None:
        """A run record that consumed the brief is what really happened."""
        _write_brief(self.queue_root / "picked", "b-1", recommended="F")
        _write_run(self.runs_root, "repo", "go-1", route="J", consumed=["b-1"])

        report = self.audit()

        self.assertEqual(report["agreement"]["compared"], 1)
        self.assertEqual(report["by_expected_route"][0]["expected"], "J")
        self.cluster(report, "J", "F")

    def test_recommended_used_when_no_run_consumed_the_brief(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1", recommended="F")

        report = self.audit()

        self.assertEqual(report["by_expected_route"][0]["expected"], "F")
        self.assertEqual(report["agreement"]["agreed"], 1)

    def test_handoffs_consumed_accepts_a_bare_string(self) -> None:
        """Run records in the wild store this as both a string and a list."""
        _write_brief(self.queue_root / "picked", "b-1", recommended="F")
        _write_run(self.runs_root, "repo", "go-1", route="J", consumed="b-1")

        self.assertEqual(cc.load_actual_routes(self.runs_root), {"b-1": "J"})

    def test_newest_run_wins_when_several_consumed_the_same_brief(self) -> None:
        _write_brief(self.queue_root / "picked", "b-1", recommended="F")
        _write_run(self.runs_root, "repo", "go-20260101-000000", route="I", consumed=["b-1"])
        _write_run(self.runs_root, "repo", "go-20260901-000000", route="J", consumed=["b-1"])

        self.assertEqual(cc.load_actual_routes(self.runs_root), {"b-1": "J"})

    def test_corrupt_run_record_is_skipped_not_fatal(self) -> None:
        _write_brief(self.queue_root / "picked", "b-1", recommended="F")
        bad = self.runs_root / "repo"
        bad.mkdir(parents=True)
        (bad / "broken.yaml").write_text("{{{ not yaml", encoding="utf-8")

        self.assertEqual(cc.load_actual_routes(self.runs_root), {})
        self.assertEqual(self.audit()["agreement"]["compared"], 1)

    def test_unparseable_route_value_is_skipped_not_coerced(self) -> None:
        """Observed in the wild: `recommended-route: verify`."""
        _write_brief(self.queue_root / "queue", "b-1", recommended="verify")

        report = self.audit()

        self.assertEqual(report["agreement"]["compared"], 0)
        self.assertEqual(report["corpus"]["skipped"], {cc.SKIP_BAD_ROUTE: 1})

    def test_missing_route_and_missing_focus_are_skipped_separately(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1", recommended=None)
        _write_brief(self.queue_root / "queue", "b-2", focus=None, recommended="F")

        report = self.audit()

        self.assertEqual(report["agreement"]["compared"], 0)
        self.assertEqual(
            report["corpus"]["skipped"],
            {cc.SKIP_NO_EXPECTED: 1, cc.SKIP_NO_FOCUS: 1},
        )

    def test_lowercase_route_letter_is_normalized(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1", recommended="f")

        self.assertEqual(self.audit()["by_expected_route"][0]["expected"], "F")

    def test_focus_falls_back_to_the_body_section(self) -> None:
        _write_brief(
            self.queue_root / "queue", "b-1", focus=FOCUS_DEFECT, focus_in_body=True
        )

        report = self.audit()

        self.assertEqual(report["agreement"]["compared"], 1)
        self.assertEqual(report["agreement"]["agreed"], 1)


class TestDisagreementClusters(ClassifierCoverageTestCase):
    def test_known_disagreement_lands_in_the_right_cluster(self) -> None:
        """A defect-worded brief recorded as J is a real F/J disagreement."""
        _write_brief(self.queue_root / "queue", "b-1", focus=FOCUS_DEFECT, recommended="J")

        report = self.audit()
        cluster = self.cluster(report, "J", "F")

        self.assertEqual(cluster["count"], 1)
        self.assertEqual(cluster["sample_briefs"], ["b-1"])
        self.assertEqual(cluster["mis_weighted_count"], 1)
        self.assertEqual(cluster["no_signal_count"], 0)

    def test_agreeing_briefs_produce_no_cluster(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1", focus=FOCUS_WORKFLOW, recommended="J")

        report = self.audit()

        self.assertEqual(report["clusters"], [])
        self.assertEqual(report["agreement"]["rate"], 1.0)

    def test_clusters_sort_by_count_then_route_pair(self) -> None:
        for index in range(3):
            _write_brief(
                self.queue_root / "queue", f"many-{index}", focus=FOCUS_DEFECT, recommended="J"
            )
        _write_brief(self.queue_root / "queue", "one", focus=FOCUS_DEFECT, recommended="I")

        report = self.audit()

        self.assertEqual(
            [(c["expected"], c["predicted"], c["count"]) for c in report["clusters"]],
            [("J", "F", 3), ("I", "F", 1)],
        )

    def test_from_actual_route_count_tracks_the_stronger_evidence(self) -> None:
        _write_brief(self.queue_root / "picked", "b-1", focus=FOCUS_DEFECT, recommended="J")
        _write_brief(self.queue_root / "picked", "b-2", focus=FOCUS_DEFECT, recommended="J")
        _write_run(self.runs_root, "repo", "go-1", route="J", consumed=["b-1"])

        cluster = self.cluster(self.audit(), "J", "F")

        self.assertEqual(cluster["count"], 2)
        self.assertEqual(cluster["from_actual_route_count"], 1)


class TestNoSignalSplit(ClassifierCoverageTestCase):
    def test_no_signal_brief_is_counted_apart_from_mis_weighting(self) -> None:
        _write_brief(
            self.queue_root / "queue", "quiet", focus=FOCUS_NO_SIGNAL, recommended="F"
        )

        report = self.audit()

        self.assertEqual(report["no_signal"]["count"], 1)
        self.assertEqual(report["no_signal"]["disagreed"], 1)
        self.assertEqual(report["no_signal"]["share_of_corpus"], 1.0)
        self.assertEqual(report["no_signal"]["share_of_disagreements"], 1.0)
        self.assertEqual(report["no_signal"]["sample_briefs"], ["quiet"])

    def test_signalled_brief_is_not_counted_as_no_signal(self) -> None:
        _write_brief(self.queue_root / "queue", "loud", focus=FOCUS_DEFECT, recommended="J")

        report = self.audit()

        self.assertEqual(report["no_signal"]["count"], 0)
        self.assertEqual(self.cluster(report, "J", "F")["no_signal_count"], 0)

    def test_e_disqualification_is_not_miscounted_as_no_signal(self) -> None:
        """resumable_state=False sets scores["E"] = -1, which classify()'s
        own `s > 0` filter then drops. A brief whose only match was an E
        signal must still count as *signalled* — reading the empty map
        literally would inflate the coverage-gap number with deliberate
        disqualifications."""
        _write_brief(
            self.queue_root / "queue",
            "resumer",
            focus="Resume the existing worktree",
            recommended="F",
        )

        # Precondition: E is this text's only signal, and it scores.
        signalled = self.audit()
        self.assertEqual(signalled["no_signal"]["count"], 0)
        self.assertEqual(signalled["clusters"][0]["predicted"], "E")

        disqualified = self.audit(resumable_state=False)

        self.assertEqual(disqualified["clusters"][0]["predicted"], "A")
        self.assertEqual(disqualified["no_signal"]["count"], 0)
        self.assertEqual(disqualified["clusters"][0]["no_signal_count"], 0)
        self.assertEqual(disqualified["clusters"][0]["mis_weighted_count"], 1)

    def test_no_signal_default_route_moves_with_resumable_state(self) -> None:
        """The same coverage gap surfaces under a different route pair once
        Route E is disqualified — which is why it is counted separately."""
        _write_brief(
            self.queue_root / "queue", "quiet", focus=FOCUS_NO_SIGNAL, recommended="F"
        )

        unknown = self.audit()
        disqualified = self.audit(resumable_state=False)

        self.assertEqual(unknown["clusters"][0]["predicted"], "E")
        self.assertEqual(disqualified["clusters"][0]["predicted"], "A")
        # The pair moved; the no-signal count did not.
        self.assertEqual(unknown["no_signal"]["count"], 1)
        self.assertEqual(disqualified["no_signal"]["count"], 1)


class TestActionableFlag(ClassifierCoverageTestCase):
    def test_single_high_confidence_disagreement_is_below_threshold(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1", focus=FOCUS_WORKFLOW, recommended="C")

        cluster = self.cluster(self.audit(), "C", "J")

        self.assertEqual(cluster["high_confidence_count"], 1)
        self.assertFalse(cluster["actionable"])

    def test_threshold_many_high_confidence_disagreements_are_actionable(self) -> None:
        for index in range(cc.MIN_ACTIONABLE_CONFIDENT):
            _write_brief(
                self.queue_root / "queue", f"b-{index}", focus=FOCUS_WORKFLOW, recommended="C"
            )

        cluster = self.cluster(self.audit(), "C", "J")

        self.assertEqual(cluster["high_confidence_count"], cc.MIN_ACTIONABLE_CONFIDENT)
        self.assertTrue(cluster["actionable"])

    def test_no_signal_disagreements_never_raise_the_actionable_flag(self) -> None:
        for index in range(cc.MIN_ACTIONABLE_CONFIDENT + 3):
            _write_brief(
                self.queue_root / "queue",
                f"quiet-{index}",
                focus=FOCUS_NO_SIGNAL,
                recommended="F",
            )

        for cluster in self.audit()["clusters"]:
            self.assertEqual(cluster["high_confidence_count"], 0)
            self.assertFalse(cluster["actionable"])


class TestCorpusBounding(ClassifierCoverageTestCase):
    def _write_dated(self) -> None:
        for day, brief_id in ((1, "oldest"), (2, "middle"), (3, "newest")):
            _write_brief(
                self.queue_root / "queue",
                brief_id,
                created=f"2026-07-0{day}T10:00:00-07:00",
            )

    def test_limit_keeps_the_newest_briefs(self) -> None:
        self._write_dated()

        report = self.audit(limit=2)

        self.assertEqual(report["corpus"]["briefs_scanned"], 3)
        self.assertEqual(report["corpus"]["briefs_compared"], 2)
        self.assertEqual(report["agreement"]["compared"], 2)

    def test_since_filters_by_created_date(self) -> None:
        self._write_dated()

        report = self.audit(since="2026-07-02")

        self.assertEqual(report["corpus"]["briefs_scanned"], 2)
        self.assertEqual(report["corpus"]["since"], "2026-07-02")

    def test_limit_zero_scans_everything(self) -> None:
        self._write_dated()

        self.assertEqual(self.audit(limit=0)["agreement"]["compared"], 3)

    def test_both_queue_and_picked_are_scanned(self) -> None:
        _write_brief(self.queue_root / "queue", "waiting")
        _write_brief(self.queue_root / "picked", "claimed")

        self.assertEqual(self.audit()["agreement"]["compared"], 2)


class TestReplayInputs(ClassifierCoverageTestCase):
    def test_default_replay_state_is_pinned_and_reported(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1")

        replay = self.audit()["replay"]

        self.assertEqual(replay["state"], cc.REPLAY_STATE)
        self.assertIsNone(replay["resumable_state"])
        self.assertFalse(replay["handoff_hint_applied"])

    def test_state_override_is_carried_into_the_report(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1")

        state = {"active_specs": 9, "handoff_queue": 0}

        self.assertEqual(self.audit(state=state)["replay"]["state"], state)

    def test_resumable_state_false_disqualifies_route_e(self) -> None:
        _write_brief(
            self.queue_root / "queue", "b-1", focus=FOCUS_NO_SIGNAL, recommended="E"
        )

        self.assertEqual(self.audit()["agreement"]["agreed"], 1)
        self.assertEqual(self.audit(resumable_state=False)["agreement"]["agreed"], 0)


class TestReadOnlyAndDeterminism(ClassifierCoverageTestCase):
    def test_audit_writes_nothing(self) -> None:
        brief = _write_brief(self.queue_root / "queue", "b-1")
        run = _write_run(self.runs_root, "repo", "go-1", route="J", consumed=["b-1"])
        before = {
            path: path.read_bytes()
            for path in (brief, run)
        }
        listing_before = sorted(p.name for p in (self.queue_root / "queue").iterdir())

        self.audit()

        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertEqual(
            sorted(p.name for p in (self.queue_root / "queue").iterdir()), listing_before
        )

    def test_repeated_runs_are_byte_identical(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1", focus=FOCUS_DEFECT, recommended="J")
        _write_brief(self.queue_root / "queue", "b-2", focus=FOCUS_NO_SIGNAL, recommended="F")

        first = cc.render_report(self.audit())
        second = cc.render_report(self.audit())

        self.assertEqual(first, second)


class TestRendering(ClassifierCoverageTestCase):
    def test_report_names_the_withheld_hint(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1")

        text = cc.render_report(self.audit())

        self.assertIn("handoff_hint=withheld", text)
        self.assertIn("tautological", text)

    def test_resumable_false_report_carries_the_route_e_caveat(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1")

        text = cc.render_report(self.audit(resumable_state=False))

        self.assertIn("by construction", text)

    def test_empty_corpus_renders_without_dividing_by_zero(self) -> None:
        text = cc.render_report(self.audit())

        self.assertIn("no comparable briefs", text)


class TestCli(ClassifierCoverageTestCase):
    def run_cli(self, argv: List[str]) -> tuple:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_json_output_carries_the_documented_top_level_keys(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1")

        code, out, _ = self.run_cli(
            ["--queue-dir", str(self.queue_root), "--runs-dir", str(self.runs_root), "--json"]
        )

        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertEqual(
            sorted(payload),
            ["agreement", "by_expected_route", "clusters", "corpus", "no_signal", "replay"],
        )

    def test_text_output_is_the_default(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1")

        code, out, _ = self.run_cli(
            ["--queue-dir", str(self.queue_root), "--runs-dir", str(self.runs_root)]
        )

        self.assertEqual(code, 0)
        self.assertIn("Classifier Coverage Audit", out)

    def test_missing_queue_root_exits_two(self) -> None:
        code, _, err = self.run_cli(["--queue-dir", str(self.root / "nope")])

        self.assertEqual(code, 2)
        self.assertIn("work-queue root not found", err)

    def test_missing_runs_root_is_tolerated(self) -> None:
        """A machine with no run records still gets the recommended-route audit."""
        _write_brief(self.queue_root / "queue", "b-1")

        code, out, _ = self.run_cli(
            [
                "--queue-dir", str(self.queue_root),
                "--runs-dir", str(self.root / "no-runs"),
                "--json",
            ]
        )

        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["agreement"]["compared"], 1)

    def test_invalid_state_json_exits_two(self) -> None:
        code, _, err = self.run_cli(
            ["--queue-dir", str(self.queue_root), "--state", "{not json"]
        )

        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)

    def test_non_object_state_json_exits_two(self) -> None:
        code, _, err = self.run_cli(
            ["--queue-dir", str(self.queue_root), "--state", "[1, 2]"]
        )

        self.assertEqual(code, 2)
        self.assertIn("must be a JSON object", err)

    def test_resumable_state_flag_reaches_the_report(self) -> None:
        _write_brief(self.queue_root / "queue", "b-1")

        code, out, _ = self.run_cli(
            [
                "--queue-dir", str(self.queue_root),
                "--runs-dir", str(self.runs_root),
                "--resumable-state", "false",
                "--json",
            ]
        )

        self.assertEqual(code, 0)
        self.assertIs(json.loads(out)["replay"]["resumable_state"], False)


if __name__ == "__main__":
    unittest.main()
