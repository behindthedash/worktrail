#!/usr/bin/env python3
"""Tests for queue_triage.py -- repo grouping and dedup-marker detection.

Run: python3 -m pytest tests/workqueue/test_queue_triage.py -q
"""

from __future__ import annotations

import datetime
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest import mock

from worktrail.workqueue import queue_triage as qt


def _brief(focus: str, repo: str | None = None, body: str = "") -> str:
    fm = [f"focus: {focus}", "status: queued"]
    if repo is not None:
        fm.append(f"repo: {repo}")
    return "---\n" + "\n".join(fm) + "\n---\n\n" + body


class QueueTriageTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        os.environ["WORK_QUEUE_DIR"] = str(self.base)
        self.queue = self.base / "queue"
        self.queue.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def write(
        self,
        name: str,
        repo: str | None = None,
        body: str = "",
        focus: str | None = None,
    ) -> Path:
        p = self.queue / name
        p.write_text(_brief(focus or name, repo=repo, body=body), encoding="utf-8")
        return p


class TestGroupQueueByRepo(QueueTriageTestBase):
    def test_groups_by_repo_value(self):
        self.write("a.md", repo="behindthedash/worktrail")
        self.write("b.md", repo="behindthedash/worktrail")
        self.write("c.md", repo="behindthedash/devops")

        groups, _inferred, _unresolvable = qt.group_queue_by_repo()

        self.assertEqual(
            {k: sorted(p.name for p in v) for k, v in groups.items()},
            {
                "behindthedash/worktrail": ["a.md", "b.md"],
                "behindthedash/devops": ["c.md"],
            },
        )

    def test_missing_repo_field_collapses_to_none_key(self):
        self.write("a.md")  # no repo: field at all

        groups, _inferred, _unresolvable = qt.group_queue_by_repo()

        self.assertEqual(list(groups), [qt.NO_REPO_KEY])
        self.assertEqual([p.name for p in groups[qt.NO_REPO_KEY]], ["a.md"])

    def test_null_and_blank_repo_collapse_to_none_key(self):
        self.write("a.md", repo="null")
        self.write("b.md", repo='""')

        groups, _inferred, _unresolvable = qt.group_queue_by_repo()

        self.assertEqual(list(groups), [qt.NO_REPO_KEY])
        self.assertEqual(
            sorted(p.name for p in groups[qt.NO_REPO_KEY]), ["a.md", "b.md"]
        )

    def test_none_and_named_repo_briefs_collapse_into_same_none_bucket(self):
        self.write("a.md")
        self.write("b.md", repo="null")
        self.write("c.md", repo="behindthedash/worktrail")

        groups, _inferred, _unresolvable = qt.group_queue_by_repo()

        self.assertEqual(
            sorted(p.name for p in groups[qt.NO_REPO_KEY]), ["a.md", "b.md"]
        )
        self.assertEqual([p.name for p in groups["behindthedash/worktrail"]], ["c.md"])

    def test_empty_queue_dir_yields_no_groups(self):
        self.assertEqual(qt.group_queue_by_repo(), ({}, [], []))

    def test_missing_queue_dir_yields_no_groups(self):
        for f in self.queue.iterdir():
            f.unlink()
        self.queue.rmdir()
        self.assertEqual(qt.group_queue_by_repo(), ({}, [], []))

    def test_non_markdown_files_are_ignored(self):
        (self.queue / "notes.txt").write_text("not a brief", encoding="utf-8")
        self.write("a.md")

        groups, _inferred, _unresolvable = qt.group_queue_by_repo()

        self.assertEqual([p.name for p in groups[qt.NO_REPO_KEY]], ["a.md"])


class TestIsRecentlyTriaged(QueueTriageTestBase):
    def test_recent_triage_section_is_within_window(self):
        import datetime

        recent = datetime.date.today() - datetime.timedelta(days=1)  # noqa: DTZ011
        p = self.write(
            "a.md",
            body=f"## Triage {recent.isoformat()}\n\nkeep\n",
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_stale_triage_section_is_outside_window(self):
        p = self.write(
            "a.md",
            body="## Triage 2020-01-01\n\nkeep\n",
        )
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))

    def test_no_triage_section_is_not_recently_triaged(self):
        p = self.write("a.md", body="## Focus\n\nsome brief\n")
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))

    def test_most_recent_of_multiple_triage_sections_wins(self):
        import datetime

        recent = datetime.date.today() - datetime.timedelta(days=1)  # noqa: DTZ011
        p = self.write(
            "a.md",
            body=(
                "## Triage 2020-01-01\n\nstale\n\n"
                f"## Triage {recent.isoformat()}\n\nrecent\n"
            ),
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_unparsable_triage_date_is_ignored_not_raised(self):
        p = self.write("a.md", body="## Triage not-a-date\n\nkeep\n")
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))

    def test_unparsable_date_does_not_shadow_a_valid_one(self):
        import datetime

        recent = datetime.date.today() - datetime.timedelta(days=1)  # noqa: DTZ011
        p = self.write(
            "a.md",
            body=(
                "## Triage not-a-date\n\nkeep\n\n"
                f"## Triage {recent.isoformat()}\n\nkeep\n"
            ),
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_missing_file_returns_false(self):
        missing = self.queue / "does-not-exist.md"
        self.assertFalse(qt.is_recently_triaged(missing, within_days=30))

    def test_boundary_at_exactly_within_days_is_recent(self):
        # today() is 2026-08-05 per environment context; use a relative date
        # computed the same way the implementation does to avoid brittleness.
        import datetime

        today = datetime.date.today()  # noqa: DTZ011
        boundary_date = today - datetime.timedelta(days=30)
        p = self.write(
            "a.md",
            body=f"## Triage {boundary_date.isoformat()}\n\nkeep\n",
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_one_day_past_boundary_is_stale(self):
        import datetime

        today = datetime.date.today()  # noqa: DTZ011
        past_boundary = today - datetime.timedelta(days=31)
        p = self.write(
            "a.md",
            body=f"## Triage {past_boundary.isoformat()}\n\nkeep\n",
        )
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))


class TestParseVerdicts(unittest.TestCase):
    def test_well_formed_verdict_is_parsed_as_is(self):
        raw = (
            '{"brief_id": "a", "verdict": "stale-close", "duplicate_of": null, '
            '"evidence": "PR #42 already shipped this", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a"])

        self.assertEqual(len(verdicts), 1)
        v = verdicts[0]
        self.assertEqual(v.brief_id, "a")
        self.assertEqual(v.verdict, "stale-close")
        self.assertIsNone(v.duplicate_of)
        self.assertEqual(v.evidence, "PR #42 already shipped this")
        self.assertEqual(v.confidence, "high")

    def test_well_formed_duplicate_of_verdict_retains_target(self):
        raw = (
            '{"brief_id": "a", "verdict": "duplicate-of", "duplicate_of": "b", '
            '"evidence": "same premise as b.md", "confidence": "medium"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a"])

        self.assertEqual(verdicts[0].verdict, "duplicate-of")
        self.assertEqual(verdicts[0].duplicate_of, "b")

    def test_multiple_wellformed_verdicts_parsed_in_expected_order(self):
        raw = (
            "reasoning text here\n"
            '{"brief_id": "b", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "still relevant", "confidence": "low"}\n'
            "more reasoning\n"
            '{"brief_id": "a", "verdict": "needs-update", "duplicate_of": null, '
            '"evidence": "target file renamed", "confidence": "high"}\n'
        )
        verdicts = qt.parse_verdicts(raw, ["a", "b"])

        self.assertEqual([v.brief_id for v in verdicts], ["a", "b"])
        self.assertEqual(verdicts[0].verdict, "needs-update")
        self.assertEqual(verdicts[1].verdict, "keep")

    def test_unparsable_json_falls_back_to_keep_with_full_raw_text_retained(self):
        raw = "the evaluator rambled and never emitted any JSON at all"
        verdicts = qt.parse_verdicts(raw, ["a"])

        self.assertEqual(len(verdicts), 1)
        v = verdicts[0]
        self.assertEqual(v.brief_id, "a")
        self.assertEqual(v.verdict, "keep")
        self.assertIsNone(v.duplicate_of)
        self.assertEqual(v.evidence, raw)
        self.assertIsNone(v.confidence)

    def test_invalid_verdict_type_falls_back_to_keep_with_own_snippet_retained(self):
        """The fallback evidence is this brief's own matched snippet, not the
        surrounding prose or `raw_text` at large -- see
        `test_multi_brief_fallback_never_bleeds_another_briefs_evidence` for why."""
        snippet = (
            '{"brief_id": "a", "verdict": "not-a-real-verdict", "duplicate_of": null, '
            '"evidence": "some evidence", "confidence": "high"}'
        )
        raw = f"here is my answer: {snippet}"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_multi_brief_fallback_never_bleeds_another_briefs_evidence(self):
        """A batched evaluator call covers many briefs in one `raw_text`; brief
        `a`'s malformed JSON must never fall back to evidence that includes
        brief `b`'s own (valid, unrelated) verdict and evidence."""
        a_snippet = (
            '{"brief_id": "a", "verdict": "not-a-real-verdict", "duplicate_of": null, '
            '"evidence": "some evidence", "confidence": "high"}'
        )
        b_snippet = (
            '{"brief_id": "b", "verdict": "stale-close", "duplicate_of": null, '
            '"evidence": "brief b private rationale, must not leak into a", '
            '"confidence": "high"}'
        )
        raw = f"{a_snippet}\n{b_snippet}"
        verdicts = qt.parse_verdicts(raw, ["a", "b"])

        a_verdict = next(v for v in verdicts if v.brief_id == "a")
        self.assertEqual(a_verdict.verdict, "keep")
        self.assertEqual(a_verdict.evidence, a_snippet)
        self.assertNotIn("brief b private rationale", a_verdict.evidence)

        b_verdict = next(v for v in verdicts if v.brief_id == "b")
        self.assertEqual(b_verdict.verdict, "stale-close")

    def test_duplicate_of_without_target_falls_back_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "duplicate-of", "duplicate_of": null, '
            '"evidence": "looks like a dupe but no target cited", "confidence": "low"}'
        )
        verdicts = qt.parse_verdicts(snippet, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_missing_evidence_falls_back_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "stale-close", "duplicate_of": null, '
            '"evidence": "", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(snippet, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_evidence_field_entirely_absent_falls_back_to_keep(self):
        snippet = '{"brief_id": "a", "verdict": "stale-close", "duplicate_of": null}'
        verdicts = qt.parse_verdicts(snippet, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_missing_brief_id_still_appears_with_keep_fallback(self):
        raw = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "confirmed still needed", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a", "b"])

        self.assertEqual([v.brief_id for v in verdicts], ["a", "b"])
        self.assertEqual(verdicts[1].verdict, "keep")
        self.assertEqual(verdicts[1].evidence, raw)

    def test_no_expected_brief_ids_yields_no_verdicts(self):
        self.assertEqual(qt.parse_verdicts("{}", []), [])

    def test_well_formed_fold_into_change_verdict_is_parsed_as_is(self):
        raw = (
            '{"brief_id": "a", "verdict": "fold-into-change", "duplicate_of": null, '
            '"target_change": "widget-export-pipeline", '
            '"evidence": "overlaps open tasks in widget-export-pipeline", '
            '"confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(
            raw, ["a"], candidates_by_brief={"a": ["widget-export-pipeline"]}
        )

        v = verdicts[0]
        self.assertEqual(v.verdict, "fold-into-change")
        self.assertEqual(v.target_change, "widget-export-pipeline")
        self.assertEqual(v.evidence, "overlaps open tasks in widget-export-pipeline")

    def test_fold_into_change_target_outside_candidate_list_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "fold-into-change", "duplicate_of": null, '
            '"target_change": "not-a-presented-candidate", '
            '"evidence": "looks related", "confidence": "medium"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(
            raw, ["a"], candidates_by_brief={"a": ["widget-export-pipeline"]}
        )

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertIsNone(v.target_change)
        self.assertEqual(v.evidence, snippet)

    def test_fold_into_change_without_candidates_presented_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "fold-into-change", "duplicate_of": null, '
            '"target_change": "widget-export-pipeline", '
            '"evidence": "looks related", "confidence": "medium"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_fold_into_change_missing_target_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "fold-into-change", "duplicate_of": null, '
            '"evidence": "looks related", "confidence": "medium"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(
            raw, ["a"], candidates_by_brief={"a": ["widget-export-pipeline"]}
        )

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_well_formed_propose_change_verdict_is_parsed_as_is(self):
        raw = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "behindthedash/worktrail", '
            '"proposed_change_name": "add-widget-export", '
            '"evidence": "no existing change covers this", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "propose-change")
        self.assertEqual(v.target_repo, "behindthedash/worktrail")
        self.assertEqual(v.proposed_change_name, "add-widget-export")

    def test_propose_change_missing_target_repo_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"proposed_change_name": "add-widget-export", '
            '"evidence": "no existing change covers this", "confidence": "high"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_propose_change_non_kebab_case_name_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "behindthedash/worktrail", '
            '"proposed_change_name": "Add_Widget Export!", '
            '"evidence": "no existing change covers this", "confidence": "high"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_propose_change_missing_proposed_change_name_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "behindthedash/worktrail", '
            '"evidence": "no existing change covers this", "confidence": "high"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_propose_change_blank_target_repo_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "   ", '
            '"proposed_change_name": "add-widget-export", '
            '"evidence": "no existing change covers this", "confidence": "high"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_propose_change_trailing_newline_name_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "behindthedash/worktrail", '
            '"proposed_change_name": "valid-name\\n", '
            '"evidence": "no existing change covers this", "confidence": "high"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_fold_into_change_blank_target_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "fold-into-change", "duplicate_of": null, '
            '"target_change": "   ", '
            '"evidence": "looks related", "confidence": "medium"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(
            raw, ["a"], candidates_by_brief={"a": ["widget-export-pipeline"]}
        )

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_needs_decision_blank_question_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "needs-decision", "duplicate_of": null, '
            '"question": "   ", '
            '"evidence": "no clear owning repo", "confidence": "low"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_well_formed_work_directly_verdict_is_parsed_as_is(self):
        raw = (
            '{"brief_id": "a", "verdict": "work-directly", "duplicate_of": null, '
            '"evidence": "reproduces via pytest tests/foo -k bar", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "work-directly")
        self.assertEqual(v.evidence, "reproduces via pytest tests/foo -k bar")

    def test_well_formed_needs_decision_verdict_is_parsed_as_is(self):
        raw = (
            '{"brief_id": "a", "verdict": "needs-decision", "duplicate_of": null, '
            '"question": "should this fold into repo X or stay standalone?", '
            '"evidence": "no clear owning repo", "confidence": "low"}'
        )
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "needs-decision")
        self.assertEqual(v.question, "should this fold into repo X or stay standalone?")

    def test_needs_decision_missing_question_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "needs-decision", "duplicate_of": null, '
            '"evidence": "no clear owning repo", "confidence": "low"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_repo_less_propose_change_naming_known_repo_parses_as_is(self):
        raw = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "widgets", '
            '"proposed_change_name": "add-widget-export", '
            '"evidence": "clearly belongs in widgets", "confidence": "high"}'
        )
        verdicts = qt.parse_verdicts(
            raw, ["a"], known_repos_by_brief={"a": ["widgets", "gadgets"]}
        )

        v = verdicts[0]
        self.assertEqual(v.verdict, "propose-change")
        self.assertEqual(v.target_repo, "widgets")

    def test_repo_less_propose_change_naming_unlisted_repo_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "not-a-known-repo", '
            '"proposed_change_name": "add-widget-export", '
            '"evidence": "clearly belongs somewhere", "confidence": "high"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(
            raw, ["a"], known_repos_by_brief={"a": ["widgets", "gadgets"]}
        )

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_repo_less_fold_into_change_downgrades_to_keep(self):
        snippet = (
            '{"brief_id": "a", "verdict": "fold-into-change", "duplicate_of": null, '
            '"target_change": "widget-export-pipeline", '
            '"evidence": "looks related", "confidence": "medium"}'
        )
        raw = f"analysis before verdict\n{snippet}\nanalysis after verdict"
        verdicts = qt.parse_verdicts(
            raw,
            ["a"],
            candidates_by_brief={"a": []},
            known_repos_by_brief={"a": ["widgets"]},
        )

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

    def test_repo_bearing_group_propose_change_unaffected_by_known_repos(self):
        raw = (
            '{"brief_id": "a", "verdict": "propose-change", "duplicate_of": null, '
            '"target_repo": "behindthedash/worktrail", '
            '"proposed_change_name": "add-widget-export", '
            '"evidence": "no existing change covers this", "confidence": "high"}'
        )
        # No entry for "a" in known_repos_by_brief -- mirrors evaluate_group()
        # only populating the map for `NO_REPO_KEY` groups' briefs.
        verdicts = qt.parse_verdicts(raw, ["a"], known_repos_by_brief={})

        v = verdicts[0]
        self.assertEqual(v.verdict, "propose-change")
        self.assertEqual(v.target_repo, "behindthedash/worktrail")

    def test_second_candidate_used_when_first_is_malformed(self):
        good = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "second attempt is valid", "confidence": "medium"}'
        )
        bad = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, "evidence": ""}'
        )
        raw = f"{bad}\n{good}"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, "second attempt is valid")


class TestEvaluateGroupArchivedShortCircuit(QueueTriageTestBase):
    """2.4's archived-repo short-circuit: `_check_repo_archived()` gates whether
    `evaluate_group()` synthesizes verdicts itself or spawns an evaluator agent.
    Faked via `subprocess.run`/`spawn_agent` patches -- never hits the network.
    """

    def _completed(
        self, returncode: int, stdout: str = ""
    ) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(
            args=["gh"], returncode=returncode, stdout=stdout, stderr=""
        )

    def test_confirmed_archival_synthesizes_group_wide_stale_close_without_spawning(
        self,
    ):
        repo = "behindthedash/retired-repo"
        briefs = [self.write("a.md", repo=repo), self.write("b.md", repo=repo)]

        gh_stdout = json.dumps({"isArchived": True, "name": repo})
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run",
                return_value=self._completed(0, gh_stdout),
            ) as mock_run,
            mock.patch("worktrail.orchestrator.spawnlib.spawn_agent") as mock_spawn,
        ):
            result = qt.evaluate_group(repo, briefs, cwd=self.base)

        mock_run.assert_called_once()
        mock_spawn.assert_not_called()

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["repo"], repo)
        self.assertEqual(result[0]["brief_ids"], ["a", "b"])
        self.assertEqual(result[0]["candidates_by_brief"], {"a": [], "b": []})

        verdicts = qt.parse_verdicts(result[0]["raw_text"], ["a", "b"])
        self.assertEqual([v.brief_id for v in verdicts], ["a", "b"])
        for v in verdicts:
            self.assertEqual(v.verdict, "stale-close")
            self.assertIsNone(v.duplicate_of)
            self.assertIn(repo, v.evidence)
            self.assertEqual(v.confidence, "high")

    def test_gh_check_failure_falls_through_to_normal_evaluation(self):
        repo = "behindthedash/worktrail"
        briefs = [self.write("a.md", repo=repo)]

        from worktrail.orchestrator.spawnlib import SpawnResult

        evaluator_text = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "still relevant", "confidence": "medium"}'
        )
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run",
                return_value=self._completed(1, ""),
            ) as mock_run,
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                return_value=SpawnResult(text=evaluator_text, usage={}),
            ) as mock_spawn,
        ):
            result = qt.evaluate_group(repo, briefs, cwd=self.base)

        mock_run.assert_called_once()
        mock_spawn.assert_called_once()

        self.assertEqual(result[0]["raw_text"], evaluator_text)
        verdicts = qt.parse_verdicts(result[0]["raw_text"], ["a"])
        self.assertEqual(verdicts[0].verdict, "keep")

    def test_gh_check_exception_falls_through_to_normal_evaluation(self):
        repo = "behindthedash/worktrail"
        briefs = [self.write("a.md", repo=repo)]

        from worktrail.orchestrator.spawnlib import SpawnResult

        evaluator_text = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "gh unavailable, kept as fail-open", "confidence": "low"}'
        )
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run",
                side_effect=OSError("gh not found"),
            ) as mock_run,
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                return_value=SpawnResult(text=evaluator_text, usage={}),
            ) as mock_spawn,
        ):
            result = qt.evaluate_group(repo, briefs, cwd=self.base)

        mock_run.assert_called_once()
        mock_spawn.assert_called_once()
        self.assertEqual(result[0]["raw_text"], evaluator_text)


class TestEvaluatorPromptTemplate(unittest.TestCase):
    """2.3: the evaluator prompt states the fold/propose candidate-target rule,
    the `work-directly` reproduction-evidence rule, and the `needs-decision`
    rule for `repo: null` groups, and its output schema covers all eight
    verdict types plus their target fields.
    """

    def test_prompt_states_fold_target_must_be_a_presented_candidate(self):
        self.assertIn(
            "may only name `target_change` as one of *that brief's own* "
            "listed candidate ids",
            qt.EVALUATOR_PROMPT_TEMPLATE,
        )

    def test_prompt_states_work_directly_reproduction_evidence_rule(self):
        self.assertIn(
            "Use `work-directly` only when your evidence cites a specific "
            "test, check, or command",
            qt.EVALUATOR_PROMPT_TEMPLATE,
        )
        self.assertIn(
            "apply will downgrade a `work-directly` verdict lacking one to `keep`",
            qt.EVALUATOR_PROMPT_TEMPLATE,
        )

    def test_prompt_states_needs_decision_rule_for_no_repo_groups(self):
        self.assertIn(
            "If `{repo}` is `{no_repo_key}` (no target repo), "
            "`fold-into-change` is never valid",
            qt.EVALUATOR_PROMPT_TEMPLATE,
        )
        self.assertIn(
            "`propose-change` is valid only when your evidence names one of "
            "these known repos as the `target_repo`: {known_repos}",
            qt.EVALUATOR_PROMPT_TEMPLATE,
        )
        self.assertIn("use `needs-decision`", qt.EVALUATOR_PROMPT_TEMPLATE)

    def test_prompt_output_schema_covers_all_verdict_types_and_target_fields(self):
        for verdict_type in qt.VALID_VERDICT_TYPES:
            self.assertIn(verdict_type, qt.EVALUATOR_PROMPT_TEMPLATE)
        for field in (
            "target_change",
            "target_repo",
            "proposed_change_name",
            "question",
        ):
            self.assertIn(field, qt.EVALUATOR_PROMPT_TEMPLATE)


class TestProposeChangePromptTemplate(unittest.TestCase):
    """The propose-change authoring prompt must name both gates the
    generated change is checked against -- `openspec validate --strict`
    and `worktrail-compile` -- so the agent knows to write `tasks.md` file
    scope that clears the compile gate's own checks, not just validate's.
    """

    def _formatted(self) -> str:
        return qt.PROPOSE_CHANGE_PROMPT_TEMPLATE.format(
            repo="repo",
            proposed_change_name="some-change",
            brief_id="a",
            evidence="evidence text",
        )

    def test_prompt_names_openspec_validate(self):
        self.assertIn("openspec validate some-change --strict", self._formatted())

    def test_prompt_names_worktrail_compile(self):
        self.assertIn(
            "worktrail-compile openspec/changes/some-change", self._formatted()
        )


class TestFormatCandidates(unittest.TestCase):
    def test_empty_list_renders_none(self):
        self.assertEqual(qt._format_candidates([]), "(none)")

    def test_candidates_render_id_score_open_task_count_and_feature_summary(self):
        rendered = qt._format_candidates(
            [
                {
                    "id": "widget-export-pipeline",
                    "score": 0.625,
                    "open_task_count": 2,
                    "feature_summary": "widget export pipeline serializer",
                }
            ]
        )
        self.assertEqual(
            rendered,
            "widget-export-pipeline (score 0.62, 2 open tasks): "
            "widget export pipeline serializer",
        )


class TestEvaluateGroupCandidateContext(QueueTriageTestBase):
    """2.3: `evaluate_group()` ranks each brief's fold candidates via 2.1's
    `rank_change_candidates()`, embeds them in the prompt sent to the
    evaluator, and returns them alongside the raw text for `parse_verdicts()`.
    """

    def _make_change(self, repo_root: Path, change_id: str, *, why: str) -> None:
        change_dir = repo_root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            f"# {change_id}\n\n## Why\n{why}\n", encoding="utf-8"
        )
        (change_dir / "tasks.md").write_text(
            f"## 1. Tasks\n\n- [ ] 1.1 {why}\n", encoding="utf-8"
        )

    def test_ranked_candidates_appear_in_prompt_and_result(self):
        from worktrail.orchestrator.spawnlib import SpawnResult

        repo_root = self.base / "repo"
        self._make_change(
            repo_root,
            "widget-export-pipeline",
            why="widget export pipeline serializer downstream reporting",
        )
        brief_path = self.write(
            "a.md",
            repo=str(repo_root),
            body="## Focus\n\nwidget export pipeline serializer downstream reporting\n",
            focus="widget export pipeline serializer downstream reporting",
        )

        with mock.patch(
            "worktrail.orchestrator.spawnlib.spawn_agent",
            return_value=SpawnResult(text="", usage={}),
        ) as mock_spawn:
            result = qt.evaluate_group(str(repo_root), [brief_path], cwd=repo_root)

        self.assertEqual(
            result[0]["candidates_by_brief"], {"a": ["widget-export-pipeline"]}
        )
        prompt_sent = mock_spawn.call_args.args[0]
        self.assertIn("widget-export-pipeline", prompt_sent)
        self.assertIn(
            "widget export pipeline serializer downstream reporting", prompt_sent
        )

    def test_no_repo_group_ranks_no_candidates_for_any_brief(self):
        from worktrail.orchestrator.spawnlib import SpawnResult

        brief_path = self.write("a.md", body="## Focus\n\nanything at all\n")

        with mock.patch(
            "worktrail.orchestrator.spawnlib.spawn_agent",
            return_value=SpawnResult(text="", usage={}),
        ):
            result = qt.evaluate_group(qt.NO_REPO_KEY, [brief_path], cwd=self.base)

        self.assertEqual(result[0]["candidates_by_brief"], {"a": []})

    def test_no_repo_group_lists_known_repos_in_prompt_and_result(self):
        from worktrail.orchestrator.spawnlib import SpawnResult

        repos_root = self.base / "repos"
        (repos_root / "widgets").mkdir(parents=True, exist_ok=True)
        (repos_root / "gadgets").mkdir(parents=True, exist_ok=True)
        brief_path = self.write("a.md", body="## Focus\n\nanything at all\n")

        with mock.patch(
            "worktrail.orchestrator.spawnlib.spawn_agent",
            return_value=SpawnResult(text="", usage={}),
        ) as mock_spawn:
            result = qt.evaluate_group(
                qt.NO_REPO_KEY, [brief_path], cwd=self.base, repos_root=repos_root
            )

        self.assertEqual(
            result[0]["known_repos_by_brief"], {"a": ["gadgets", "widgets"]}
        )
        prompt_sent = mock_spawn.call_args.args[0]
        self.assertIn("gadgets", prompt_sent)
        self.assertIn("widgets", prompt_sent)

    def test_repo_bearing_group_has_no_known_repos_restriction(self):
        from worktrail.orchestrator.spawnlib import SpawnResult

        repo_root = self.base / "repo"
        brief_path = self.write(
            "a.md", repo=str(repo_root), body="## Focus\n\nanything at all\n"
        )

        with mock.patch(
            "worktrail.orchestrator.spawnlib.spawn_agent",
            return_value=SpawnResult(text="", usage={}),
        ):
            result = qt.evaluate_group(
                str(repo_root), [brief_path], cwd=repo_root, repos_root=self.base
            )

        self.assertEqual(result[0]["known_repos_by_brief"], {})


class TestApplyVerdicts(QueueTriageTestBase):
    """4.2's `apply_verdicts()`: `--confirm` false must be a pure dry run (no
    filesystem mutation at all), `--confirm` true must actually execute
    `stale-close` (claim+done with note) and `needs-update` (in-place append)
    against a temp `$WORK_QUEUE_DIR` fixture.
    """

    def _verdicts(self):
        return [
            qt.Verdict(
                brief_id="a",
                verdict="stale-close",
                duplicate_of=None,
                evidence="PR #42 already shipped this",
                confidence="high",
            ),
            qt.Verdict(
                brief_id="b",
                verdict="needs-update",
                duplicate_of=None,
                evidence="target file renamed, brief needs a refresh",
                confidence="medium",
            ),
        ]

    def test_confirm_false_is_a_pure_dry_run(self):
        a_path = self.write("a.md", body="## Focus\n\nsome brief\n")
        b_path = self.write("b.md", body="## Focus\n\nanother brief\n")
        a_before = a_path.read_text(encoding="utf-8")
        b_before = b_path.read_text(encoding="utf-8")

        log = qt.apply_verdicts(self._verdicts(), confirm=False)

        self.assertEqual(len(log), 2)
        for entry in log:
            self.assertEqual(entry["status"], "planned")
            self.assertFalse(entry["confirm"])
            self.assertIsNone(entry["path"])
            self.assertIsNone(entry["error"])
        self.assertEqual(log[0]["action"], "claim+done")
        self.assertEqual(log[0]["note"], "PR #42 already shipped this")
        self.assertEqual(log[1]["action"], "append-triage-note")
        self.assertEqual(log[1]["note"], "target file renamed, brief needs a refresh")

        # no file was moved, created, or rewritten anywhere under WORK_QUEUE_DIR
        self.assertEqual(a_path.read_text(encoding="utf-8"), a_before)
        self.assertEqual(b_path.read_text(encoding="utf-8"), b_before)
        self.assertTrue(a_path.exists())
        self.assertTrue(b_path.exists())
        picked = self.base / "picked"
        self.assertFalse(picked.exists() and any(picked.iterdir()))

    def _write_route_c(self, name: str, body: str) -> Path:
        path = self.write(name, body=body)
        path.write_text(
            path.read_text(encoding="utf-8").replace(
                "status: queued", "status: queued\nrecommended-route: C", 1
            ),
            encoding="utf-8",
        )
        return path

    def test_stale_close_on_route_c_brief_executes(self):
        """The Route-C planning/implementation decision gate must not block a
        triage closure (live 2026-09-02: 9 verdicts rolled back on it)."""
        self._write_route_c("a.md", "## Focus\n\nsome brief\n")
        verdict = qt.Verdict(
            brief_id="a",
            verdict="stale-close",
            duplicate_of=None,
            evidence="PR #42 already shipped this",
            confidence="high",
        )

        log = qt.apply_verdicts([verdict], confirm=True)

        self.assertEqual(log[0]["status"], "executed", log[0])
        self.assertEqual(qt.read_frontmatter(Path(log[0]["path"]))["status"], "done")

    def test_duplicate_of_on_consolidated_route_c_brief_executes(self):
        self._write_route_c(
            "a.md",
            "## Consolidated from\n\n- member-x\n- member-y\n",
        )
        self.write("b.md", body="## Focus\n\nthe surviving brief\n")
        verdict = qt.Verdict(
            brief_id="a",
            verdict="duplicate-of",
            duplicate_of="b",
            evidence="same findings as b",
            confidence="high",
        )

        log = qt.apply_verdicts([verdict], confirm=True)

        self.assertEqual(log[0]["status"], "executed", log[0])
        fm = qt.read_frontmatter(Path(log[0]["path"]))
        self.assertEqual(fm["status"], "done")
        self.assertEqual(fm["duplicate-of"], "b")

    def test_confirm_true_executes_stale_close_via_claim_and_done(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        self.write("b.md", body="## Focus\n\nanother brief\n")

        log = qt.apply_verdicts(self._verdicts(), confirm=True)

        close_entry = log[0]
        self.assertEqual(close_entry["brief_id"], "a")
        self.assertEqual(close_entry["status"], "executed")
        self.assertEqual(close_entry["action"], "claim+done")
        self.assertTrue(close_entry["confirm"])
        self.assertIsNone(close_entry["error"])

        picked_path = Path(close_entry["path"])
        self.assertEqual(picked_path.parent, self.base / "picked")
        self.assertFalse((self.queue / "a.md").exists())

        content = picked_path.read_text(encoding="utf-8")
        fm = qt.read_frontmatter(picked_path)
        self.assertEqual(fm["status"], "done")
        self.assertIn("## Closure Note", content)
        self.assertIn("PR #42 already shipped this", content)

    def test_confirm_true_executes_needs_update_via_inplace_append(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        b_path = self.write("b.md", body="## Focus\n\nanother brief\n")

        log = qt.apply_verdicts(self._verdicts(), confirm=True)

        update_entry = log[1]
        self.assertEqual(update_entry["brief_id"], "b")
        self.assertEqual(update_entry["status"], "executed")
        self.assertEqual(update_entry["action"], "append-triage-note")
        self.assertTrue(update_entry["confirm"])
        self.assertIsNone(update_entry["error"])
        self.assertEqual(update_entry["path"], str(b_path))

        # brief is left in place -- unlike stale-close, needs-update never claims it
        self.assertTrue(b_path.exists())
        content = b_path.read_text(encoding="utf-8")
        run_date = datetime.date.today().isoformat()  # noqa: DTZ011
        self.assertIn(f"## Triage {run_date}", content)
        self.assertIn("target file renamed, brief needs a refresh", content)
        self.assertTrue(qt.is_recently_triaged(b_path, within_days=1))

    def test_keep_verdict_appends_a_triage_note_instead_of_being_a_noop(self):
        """`keep` is never a stable verdict (design D2): it appends an
        in-place `## Triage <date>` note recording the keep streak --
        previewed without `--confirm`, executed with it -- rather than being
        a pure no-op."""
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="keep",
                duplicate_of=None,
                evidence="still relevant",
                confidence="low",
            )
        ]

        preview = qt.apply_verdicts(verdicts, confirm=False)
        self.assertEqual(preview[0]["status"], "planned")
        self.assertEqual(preview[0]["action"], "append-triage-note")
        self.assertNotIn("## Triage", path.read_text(encoding="utf-8"))

        executed = qt.apply_verdicts(verdicts, confirm=True)
        self.assertEqual(executed[0]["status"], "executed")
        self.assertEqual(executed[0]["action"], "append-triage-note")
        body = path.read_text(encoding="utf-8")
        self.assertIn("verdict: keep", body)
        self.assertIn("keep-count: 1", body)
        self.assertTrue((self.queue / "a.md").exists())

    def test_confirm_false_previews_fold_propose_branch_and_pr_title(self):
        """3.5: without `--confirm`, fold-into-change/propose-change verdicts
        preview their planned branch, target change, and PR title -- fully
        derived from the verdict's own fields, since 3.1/3.2's apply actions
        don't exist yet to actually compute them -- and never touch the
        queue or any repo."""
        a_path = self.write("a.md", body="## Focus\n\nsome brief\n")
        a_before = a_path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="fold-into-change",
                duplicate_of=None,
                evidence="overlaps with 042-example",
                confidence="high",
                target_change="042-example",
            ),
            qt.Verdict(
                brief_id="a",
                verdict="propose-change",
                duplicate_of=None,
                evidence="no good fold target, deserves its own change",
                confidence="medium",
                target_repo="behindthedash/worktrail",
                proposed_change_name="new-feature-x",
            ),
        ]

        log = qt.apply_verdicts(verdicts, confirm=False)

        self.assertEqual(len(log), 2)
        fold_entry, propose_entry = log
        self.assertEqual(fold_entry["status"], "planned")
        self.assertEqual(fold_entry["action"], "open-pull-request")
        self.assertFalse(fold_entry["confirm"])
        self.assertIsNone(fold_entry["path"])
        self.assertIsNone(fold_entry["error"])
        self.assertEqual(fold_entry["planned_target_change"], "042-example")
        self.assertEqual(
            fold_entry["planned_branch"], "queue-triage/fold-a-into-042-example"
        )
        self.assertIn("042-example", fold_entry["planned_pr_title"])

        self.assertEqual(propose_entry["status"], "planned")
        self.assertEqual(propose_entry["action"], "open-pull-request")
        self.assertFalse(propose_entry["confirm"])
        self.assertIsNone(propose_entry["path"])
        self.assertIsNone(propose_entry["error"])
        self.assertEqual(propose_entry["planned_target_change"], "new-feature-x")
        self.assertIn("new-feature-x", propose_entry["planned_branch"])
        self.assertIn("new-feature-x", propose_entry["planned_pr_title"])

        # never mutated -- pure preview, no queue or repo writes
        self.assertEqual(a_path.read_text(encoding="utf-8"), a_before)
        self.assertTrue((self.queue / "a.md").exists())


class TestCmdApplyPreviewPrintsPlannedFields(QueueTriageTestBase):
    """3.5: the human-readable (non-`--json`) `apply` output must print the
    planned branch/target change/PR title (fold/propose) and planned
    stamp/envelope (work-directly/needs-decision) for a no-`--confirm` dry
    run, not just return them from `apply_verdicts()` for a `--json` caller.
    """

    def test_confirm_false_human_output_prints_fold_branch_and_pr_title(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="fold-into-change",
                duplicate_of=None,
                evidence="overlaps with 042-example",
                confidence="high",
                target_change="042-example",
            ),
        ]
        verdict_path = qt.write_verdict_file(verdicts, self.base / "out")

        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = qt.main(["apply", "--verdict-file", str(verdict_path)])

        self.assertEqual(exit_code, 0)
        printed = buf.getvalue()
        self.assertIn("queue-triage/fold-a-into-042-example", printed)
        self.assertIn("042-example", printed)
        self.assertIn("Fold a into 042-example", printed)
        # dry run still never touches the queue
        self.assertTrue((self.queue / "a.md").exists())

    def test_confirm_false_human_output_prints_needs_decision_stamp_and_envelope(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous priority",
                confidence="medium",
                question="Should this be folded or is it a new change?",
            ),
        ]
        verdict_path = qt.write_verdict_file(verdicts, self.base / "out")

        buf = io.StringIO()
        with redirect_stdout(buf):
            exit_code = qt.main(["apply", "--verdict-file", str(verdict_path)])

        self.assertEqual(exit_code, 0)
        printed = buf.getvalue()
        self.assertIn("awaiting-decision", printed)
        self.assertIn("Should this be folded or is it a new change?", printed)
        # dry run still never touches the queue
        self.assertTrue((self.queue / "a.md").exists())


class TestApplyWorkDirectly(QueueTriageTestBase):
    """3.3's `work-directly` apply action: stamp `seeded-from`/`recommended-route`
    in place when the evidence cites a reproduction reference, downgrade to a
    no-op `keep` when it doesn't.
    """

    def test_confirm_true_stamps_frontmatter_when_evidence_has_repro_reference(self):
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="reproduces via pytest tests/foo.py -k bar",
                confidence="high",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        entry = log[0]
        self.assertEqual(entry["brief_id"], "a")
        self.assertEqual(entry["action"], "stamp-frontmatter")
        self.assertEqual(entry["status"], "executed")
        self.assertTrue(entry["confirm"])
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["path"], str(path))

        # brief is left in place, in queue/ -- unlike stale-close, work-directly
        # never claims or closes it
        self.assertTrue(path.exists())
        self.assertTrue((self.queue / "a.md").exists())

        fm = qt.read_frontmatter(path)
        run_date = datetime.date.today().isoformat()  # noqa: DTZ011
        self.assertEqual(fm["seeded-from"], f"triage:{run_date}:direct")
        self.assertEqual(fm["recommended-route"], "F")
        # focus and other original frontmatter fields survive the stamp
        self.assertEqual(fm["focus"], "a.md")
        self.assertEqual(fm["status"], "queued")

    def test_confirm_true_downgrades_to_keep_when_evidence_lacks_repro_reference(self):
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        before = path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="this brief describes a real, actionable problem",
                confidence="high",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        entry = log[0]
        self.assertEqual(entry["brief_id"], "a")
        self.assertEqual(entry["verdict"], "work-directly")
        self.assertEqual(entry["action"], "noop")
        self.assertEqual(entry["status"], "downgraded-to-keep")
        self.assertIsNone(entry["path"])
        self.assertIsNone(entry["error"])

        # never mutated -- downgraded to a no-op, not stamped
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        fm = qt.read_frontmatter(path)
        self.assertNotIn("seeded-from", fm)
        self.assertNotIn("recommended-route", fm)

    def test_confirm_false_is_a_pure_dry_run_when_evidence_has_repro_reference(self):
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        before = path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="reproduces via pytest tests/foo.py -k bar",
                confidence="high",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=False)

        entry = log[0]
        self.assertEqual(entry["action"], "stamp-frontmatter")
        self.assertEqual(entry["status"], "planned")
        self.assertFalse(entry["confirm"])
        self.assertIsNone(entry["path"])
        self.assertIsNone(entry["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), before)

        # 3.5: previews the exact frontmatter stamp _apply_work_directly would make
        run_date = datetime.date.today().isoformat()  # noqa: DTZ011
        self.assertEqual(
            entry["planned_stamp"],
            {"seeded-from": f"triage:{run_date}:direct", "recommended-route": "F"},
        )

    def test_confirm_false_previews_downgrade_to_keep_when_evidence_lacks_repro_reference(
        self,
    ):
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        before = path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="this brief describes a real, actionable problem",
                confidence="high",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=False)

        # the preview must agree with what confirm=True will actually do --
        # it must not advertise a stamp that the real run will downgrade
        entry = log[0]
        self.assertEqual(entry["action"], "noop")
        self.assertEqual(entry["status"], "planned-downgrade-to-keep")
        self.assertFalse(entry["confirm"])
        self.assertIsNone(entry["path"])
        self.assertIsNone(entry["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_evidence_naming_check_or_command_without_citing_one_is_rejected(self):
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        before = path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence=(
                    "I could not find a test or check that reproduces this, "
                    "but the brief looks actionable"
                ),
                confidence="high",
            ),
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="no command needed, it is obvious",
                confidence="high",
            ),
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        for entry in log:
            self.assertEqual(entry["action"], "noop")
            self.assertEqual(entry["status"], "downgraded-to-keep")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_evidence_with_backtick_quoted_non_command_span_is_rejected(self):
        """A backtick-quoted file path or brief id is not, on its own,
        evidence of a reproduction reference -- the span must actually name a
        test/check/command tool or verb (`pytest`, `make lint`, etc.), not just
        appear inside backticks."""
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        before = path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="confirmed by reading `src/worktrail/orchestrator/live.py`",
                confidence="high",
            ),
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="see brief `20260901-000000-some-other-brief` for context",
                confidence="high",
            ),
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        for entry in log:
            self.assertEqual(entry["action"], "noop")
            self.assertEqual(entry["status"], "downgraded-to-keep")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_evidence_citing_gh_git_or_grep_command_is_accepted(self):
        """Live 2026-09-03 (brief 20260903-111047): the evaluator's own
        high-confidence `work-directly` evidence cited `gh repo view` and
        `grep -rn` and was still downgraded, because the regex only knew
        test-runner and lint tools. A named `gh`/`git` read subcommand or a
        flagged `grep`/`rg` invocation is a command citation too."""
        for evidence in (
            (
                "Directly confirmed premise by reading all four cited logs; "
                "`gh repo view` shows the repo is archived"
            ),
            (
                "Confirmed via grep: `grep -rn triage-repos-root src/` returns "
                "zero matches"
            ),
            (
                "rg -n 'foo' src/ shows the symbol is unused; git log -1 -- "
                "src/foo.py shows no change since"
            ),
        ):
            with self.subTest(evidence=evidence):
                path = self.write("a.md", body="## Focus\n\nsome brief\n")
                verdicts = [
                    qt.Verdict(
                        brief_id="a",
                        verdict="work-directly",
                        duplicate_of=None,
                        evidence=evidence,
                        confidence="high",
                    )
                ]
                log = qt.apply_verdicts(verdicts, confirm=True)
                self.assertEqual(log[0]["action"], "stamp-frontmatter")
                self.assertEqual(log[0]["status"], "executed")
                fm, _ = qt.split_frontmatter(path.read_text(encoding="utf-8"))
                self.assertEqual(fm["recommended-route"], "F")

    def test_evidence_with_bare_gh_git_or_grep_prose_is_rejected(self):
        """`git history`, `gh workflow`, or "grep for it" are prose, not a
        command citation -- only a known subcommand or a flagged invocation
        qualifies."""
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        before = path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence=evidence,
                confidence="high",
            )
            for evidence in (
                "the git history suggests this is still open",
                "a gh workflow probably covers this; grep for it later",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        for entry in log:
            self.assertEqual(entry["action"], "noop")
            self.assertEqual(entry["status"], "downgraded-to-keep")
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_malformed_frontmatter_block_is_not_clobbered(self):
        # A tab-indented value inside the fence makes the block a
        # yaml.YAMLError -- split_frontmatter degrades that leniently to {}
        # for display, but the stamp must never re-serialize that {} back to
        # disk, since that would silently destroy every other field. The
        # in-place stamp edits the fenced lines surgically instead, so the
        # unparsable line (and every other field) survives untouched.
        path = self.queue / "a.md"
        path.write_text(
            "---\n"
            "id: a\n"
            "focus: fix thing\n"
            "status: queued\n"
            "\tbad: [unclosed\n"
            "---\n"
            "## Focus\n\nbody\n",
            encoding="utf-8",
        )
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="reproduces via pytest tests/foo.py -k bar",
                confidence="high",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        entry = log[0]
        self.assertEqual(entry["action"], "stamp-frontmatter")
        self.assertEqual(entry["status"], "executed")
        self.assertIsNone(entry["error"])

        after = path.read_text(encoding="utf-8")
        # every pre-existing field, including the unparsable line, survives
        self.assertIn("id: a\n", after)
        self.assertIn("focus: fix thing\n", after)
        self.assertIn("status: queued\n", after)
        self.assertIn("\tbad: [unclosed\n", after)
        self.assertIn("## Focus\n\nbody\n", after)
        # the new fields are stamped in
        run_date = datetime.date.today().isoformat()  # noqa: DTZ011
        self.assertIn(f"seeded-from: triage:{run_date}:direct\n", after)
        self.assertIn("recommended-route: F\n", after)


class TestApplyNeedsDecision(QueueTriageTestBase):
    """3.4's `needs-decision` apply action: file a pending decision via
    `decisions.ask()` (which builds the envelope with
    `decisions.pending_decision_envelope()`), leaving the brief queued.
    """

    def test_confirm_true_files_decision_and_leaves_brief_queued(self):
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous which repo this belongs to",
                confidence="medium",
                question="Which repo should this brief target?",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        entry = log[0]
        self.assertEqual(entry["brief_id"], "a")
        self.assertEqual(entry["verdict"], "needs-decision")
        self.assertEqual(entry["action"], "file-decision")
        self.assertEqual(entry["status"], "executed")
        self.assertTrue(entry["confirm"])
        self.assertIsNone(entry["error"])
        self.assertIsNotNone(entry["path"])

        # the brief is never claimed or closed -- it stays in queue/, unlike
        # stale-close/duplicate-of
        self.assertTrue(path.exists())
        self.assertTrue((self.queue / "a.md").exists())
        fm = qt.read_frontmatter(path)
        self.assertEqual(fm["awaiting-decision"], entry["decision_id"])

        from worktrail.workqueue import decisions

        found = decisions.find_decision(entry["decision_id"], self.base)
        self.assertIsNotNone(found)
        self.assertEqual(found["status"], "open")
        content = found["path"].read_text(encoding="utf-8")
        self.assertIn("Which repo should this brief target?", content)
        self.assertIn("ambiguous which repo this belongs to", content)

    def test_confirm_false_is_a_pure_dry_run(self):
        path = self.write("a.md", body="## Focus\n\nsome brief\n")
        before = path.read_text(encoding="utf-8")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous which repo this belongs to",
                confidence="medium",
                question="Which repo should this brief target?",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=False)

        entry = log[0]
        self.assertEqual(entry["action"], "file-decision")
        self.assertEqual(entry["status"], "planned")
        self.assertFalse(entry["confirm"])
        self.assertIsNone(entry["path"])
        self.assertIsNone(entry["error"])
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertFalse((self.base / "decisions").exists())

        # 3.5: previews the exact awaiting-decision stamp and the full pending
        # decision envelope ask() would file, without writing either
        self.assertIn("planned_stamp", entry)
        self.assertIn("awaiting-decision", entry["planned_stamp"])
        self.assertIn("planned_envelope", entry)
        self.assertEqual(
            entry["planned_envelope"]["question"],
            "Which repo should this brief target?",
        )
        self.assertEqual(
            entry["planned_envelope"]["decision_id"],
            entry["planned_stamp"]["awaiting-decision"],
        )

    def test_rerun_on_same_verdict_converges_on_existing_decision(self):
        """A re-run of `evaluate` that re-files the same still-open question
        for the same brief must not create a second decision record --
        `decision_identity()` is deterministic on (source, repo, subject,
        question), so the second `apply` converges on the first record.
        """
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous which repo this belongs to",
                confidence="medium",
                question="Which repo should this brief target?",
            )
        ]

        first = qt.apply_verdicts(verdicts, confirm=True)[0]
        second = qt.apply_verdicts(verdicts, confirm=True)[0]

        self.assertEqual(first["decision_id"], second["decision_id"])
        from worktrail.workqueue import decisions

        open_dir = decisions.decisions_dir(self.base) / "open"
        self.assertEqual(len(list(open_dir.glob("*.md"))), 1)

    def test_missing_question_is_an_error_not_a_crash(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous which repo this belongs to",
                confidence="medium",
                question=None,
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIsNotNone(entry["error"])
        self.assertTrue((self.queue / "a.md").exists())

    def test_unstampable_brief_is_an_error_not_executed(self):
        """If the brief cannot be found under queue/ or picked/ at apply
        time (deleted, claimed-and-closed, or unwritable between `evaluate`
        and `apply --confirm`), `ask()` still creates the decision record
        but reports `brief_stamped=False` -- that must surface as
        `status="error"`, not `"executed"`, or the skip clause silently does
        not hold for that brief while the log claims it does.
        """
        # note: no `self.write("ghost.md", ...)` -- the brief does not exist
        verdicts = [
            qt.Verdict(
                brief_id="ghost",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous which repo this belongs to",
                confidence="medium",
                question="Which repo should this brief target?",
            )
        ]

        log = qt.apply_verdicts(verdicts, confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIsNotNone(entry["error"])
        self.assertIsNotNone(entry["decision_id"])

        from worktrail.workqueue import decisions

        found = decisions.find_decision(entry["decision_id"], self.base)
        self.assertIsNotNone(found)
        self.assertEqual(found["status"], "open")

    def test_refile_against_already_resolved_decision_is_not_executed(self):
        """A re-run of `evaluate` after a human already answered and the
        decision was consumed (`resolved/`) must not report `"executed"`:
        `ask()` creates nothing and finds the resolved record, so the log
        must say so distinctly instead of implying a fresh decision was
        filed.
        """
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous which repo this belongs to",
                confidence="medium",
                question="Which repo should this brief target?",
            )
        ]

        first = qt.apply_verdicts(verdicts, confirm=True)[0]

        from worktrail.workqueue import decisions

        decisions.answer(
            first["decision_id"], "target behindthedash/worktrail", self.base
        )
        decisions.consume_answer(first["decision_id"], "test", self.base)

        second = qt.apply_verdicts(verdicts, confirm=True)[0]

        self.assertEqual(second["status"], "already-resolved")
        self.assertNotEqual(second["status"], "executed")
        self.assertIsNone(second["error"])
        self.assertEqual(second["decision_id"], first["decision_id"])


class TestApplyFoldIntoChange(QueueTriageTestBase):
    """3.1's `fold-into-change` apply action: fresh worktree off the target
    repo's base, `proposal.md`/`tasks.md` edits, `openspec validate`, commit,
    push, `gh pr create`, then close the brief with `triaged-to:`. Any
    failure before the PR exists must leave the brief completely untouched
    and report the branch name it would have used. `git`/`gh`/`openspec` are
    faked via a `subprocess.run` dispatcher -- never hits the network.
    """

    def setUp(self):
        super().setUp()
        self.repo = self.base / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.target_change = "widget-export-pipeline"
        self.brief_path = self.write(
            "a.md", repo=str(self.repo), body="## Focus\n\nfold this in\n"
        )
        self.verdict = qt.Verdict(
            brief_id="a",
            verdict="fold-into-change",
            duplicate_of=None,
            evidence="overlaps open tasks in widget-export-pipeline",
            confidence="high",
            target_change=self.target_change,
            repo=str(self.repo),
        )
        self.branch = qt._planned_fold_propose_branch(self.verdict)
        self.worktree_dir = qt._fold_propose_worktree_dir(self.repo, self.branch)

    def _seed_change(self) -> None:
        change_dir = self.worktree_dir / "openspec" / "changes" / self.target_change
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            "# Widget export pipeline\n\n## Why\n\nExisting rationale.\n",
            encoding="utf-8",
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 Existing task\n",
            encoding="utf-8",
        )

    @staticmethod
    def _completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
        return subprocess.CompletedProcess(
            args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _dispatcher(
        self,
        *,
        pr_returncode: int = 0,
        validate_returncode: int = 0,
        compile_returncode: int = 0,
        fetch_returncode: int = 0,
        push_default: str | None = None,
    ):
        pr_url = "https://github.com/acme/widgets/pull/42"
        self.seen: list[list[str]] = []

        def _run(cmd, **kwargs):
            self.seen.append(list(cmd))
            if cmd[0] == "git" and "-C" in cmd:
                if "symbolic-ref" in cmd:
                    return self._completed(0, stdout="origin/main\n")
                if "config" in cmd and "remote.pushDefault" in cmd:
                    if push_default is None:
                        return self._completed(1)
                    return self._completed(0, stdout=f"{push_default}\n")
                if "remote" in cmd and "get-url" in cmd:
                    return self._completed(
                        0, stdout="git@github.com:acme-fork/widgets.git\n"
                    )
                if "fetch" in cmd:
                    return self._completed(
                        fetch_returncode,
                        stderr=""
                        if fetch_returncode == 0
                        else "fatal: could not read from remote repository",
                    )
                if "worktree" in cmd and "add" in cmd:
                    self._seed_change()
                    return self._completed(0)
                if "worktree" in cmd and "remove" in cmd:
                    return self._completed(0)
                if "branch" in cmd and "-D" in cmd:
                    return self._completed(0)
            if cmd[0] == "openspec" and cmd[1] == "validate":
                return self._completed(
                    validate_returncode,
                    stdout="valid\n" if validate_returncode == 0 else "",
                    stderr="" if validate_returncode == 0 else "task 1.2 has no owner",
                )
            if cmd[0] == "worktrail-compile":
                if compile_returncode == 0:
                    (Path(cmd[1]) / ".compile-ok").write_text("fp\n", encoding="utf-8")
                return self._completed(
                    compile_returncode,
                    stderr=""
                    if compile_returncode == 0
                    else "task 2.1 has no file scope",
                )
            if cmd[0] == "git" and cmd[1] in ("add", "commit"):
                if cmd[1] == "commit":
                    self.marker_at_commit = (
                        self.worktree_dir
                        / "openspec"
                        / "changes"
                        / self.target_change
                        / ".compile-ok"
                    ).is_file()
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] == "push":
                return self._completed(0)
            if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
                return self._completed(
                    pr_returncode,
                    stdout=f"{pr_url}\n" if pr_returncode == 0 else "",
                    stderr=""
                    if pr_returncode == 0
                    else "could not create pull request",
                )
            raise AssertionError(f"unexpected command: {cmd}")

        return _run, pr_url

    def test_success_edits_change_opens_pr_and_closes_brief(self):
        run, pr_url = self._dispatcher()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["brief_id"], "a")
        self.assertEqual(entry["action"], "open-pull-request")
        self.assertEqual(entry["status"], "executed")
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertEqual(entry["pr_url"], pr_url)

        # brief closed with triaged-to: the PR URL, no longer in queue/
        self.assertFalse((self.queue / "a.md").exists())
        picked_path = Path(entry["path"])
        self.assertEqual(picked_path.parent, self.base / "picked")
        fm = qt.read_frontmatter(picked_path)
        self.assertEqual(fm["status"], "done")
        self.assertEqual(fm["triaged-to"], pr_url)

        # target change's proposal.md/tasks.md were edited in the worktree
        change_dir = self.worktree_dir / "openspec" / "changes" / self.target_change
        proposal_text = (change_dir / "proposal.md").read_text(encoding="utf-8")
        self.assertIn("## Folded from a", proposal_text)
        self.assertIn(self.verdict.evidence, proposal_text)
        tasks_text = (change_dir / "tasks.md").read_text(encoding="utf-8")
        self.assertIn("## 2. Folded from a", tasks_text)
        self.assertIn("- [ ] 2.1", tasks_text)
        self.assertIn(self.verdict.evidence, tasks_text)

    def test_multiline_evidence_is_collapsed_in_the_tasks_checklist_line(self):
        """A `- [ ] N.1` item is one line: embedded newlines in the evidence
        would spill its tail out of the checklist item. The `proposal.md`
        prose section keeps the evidence verbatim."""
        multiline = qt.Verdict(
            brief_id="a",
            verdict="fold-into-change",
            duplicate_of=None,
            evidence=(
                "overlaps open tasks in widget-export-pipeline\n"
                "specifically the serializer work\n\nand its docs"
            ),
            confidence="high",
            target_change=self.target_change,
            repo=str(self.repo),
        )
        run, _ = self._dispatcher()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([multiline], confirm=True)

        self.assertEqual(log[0]["status"], "executed", log[0])
        change_dir = self.worktree_dir / "openspec" / "changes" / self.target_change

        tasks_text = (change_dir / "tasks.md").read_text(encoding="utf-8")
        task_line = next(
            line for line in tasks_text.splitlines() if line.startswith("- [ ] 2.1 ")
        )
        self.assertEqual(
            task_line,
            "- [ ] 2.1 overlaps open tasks in widget-export-pipeline specifically "
            "the serializer work and its docs",
        )

        # proposal.md keeps the evidence's original line breaks
        proposal_text = (change_dir / "proposal.md").read_text(encoding="utf-8")
        self.assertIn(multiline.evidence, proposal_text)

    def test_pr_creation_failure_leaves_brief_untouched_and_reports_branch(self):
        run, _ = self._dispatcher(pr_returncode=1)
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("gh pr create failed", entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertNotIn("pr_url", entry)

        # brief is completely untouched -- still queued, no claim/close attempted
        self.assertTrue((self.queue / "a.md").exists())
        fm = qt.read_frontmatter(self.queue / "a.md")
        self.assertEqual(fm["status"], "queued")
        self.assertNotIn("triaged-to", fm)

    def test_validation_failure_leaves_brief_untouched_and_reports_branch(self):
        run, _ = self._dispatcher(validate_returncode=1)
        with mock.patch(
            "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("openspec validate failed", entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertNotIn("pr_url", entry)

        self.assertTrue((self.queue / "a.md").exists())
        fm = qt.read_frontmatter(self.queue / "a.md")
        self.assertEqual(fm["status"], "queued")
        self.assertNotIn("triaged-to", fm)

    def test_missing_target_change_files_is_an_error_before_pr(self):
        """No `_seed_change()` call happens before `openspec validate` here
        since proposal.md/tasks.md never existed in the first place -- the
        missing-files check must fire first."""

        def run(cmd, **kwargs):
            if cmd[0] == "git" and "-C" in cmd:
                if "symbolic-ref" in cmd:
                    return self._completed(0, stdout="origin/main\n")
                if "fetch" in cmd:
                    return self._completed(0)
                if "worktree" in cmd and "add" in cmd:
                    self.worktree_dir.mkdir(parents=True, exist_ok=True)
                    return self._completed(0)
                if "worktree" in cmd and "remove" in cmd:
                    return self._completed(0)
                if "branch" in cmd and "-D" in cmd:
                    return self._completed(0)
            raise AssertionError(f"unexpected command: {cmd}")

        with mock.patch(
            "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("has no proposal.md/tasks.md", entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertTrue((self.queue / "a.md").exists())

    def test_compile_marker_is_written_before_commit(self):
        """CI's Scope check (`check_compile_markers.py`) refuses a change PR
        whose `.compile-ok` is missing or stale against `tasks.md` (live
        2026-09-02: worktrail #897/#898)."""
        run, _ = self._dispatcher()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        self.assertEqual(log[0]["status"], "executed", log[0])
        self.assertTrue(self.marker_at_commit)
        compile_calls = [c for c in self.seen if c[0] == "worktrail-compile"]
        self.assertEqual(len(compile_calls), 1)
        self.assertTrue(compile_calls[0][1].endswith(self.target_change))

    def test_compile_failure_blocks_pr_and_leaves_brief_untouched(self):
        run, _ = self._dispatcher(compile_returncode=1)
        before = self.brief_path.read_text(encoding="utf-8")
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        self.assertEqual(log[0]["status"], "error")
        self.assertIn("worktrail-compile failed", log[0]["error"])
        self.assertFalse(any(c[:3] == ["gh", "pr", "create"] for c in self.seen))
        self.assertFalse(any(c[:2] == ["git", "push"] for c in self.seen))
        self.assertEqual(self.brief_path.read_text(encoding="utf-8"), before)

    def test_push_default_remote_routes_push_and_pr_to_the_fork(self):
        """`git config remote.pushDefault fork` means push there and open the
        PR against that remote's repo, never upstream `origin` (live
        2026-09-02: aspens propose-change pushed to aspenkit/aspens, denied)."""
        run, _ = self._dispatcher(push_default="fork")
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        self.assertEqual(log[0]["status"], "executed", log[0])
        push = next(c for c in self.seen if c[:2] == ["git", "push"])
        self.assertEqual(push, ["git", "push", "-u", "fork", self.branch])
        pr = next(c for c in self.seen if c[:3] == ["gh", "pr", "create"])
        self.assertEqual(pr[3:5], ["-R", "acme-fork/widgets"])

    def test_fetch_failure_leaves_brief_untouched_and_reports_branch(self):
        """A failed `git fetch origin <base>` short-circuits before any
        worktree exists -- same untouched-brief/reported-branch shape as
        every other pre-PR failure."""
        run, _ = self._dispatcher(fetch_returncode=1)
        with mock.patch(
            "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("git fetch origin main failed", entry["error"])
        self.assertIn("could not read from remote repository", entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertNotIn("pr_url", entry)

        # nothing was created: no worktree add attempted, none left behind
        self.assertFalse(any("worktree" in c and "add" in c for c in self.seen))
        self.assertFalse(self.worktree_dir.exists())

        # brief is completely untouched -- still queued, no claim/close attempted
        self.assertTrue((self.queue / "a.md").exists())
        fm = qt.read_frontmatter(self.queue / "a.md")
        self.assertEqual(fm["status"], "queued")
        self.assertNotIn("triaged-to", fm)

    def test_worktree_is_created_off_the_fetched_remote_base_ref(self):
        """Branching off the *local* base branch in a long-lived checkout
        opens a PR that reverts already-merged work; fetch first, then branch
        off `origin/<base>`."""
        run, _ = self._dispatcher()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        self.assertEqual(log[0]["status"], "executed", log[0])
        fetch = next(c for c in self.seen if "fetch" in c)
        self.assertEqual(fetch[3:], ["fetch", "origin", "main"])
        add = next(c for c in self.seen if "worktree" in c and "add" in c)
        self.assertEqual(add[-1], "origin/main")
        self.assertLess(self.seen.index(fetch), self.seen.index(add))

    def test_without_push_default_pushes_origin_and_lets_gh_infer_repo(self):
        run, _ = self._dispatcher()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            qt.apply_verdicts([self.verdict], confirm=True)

        push = next(c for c in self.seen if c[:2] == ["git", "push"])
        self.assertEqual(push[3], "origin")
        pr = next(c for c in self.seen if c[:3] == ["gh", "pr", "create"])
        self.assertNotIn("-R", pr)


class TestApplyProposeChange(QueueTriageTestBase):
    """3.2's `propose-change` apply action: fresh worktree off the target
    repo's base, `openspec new change`, an agent-authored proposal/design/
    specs/tasks, `openspec validate`, commit, push, `gh pr create`, then
    close the brief with `triaged-to:`. Any failure before the PR exists
    must leave the brief completely untouched and report the branch name
    it would have used. `git`/`gh`/`openspec` are faked via a
    `subprocess.run` dispatcher, and the evaluator agent via
    `spawnlib.spawn_agent` -- never hits the network.
    """

    def setUp(self):
        super().setUp()
        self.repo = self.base / "repo"
        self.repo.mkdir(parents=True, exist_ok=True)
        self.proposed_change_name = "widget-export-pipeline-v2"
        self.brief_path = self.write(
            "a.md", repo=str(self.repo), body="## Focus\n\npropose this\n"
        )
        self.verdict = qt.Verdict(
            brief_id="a",
            verdict="propose-change",
            duplicate_of=None,
            evidence="no good fold target, deserves its own change",
            confidence="high",
            target_repo=str(self.repo),
            proposed_change_name=self.proposed_change_name,
            repo=str(self.repo),
        )
        self.branch = qt._planned_fold_propose_branch(self.verdict)
        self.worktree_dir = qt._fold_propose_worktree_dir(self.repo, self.branch)
        self.change_dir = (
            self.worktree_dir / "openspec" / "changes" / self.proposed_change_name
        )

    def _write_change(self) -> None:
        self.change_dir.mkdir(parents=True, exist_ok=True)
        (self.change_dir / "proposal.md").write_text(
            "# Widget export pipeline v2\n\n## Why\n\nAgent-authored rationale.\n",
            encoding="utf-8",
        )
        (self.change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 Agent-authored task\n",
            encoding="utf-8",
        )

    @staticmethod
    def _completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
        return subprocess.CompletedProcess(
            args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _spawn(self, *, writes_change: bool = True):
        from worktrail.orchestrator.spawnlib import SpawnResult

        def _run(prompt, cwd, **kwargs):
            if writes_change:
                self._write_change()
            return SpawnResult(text="done authoring the change", usage={})

        return _run

    def _dispatcher(
        self,
        *,
        pr_returncode: int = 0,
        validate_returncode: int = 0,
        new_change_returncode: int = 0,
    ):
        pr_url = "https://github.com/acme/widgets/pull/43"
        self.seen: list[list[str]] = []

        def _run(cmd, **kwargs):
            self.seen.append(list(cmd))
            if cmd[0] == "git" and "-C" in cmd:
                if "symbolic-ref" in cmd:
                    return self._completed(0, stdout="origin/main\n")
                if "config" in cmd and "remote.pushDefault" in cmd:
                    return self._completed(1)
                if "fetch" in cmd:
                    return self._completed(0)
                if "worktree" in cmd and "add" in cmd:
                    self.worktree_dir.mkdir(parents=True, exist_ok=True)
                    return self._completed(0)
                if "worktree" in cmd and "remove" in cmd:
                    return self._completed(0)
                if "branch" in cmd and "-D" in cmd:
                    return self._completed(0)
            if cmd[0] == "openspec" and cmd[1:3] == ["new", "change"]:
                return self._completed(
                    new_change_returncode,
                    stdout="",
                    stderr=""
                    if new_change_returncode == 0
                    else "change already exists",
                )
            if cmd[0] == "openspec" and cmd[1] == "validate":
                return self._completed(
                    validate_returncode,
                    stdout="valid\n" if validate_returncode == 0 else "",
                    stderr="" if validate_returncode == 0 else "task 1.1 has no owner",
                )
            if cmd[0] == "worktrail-compile":
                Path(cmd[1]).mkdir(parents=True, exist_ok=True)
                (Path(cmd[1]) / ".compile-ok").write_text("fp\n", encoding="utf-8")
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] in ("add", "commit"):
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] == "push":
                return self._completed(0)
            if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
                return self._completed(
                    pr_returncode,
                    stdout=f"{pr_url}\n" if pr_returncode == 0 else "",
                    stderr=""
                    if pr_returncode == 0
                    else "could not create pull request",
                )
            raise AssertionError(f"unexpected command: {cmd}")

        return _run, pr_url

    def test_success_authors_change_opens_pr_and_closes_brief(self):
        run, pr_url = self._dispatcher()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                side_effect=self._spawn(),
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["brief_id"], "a")
        self.assertEqual(entry["action"], "open-pull-request")
        self.assertEqual(entry["status"], "executed")
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertEqual(entry["pr_url"], pr_url)

        # brief closed with triaged-to: the PR URL, no longer in queue/
        self.assertFalse((self.queue / "a.md").exists())
        picked_path = Path(entry["path"])
        self.assertEqual(picked_path.parent, self.base / "picked")
        fm = qt.read_frontmatter(picked_path)
        self.assertEqual(fm["status"], "done")
        self.assertEqual(fm["triaged-to"], pr_url)

        # the evaluator agent's proposal.md/tasks.md exist in the worktree
        self.assertTrue((self.change_dir / "proposal.md").is_file())
        self.assertTrue((self.change_dir / "tasks.md").is_file())

    def test_validation_failure_leaves_brief_untouched_and_reports_branch(self):
        run, _ = self._dispatcher(validate_returncode=1)
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                side_effect=self._spawn(),
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("openspec validate failed", entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertNotIn("pr_url", entry)

        # brief is completely untouched -- still queued, no claim/close attempted
        self.assertTrue((self.queue / "a.md").exists())
        fm = qt.read_frontmatter(self.queue / "a.md")
        self.assertEqual(fm["status"], "queued")
        self.assertNotIn("triaged-to", fm)

    def test_worktree_is_created_off_the_fetched_remote_base_ref(self):
        """`_worktree_pr_close()` is shared with the fold path: propose must
        branch off `origin/<base>` after a fetch too."""
        run, _ = self._dispatcher()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                side_effect=self._spawn(),
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        self.assertEqual(log[0]["status"], "executed", log[0])
        fetch = next(c for c in self.seen if "fetch" in c)
        self.assertEqual(fetch[3:], ["fetch", "origin", "main"])
        add = next(c for c in self.seen if "worktree" in c and "add" in c)
        self.assertEqual(add[-1], "origin/main")
        self.assertLess(self.seen.index(fetch), self.seen.index(add))

    def test_openspec_new_change_failure_is_an_error_before_agent_spawns(self):
        run, _ = self._dispatcher(new_change_returncode=1)
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch("worktrail.orchestrator.spawnlib.spawn_agent") as mock_spawn,
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        mock_spawn.assert_not_called()
        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("openspec new change failed", entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertTrue((self.queue / "a.md").exists())

    def test_agent_produces_no_proposal_is_an_error_before_validate(self):
        run, _ = self._dispatcher()

        def _no_validate(cmd, **kwargs):
            if cmd[0] == "openspec" and cmd[1] == "validate":
                raise AssertionError("validate must not run without proposal/tasks")
            return run(cmd, **kwargs)

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run",
                side_effect=_no_validate,
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent",
                side_effect=self._spawn(writes_change=False),
            ),
        ):
            log = qt.apply_verdicts([self.verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("did not produce proposal.md/tasks.md", entry["error"])
        self.assertEqual(entry["branch"], self.branch)
        self.assertTrue((self.queue / "a.md").exists())

    def test_missing_repo_or_proposed_change_name_is_an_error(self):
        bad = qt.Verdict(
            brief_id="a",
            verdict="propose-change",
            duplicate_of=None,
            evidence="deserves its own change",
            confidence="high",
            target_repo=str(self.repo),
            proposed_change_name=None,
            repo=str(self.repo),
        )

        log = qt.apply_verdicts([bad], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("missing repo or proposed_change_name", entry["error"])
        self.assertTrue((self.queue / "a.md").exists())


class TestApplyRepoResolution(QueueTriageTestBase):
    """1.1: `repo` must be resolved to an on-disk checkout (via
    `_resolve_repo_dir()`, basename-under-`repos_root` for bare names) before
    any worktree/git op, for both `fold-into-change` and `propose-change`.
    """

    def setUp(self):
        super().setUp()
        self.repos_root = self.base / "repos"
        self.repos_root.mkdir(parents=True, exist_ok=True)
        self.repo_dir = self.repos_root / "widgets"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.target_change = "widget-export-pipeline"
        self.proposed_change_name = "widget-export-pipeline-v2"

    @staticmethod
    def _completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
        return subprocess.CompletedProcess(
            args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _fold_verdict(self, repo: str) -> qt.Verdict:
        return qt.Verdict(
            brief_id="a",
            verdict="fold-into-change",
            duplicate_of=None,
            evidence="overlaps open tasks in widget-export-pipeline",
            confidence="high",
            target_change=self.target_change,
            repo=repo,
        )

    def _propose_verdict(self, repo: str) -> qt.Verdict:
        return qt.Verdict(
            brief_id="a",
            verdict="propose-change",
            duplicate_of=None,
            evidence="no good fold target, deserves its own change",
            confidence="high",
            target_repo=repo,
            proposed_change_name=self.proposed_change_name,
            repo=repo,
        )

    def _seed_fold_change(self, worktree_dir: Path) -> None:
        change_dir = worktree_dir / "openspec" / "changes" / self.target_change
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            "# Widget export pipeline\n\n## Why\n\nExisting rationale.\n",
            encoding="utf-8",
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 Existing task\n",
            encoding="utf-8",
        )

    def _fold_dispatcher(self, worktree_dir: Path):
        pr_url = "https://github.com/acme/widgets/pull/42"

        def _run(cmd, **kwargs):
            if cmd[0] == "git" and "-C" in cmd:
                if "symbolic-ref" in cmd:
                    return self._completed(0, stdout="origin/main\n")
                if "config" in cmd and "remote.pushDefault" in cmd:
                    return self._completed(1)
                if "remote" in cmd and "get-url" in cmd:
                    return self._completed(
                        0, stdout="git@github.com:acme-fork/widgets.git\n"
                    )
                if "fetch" in cmd:
                    return self._completed(0)
                if "worktree" in cmd and "add" in cmd:
                    self._seed_fold_change(worktree_dir)
                    return self._completed(0)
                if "worktree" in cmd and "remove" in cmd:
                    return self._completed(0)
                if "branch" in cmd and "-D" in cmd:
                    return self._completed(0)
            if cmd[0] == "openspec" and cmd[1] == "validate":
                return self._completed(0, stdout="valid\n")
            if cmd[0] == "worktrail-compile":
                (Path(cmd[1]) / ".compile-ok").write_text("fp\n", encoding="utf-8")
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] in ("add", "commit"):
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] == "push":
                return self._completed(0)
            if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
                return self._completed(0, stdout=f"{pr_url}\n")
            raise AssertionError(f"unexpected command: {cmd}")

        return _run, pr_url

    def test_bare_repo_name_resolves_under_repos_root_for_fold(self):
        self.write("a.md", repo="widgets", body="## Focus\n\nfold this in\n")
        verdict = self._fold_verdict("widgets")
        branch = qt._planned_fold_propose_branch(verdict)
        worktree_dir = qt._fold_propose_worktree_dir(self.repo_dir, branch)
        run, pr_url = self._fold_dispatcher(worktree_dir)

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        entry = log[0]
        self.assertEqual(entry["status"], "executed")
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["pr_url"], pr_url)

    def _seed_propose_change(self, worktree_dir: Path) -> None:
        change_dir = worktree_dir / "openspec" / "changes" / self.proposed_change_name
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            "# Widget export pipeline v2\n\n## Why\n\nAgent-authored rationale.\n",
            encoding="utf-8",
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 Agent-authored task\n",
            encoding="utf-8",
        )

    def _propose_dispatcher(self, worktree_dir: Path):
        pr_url = "https://github.com/acme/widgets/pull/43"

        def _run(cmd, **kwargs):
            if cmd[0] == "git" and "-C" in cmd:
                if "symbolic-ref" in cmd:
                    return self._completed(0, stdout="origin/main\n")
                if "config" in cmd and "remote.pushDefault" in cmd:
                    return self._completed(1)
                if "remote" in cmd and "get-url" in cmd:
                    return self._completed(
                        0, stdout="git@github.com:acme-fork/widgets.git\n"
                    )
                if "fetch" in cmd:
                    return self._completed(0)
                if "worktree" in cmd and "add" in cmd:
                    worktree_dir.mkdir(parents=True, exist_ok=True)
                    return self._completed(0)
                if "worktree" in cmd and "remove" in cmd:
                    return self._completed(0)
                if "branch" in cmd and "-D" in cmd:
                    return self._completed(0)
            if cmd[0] == "openspec" and cmd[1:3] == ["new", "change"]:
                return self._completed(0)
            if cmd[0] == "openspec" and cmd[1] == "validate":
                return self._completed(0, stdout="valid\n")
            if cmd[0] == "worktrail-compile":
                Path(cmd[1]).mkdir(parents=True, exist_ok=True)
                (Path(cmd[1]) / ".compile-ok").write_text("fp\n", encoding="utf-8")
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] in ("add", "commit"):
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] == "push":
                return self._completed(0)
            if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
                return self._completed(0, stdout=f"{pr_url}\n")
            raise AssertionError(f"unexpected command: {cmd}")

        return _run, pr_url

    def test_bare_repo_name_resolves_under_repos_root_for_propose(self):
        self.write("a.md", repo="widgets", body="## Focus\n\npropose this\n")
        verdict = self._propose_verdict("widgets")
        branch = qt._planned_fold_propose_branch(verdict)
        worktree_dir = qt._fold_propose_worktree_dir(self.repo_dir, branch)
        run, pr_url = self._propose_dispatcher(worktree_dir)

        def _spawn(prompt, cwd, **kwargs):
            from worktrail.orchestrator.spawnlib import SpawnResult

            self._seed_propose_change(worktree_dir)
            return SpawnResult(text="done authoring the change", usage={})

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent", side_effect=_spawn
            ),
        ):
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        entry = log[0]
        self.assertEqual(entry["status"], "executed")
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["pr_url"], pr_url)

    def test_unresolvable_repo_is_an_error_and_never_shells_out(self):
        verdict = self._fold_verdict("no-such-repo")

        with mock.patch("worktrail.workqueue.queue_triage.subprocess.run") as run_mock:
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        run_mock.assert_not_called()
        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("no-such-repo", entry["error"])

    def test_unresolvable_repo_is_an_error_for_propose_and_never_shells_out(self):
        verdict = self._propose_verdict("no-such-repo")

        with mock.patch("worktrail.workqueue.queue_triage.subprocess.run") as run_mock:
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        run_mock.assert_not_called()
        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("no-such-repo", entry["error"])

    def test_absolute_repo_path_unaffected_with_no_repos_root(self):
        self.write("a.md", repo=str(self.repo_dir), body="## Focus\n\nfold this in\n")
        verdict = self._fold_verdict(str(self.repo_dir))
        branch = qt._planned_fold_propose_branch(verdict)
        worktree_dir = qt._fold_propose_worktree_dir(self.repo_dir, branch)
        run, pr_url = self._fold_dispatcher(worktree_dir)

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["status"], "executed")
        self.assertIsNone(entry["error"])
        self.assertEqual(entry["pr_url"], pr_url)

    def test_cmd_apply_forwards_explicit_repos_root(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [self._fold_verdict("widgets")]
        verdict_path = qt.write_verdict_file(verdicts, self.base / "out")

        with mock.patch(
            "worktrail.workqueue.queue_triage.apply_verdicts"
        ) as apply_mock:
            apply_mock.return_value = []
            qt.main(
                [
                    "apply",
                    "--verdict-file",
                    str(verdict_path),
                    "--repos-root",
                    str(self.repos_root),
                ]
            )

        self.assertEqual(
            apply_mock.call_args.kwargs["repos_root"], str(self.repos_root)
        )

    def test_cmd_apply_defaults_repos_root_to_home_projects(self):
        self.write("a.md", body="## Focus\n\nsome brief\n")
        verdicts = [self._fold_verdict("widgets")]
        verdict_path = qt.write_verdict_file(verdicts, self.base / "out")

        with mock.patch(
            "worktrail.workqueue.queue_triage.apply_verdicts"
        ) as apply_mock:
            apply_mock.return_value = []
            qt.main(["apply", "--verdict-file", str(verdict_path)])

        self.assertEqual(
            apply_mock.call_args.kwargs["repos_root"],
            str(Path.home() / "projects"),
        )


class TestApplyProposeChangeRepoLessStamp(QueueTriageTestBase):
    """2.5: a `propose-change` verdict from the `__none__` group carries its
    target repo on `target_repo`, not `repo` -- `_apply_propose_change()` must
    resolve that effective repo, stamp `repo:` onto the still-queued brief
    before touching a worktree, and only when resolution actually succeeds.
    """

    def setUp(self):
        super().setUp()
        self.repos_root = self.base / "repos"
        self.repos_root.mkdir(parents=True, exist_ok=True)
        self.repo_dir = self.repos_root / "widgets"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.proposed_change_name = "widget-export-pipeline-v2"

    @staticmethod
    def _completed(returncode: int, stdout: str = "", stderr: str = "") -> Any:
        return subprocess.CompletedProcess(
            args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr
        )

    def _verdict(self, *, target_repo: str = "widgets") -> qt.Verdict:
        return qt.Verdict(
            brief_id="a",
            verdict="propose-change",
            duplicate_of=None,
            evidence="no clear owning repo, but this one fits",
            confidence="high",
            target_repo=target_repo,
            proposed_change_name=self.proposed_change_name,
            repo=qt.NO_REPO_KEY,
        )

    def _seed_change(self, worktree_dir: Path) -> None:
        change_dir = worktree_dir / "openspec" / "changes" / self.proposed_change_name
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            "# Widget export pipeline v2\n\n## Why\n\nAgent-authored rationale.\n",
            encoding="utf-8",
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 Agent-authored task\n", encoding="utf-8"
        )

    def _dispatcher(self, worktree_dir: Path, *, validate_returncode: int = 0):
        pr_url = "https://github.com/acme/widgets/pull/43"

        def _run(cmd, **kwargs):
            if cmd[0] == "git" and "-C" in cmd:
                if "symbolic-ref" in cmd:
                    return self._completed(0, stdout="origin/main\n")
                if "config" in cmd and "remote.pushDefault" in cmd:
                    return self._completed(1)
                if "fetch" in cmd:
                    return self._completed(0)
                if "worktree" in cmd and "add" in cmd:
                    worktree_dir.mkdir(parents=True, exist_ok=True)
                    return self._completed(0)
                if "worktree" in cmd and "remove" in cmd:
                    return self._completed(0)
                if "branch" in cmd and "-D" in cmd:
                    return self._completed(0)
            if cmd[0] == "openspec" and cmd[1:3] == ["new", "change"]:
                return self._completed(0)
            if cmd[0] == "openspec" and cmd[1] == "validate":
                return self._completed(
                    validate_returncode,
                    stdout="valid\n" if validate_returncode == 0 else "",
                    stderr="" if validate_returncode == 0 else "invalid change",
                )
            if cmd[0] == "worktrail-compile":
                Path(cmd[1]).mkdir(parents=True, exist_ok=True)
                (Path(cmd[1]) / ".compile-ok").write_text("fp\n", encoding="utf-8")
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] in ("add", "commit"):
                return self._completed(0)
            if cmd[0] == "git" and cmd[1] == "push":
                return self._completed(0)
            if cmd[0] == "gh" and cmd[1:3] == ["pr", "create"]:
                return self._completed(0, stdout=f"{pr_url}\n")
            raise AssertionError(f"unexpected command: {cmd}")

        return _run, pr_url

    def test_stamps_repo_before_worktree_op_and_survives_pr_failure(self):
        self.write("a.md", body="## Focus\n\npropose this\n")
        verdict = self._verdict()
        branch = qt._planned_fold_propose_branch(verdict)
        worktree_dir = qt._fold_propose_worktree_dir(self.repo_dir, branch)
        run, _pr_url = self._dispatcher(worktree_dir, validate_returncode=1)

        def _spawn(prompt, cwd, **kwargs):
            from worktrail.orchestrator.spawnlib import SpawnResult

            self._seed_change(worktree_dir)
            return SpawnResult(text="done authoring the change", usage={})

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run", side_effect=run
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent", side_effect=_spawn
            ) as mock_spawn,
        ):
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("openspec validate failed", entry["error"])
        self.assertEqual(entry["stamped"], {"repo": "widgets"})
        mock_spawn.assert_called_once()

        # brief still queued (open-pull-request never closes on failure), but
        # the repo stamp survives the downstream PR failure regardless.
        fm = qt.read_frontmatter(self.queue / "a.md")
        self.assertEqual(fm["repo"], "widgets")
        self.assertEqual(fm["status"], "queued")

    def test_stamp_call_precedes_first_worktree_op(self):
        # The tests above prove the stamp *value* survives a downstream
        # failure or an unresolvable repo, but neither actually orders calls
        # -- a stamp written after `git worktree add` would pass them just as
        # well. Record call order directly across both patched seams and
        # assert the stamp happens strictly before the first worktree op.
        self.write("a.md", body="## Focus\n\npropose this\n")
        verdict = self._verdict()
        branch = qt._planned_fold_propose_branch(verdict)
        worktree_dir = qt._fold_propose_worktree_dir(self.repo_dir, branch)
        run, pr_url = self._dispatcher(worktree_dir)

        call_order: list[str] = []

        def _tracked_run(cmd, **kwargs):
            if cmd[0] == "git" and "worktree" in cmd and "add" in cmd:
                call_order.append("worktree_add")
            return run(cmd, **kwargs)

        real_set_fm_fields = qt._set_fm_fields

        def _tracked_set_fm_fields(*args, **kwargs):
            call_order.append("stamp")
            return real_set_fm_fields(*args, **kwargs)

        def _spawn(prompt, cwd, **kwargs):
            from worktrail.orchestrator.spawnlib import SpawnResult

            self._seed_change(worktree_dir)
            return SpawnResult(text="done authoring the change", usage={})

        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.subprocess.run",
                side_effect=_tracked_run,
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._set_fm_fields",
                side_effect=_tracked_set_fm_fields,
            ),
            mock.patch(
                "worktrail.orchestrator.spawnlib.spawn_agent", side_effect=_spawn
            ),
            mock.patch(
                "worktrail.workqueue.queue_triage._refresh_pr_labels",
                return_value=["go:risk-low"],
            ),
        ):
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        entry = log[0]
        self.assertEqual(entry["status"], "executed")
        self.assertEqual(entry["pr_url"], pr_url)
        self.assertIn("stamp", call_order)
        self.assertIn("worktree_add", call_order)
        self.assertLess(call_order.index("stamp"), call_order.index("worktree_add"))

    def test_missing_brief_fails_closed_before_worktree_op(self):
        # No brief written to queue/ or picked/ for brief_id "a" -- the stamp
        # is required but there is nothing to stamp, so this must error out
        # before touching a worktree/PR, not silently proceed unstamped.
        verdict = self._verdict()

        with mock.patch("worktrail.workqueue.queue_triage.subprocess.run") as run_mock:
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        run_mock.assert_not_called()
        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("brief not found", entry["error"])
        self.assertNotIn("stamped", entry)

    def test_failed_stamp_write_fails_closed_before_worktree_op(self):
        # An unterminated frontmatter fence makes _set_fm_fields() raise
        # ValueError -- that must surface as an error action-log entry, not
        # an uncaught exception that abandons the rest of the verdict batch.
        path = self.queue / "a.md"
        path.write_text(
            "---\nfocus: a\nstatus: queued\n## Focus\n\nbody\n", encoding="utf-8"
        )
        verdict = self._verdict()

        with mock.patch("worktrail.workqueue.queue_triage.subprocess.run") as run_mock:
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        run_mock.assert_not_called()
        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("closing fence", entry["error"])
        self.assertNotIn("stamped", entry)

    def test_unresolvable_target_repo_stamps_nothing(self):
        self.write("a.md", body="## Focus\n\npropose this\n")
        verdict = self._verdict(target_repo="no-such-repo")

        with mock.patch("worktrail.workqueue.queue_triage.subprocess.run") as run_mock:
            log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)

        run_mock.assert_not_called()
        entry = log[0]
        self.assertEqual(entry["status"], "error")
        self.assertIn("no-such-repo", entry["error"])
        self.assertNotIn("stamped", entry)

        fm = qt.read_frontmatter(self.queue / "a.md")
        self.assertNotIn("repo", fm)

    def test_preview_reports_planned_stamp(self):
        verdict = self._verdict()

        log = qt.apply_verdicts([verdict], confirm=False, repos_root=self.repos_root)

        entry = log[0]
        self.assertEqual(entry["status"], "planned")
        self.assertEqual(entry["planned_stamp"], {"repo": "widgets"})

    def test_wip_cap_check_reads_effective_repo(self):
        change_dir = self.repo_dir / "openspec" / "changes" / "existing-change"
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            "# Existing change\n\n## Why\nalready in flight.\n", encoding="utf-8"
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 do the thing\n", encoding="utf-8"
        )
        policy_dir = self.repo_dir / ".worktrail"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "policy.yaml").write_text(
            "max_active_changes: 1\n", encoding="utf-8"
        )

        verdict = self._verdict(target_repo=str(self.repo_dir))
        self.assertTrue(qt._propose_change_over_cap(verdict))

    def test_wip_cap_check_resolves_bare_repo_name_under_repos_root(self):
        """The `__none__` flow's `target_repo` is a bare basename (e.g.
        `widgets`), not an absolute path -- `_propose_change_over_cap()` must
        resolve it against `repos_root` before reading its cap/count, not
        inspect a `./widgets` relative to the process cwd."""
        change_dir = self.repo_dir / "openspec" / "changes" / "existing-change"
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            "# Existing change\n\n## Why\nalready in flight.\n", encoding="utf-8"
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 do the thing\n", encoding="utf-8"
        )
        policy_dir = self.repo_dir / ".worktrail"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "policy.yaml").write_text(
            "max_active_changes: 1\n", encoding="utf-8"
        )

        verdict = self._verdict(target_repo="widgets")

        self.assertFalse(qt._propose_change_over_cap(verdict))
        self.assertTrue(
            qt._propose_change_over_cap(verdict, repos_root=self.repos_root)
        )

        self.write("a.md", repo=qt.NO_REPO_KEY, body="## Focus\n\npropose this\n")
        log = qt.apply_verdicts([verdict], confirm=True, repos_root=self.repos_root)
        entry = log[0]
        self.assertEqual(entry["status"], "downgraded-to-keep")
        self.assertIn("widgets", entry["note"])


class TestApplyPropseChangeWipCapDowngrade(QueueTriageTestBase):
    """3.6: `apply_verdicts()` re-checks the target repo's active-change count
    against its `max_active_changes` policy cap at apply time (not just
    2.4's evaluate-time `held_by_wip_cap` preview), downgrading an at/over-cap
    `propose-change` to a no-op `keep` plus a `## Triage <date>` note naming
    the cap, the count, and the top fold candidates -- `fold-into-change`,
    `work-directly`, and `needs-decision` are never throttled by this key.
    """

    def _make_active_change(self, repo_root: Path, change_id: str) -> None:
        change_dir = repo_root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            f"# {change_id}\n\n## Why\nsome change.\n", encoding="utf-8"
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 do the thing\n", encoding="utf-8"
        )

    def _write_policy(self, repo_root: Path, max_active_changes: int) -> None:
        policy_dir = repo_root / ".worktrail"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "policy.yaml").write_text(
            f"max_active_changes: {max_active_changes}\n", encoding="utf-8"
        )

    def test_propose_change_downgraded_when_over_cap(self):
        repo_root = self.base / "repo-over-cap"
        self._make_active_change(repo_root, "change-1")
        self._write_policy(repo_root, max_active_changes=1)
        self.write(
            "a.md",
            repo=str(repo_root),
            body="## Focus\n\npropose this\n",
            focus="do the thing for some change",
        )
        verdict = qt.Verdict(
            brief_id="a",
            verdict="propose-change",
            duplicate_of=None,
            evidence="deserves its own change",
            confidence="high",
            target_repo=str(repo_root),
            proposed_change_name="new-thing",
            repo=str(repo_root),
        )

        def _run(cmd, **kwargs):
            raise AssertionError(f"no subprocess call expected once over cap: {cmd}")

        with mock.patch(
            "worktrail.workqueue.queue_triage.subprocess.run", side_effect=_run
        ):
            log = qt.apply_verdicts([verdict], confirm=True)

        entry = log[0]
        self.assertEqual(entry["brief_id"], "a")
        self.assertEqual(entry["action"], "append-triage-note")
        self.assertEqual(entry["status"], "downgraded-to-keep")
        self.assertIsNone(entry["error"])
        self.assertIn("max_active_changes cap of 1", entry["note"])
        self.assertIn("1 active change", entry["note"])
        self.assertIn("change-1", entry["note"])

        # brief stays queued (not claimed/closed) with the triage note appended
        self.assertTrue((self.queue / "a.md").exists())
        content = (self.queue / "a.md").read_text(encoding="utf-8")
        run_date = datetime.date.today().isoformat()  # noqa: DTZ011
        self.assertIn(f"## Triage {run_date}", content)
        self.assertIn("change-1", content)
        fm = qt.read_frontmatter(self.queue / "a.md")
        self.assertEqual(fm["status"], "queued")

    def test_propose_change_proceeds_when_cap_unset(self):
        repo_root = self.base / "repo-no-cap"
        repo_root.mkdir(parents=True, exist_ok=True)
        self._make_active_change(repo_root, "change-1")
        # No policy.yaml at all -- max_active_changes defaults to 0 (disabled).
        self.write("a.md", repo=str(repo_root), body="## Focus\n\npropose this\n")
        verdict = qt.Verdict(
            brief_id="a",
            verdict="propose-change",
            duplicate_of=None,
            evidence="deserves its own change",
            confidence="high",
            target_repo=str(repo_root),
            proposed_change_name="new-thing",
            repo=str(repo_root),
        )

        with mock.patch(
            "worktrail.workqueue.queue_triage._apply_propose_change",
            return_value={"brief_id": "a", "status": "executed-stub"},
        ) as stub:
            log = qt.apply_verdicts([verdict], confirm=True)

        stub.assert_called_once()
        self.assertEqual(log[0]["status"], "executed-stub")

    def test_fold_into_change_not_throttled_even_over_cap(self):
        repo_root = self.base / "repo-fold-over-cap"
        self._make_active_change(repo_root, "change-1")
        self._write_policy(repo_root, max_active_changes=1)
        self.write("a.md", repo=str(repo_root), body="## Focus\n\nfold this\n")
        verdict = qt.Verdict(
            brief_id="a",
            verdict="fold-into-change",
            duplicate_of=None,
            evidence="overlaps change-1's open tasks",
            confidence="high",
            target_change="change-1",
            repo=str(repo_root),
        )

        with mock.patch(
            "worktrail.workqueue.queue_triage._apply_fold_into_change",
            return_value={"brief_id": "a", "status": "executed-stub"},
        ) as stub:
            log = qt.apply_verdicts([verdict], confirm=True)

        stub.assert_called_once()
        self.assertEqual(log[0]["status"], "executed-stub")


class TestEvaluateSkipsUnresolvedDecision(QueueTriageTestBase):
    """3.4's `inventory()` skip: a brief with an unresolved (open/answered)
    pending decision must not be re-evaluated by a later `evaluate` run.
    """

    def test_open_decision_excludes_brief_from_inventory_groups(self):
        from worktrail.workqueue import decisions

        path = self.write("a.md", repo="behindthedash/worktrail")
        result = decisions.ask(
            "Which repo should this brief target?",
            background="ambiguous",
            why="cannot infer from the brief alone",
            context="checked the repo listing, no clear match",
            options=["Option A", "Option B"],
            brief="a",
            queue_base=self.base,
        )
        self.assertEqual(result["status"], "created")
        self.assertTrue(result["brief_stamped"])

        groups, skipped, _escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25)
        )

        self.assertEqual(groups, {})
        self.assertNotIn(path, skipped)

    def test_resolved_decision_does_not_exclude_brief(self):
        from worktrail.workqueue import decisions

        self.write("a.md", repo="behindthedash/worktrail")
        result = decisions.ask(
            "Which repo should this brief target?",
            background="ambiguous",
            why="cannot infer from the brief alone",
            context="checked the repo listing, no clear match",
            options=["Option A", "Option B"],
            brief="a",
            queue_base=self.base,
        )
        decisions.answer(result["id"], "Use repo A", queue_base=self.base)
        decisions.consume_answer(result["id"], queue_base=self.base)

        groups, skipped, _escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25)
        )

        self.assertIn("behindthedash/worktrail", groups)
        self.assertEqual([p.name for p in groups["behindthedash/worktrail"]], ["a.md"])
        self.assertEqual(skipped, [])

    def test_brief_with_no_awaiting_decision_link_is_unaffected(self):
        path = self.write("a.md", repo="behindthedash/worktrail")

        groups, skipped, _escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25)
        )

        self.assertIn("behindthedash/worktrail", groups)
        self.assertEqual(groups["behindthedash/worktrail"], [path])
        self.assertEqual(skipped, [])


class TestResolveDuplicateTargets(unittest.TestCase):
    """6.5's dangling-`duplicate-of` resolution: a `duplicate-of` verdict whose
    target is verdicted non-`keep` in the same batch is downgraded to a no-op
    `keep`, with a warning logged, rather than acted on against a moving target.
    """

    def _dup(
        self, brief_id: str, target: str, evidence: str = "same premise"
    ) -> qt.Verdict:
        return qt.Verdict(
            brief_id=brief_id,
            verdict="duplicate-of",
            duplicate_of=target,
            evidence=evidence,
            confidence="medium",
        )

    def test_target_verdicted_stale_close_downgrades_referencing_verdict(self):
        verdicts = [
            self._dup("a", "b"),
            qt.Verdict(
                brief_id="b",
                verdict="stale-close",
                duplicate_of=None,
                evidence="already shipped",
                confidence="high",
            ),
        ]

        with self.assertLogs("worktrail.workqueue.queue_triage", level="WARNING") as cm:
            resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].brief_id, "a")
        self.assertEqual(resolved[0].verdict, "keep")
        self.assertIsNone(resolved[0].duplicate_of)
        self.assertEqual(resolved[0].evidence, "same premise")
        self.assertEqual(resolved[0].confidence, "medium")
        # target verdict is untouched
        self.assertEqual(resolved[1].verdict, "stale-close")
        self.assertTrue(any("dangling duplicate-of" in msg for msg in cm.output))

    def test_target_verdicted_needs_update_downgrades_referencing_verdict(self):
        verdicts = [
            self._dup("a", "b"),
            qt.Verdict(
                brief_id="b",
                verdict="needs-update",
                duplicate_of=None,
                evidence="file renamed",
                confidence="high",
            ),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "keep")
        self.assertIsNone(resolved[0].duplicate_of)

    def test_target_also_duplicate_of_downgrades_referencing_verdict(self):
        verdicts = [
            self._dup("a", "b"),
            self._dup("b", "c"),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "keep")
        self.assertIsNone(resolved[0].duplicate_of)
        # "b"'s own duplicate-of target ("c") is absent from the batch, so it
        # is left as-is
        self.assertEqual(resolved[1].verdict, "duplicate-of")
        self.assertEqual(resolved[1].duplicate_of, "c")

    def test_target_verdicted_keep_is_left_as_duplicate_of(self):
        verdicts = [
            self._dup("a", "b"),
            qt.Verdict(
                brief_id="b",
                verdict="keep",
                duplicate_of=None,
                evidence="still relevant",
                confidence="low",
            ),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "duplicate-of")
        self.assertEqual(resolved[0].duplicate_of, "b")

    def test_target_absent_from_batch_is_left_as_duplicate_of(self):
        verdicts = [self._dup("a", "not-in-this-batch")]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved[0].verdict, "duplicate-of")
        self.assertEqual(resolved[0].duplicate_of, "not-in-this-batch")

    def test_non_duplicate_of_verdicts_pass_through_unchanged(self):
        verdicts = [
            qt.Verdict(
                brief_id="a",
                verdict="stale-close",
                duplicate_of=None,
                evidence="shipped",
                confidence="high",
            ),
            qt.Verdict(
                brief_id="b",
                verdict="keep",
                duplicate_of=None,
                evidence="still relevant",
                confidence="low",
            ),
        ]

        resolved = qt.resolve_duplicate_targets(verdicts)

        self.assertEqual(resolved, verdicts)


class TestReportAndVerdictFileOutput(QueueTriageTestBase):
    """6.6: the Markdown report's verdict counts must match `verdict.json`'s
    contents exactly. `test_report_verdict_counts_match_json_file_exactly` and
    friends exercise `write_verdict_file()`/`write_report()` directly on a flat,
    hand-built `List[Verdict]` covering every verdict type (including a `keep`
    fallback); `test_multi_group_evaluate_run_report_matches_json` drives the
    actual multi-group aggregation path -- briefs across two distinct `repo:`
    values plus the `__none__` bucket, a skipped-via-dedup brief, through
    `qt.main(["evaluate", ...])` with `evaluate_group()` patched -- the same
    path `cmd_evaluate()` uses to accumulate verdicts across groups before
    handing them to the writers.
    """

    @staticmethod
    def _report_counts(report_text: str) -> dict:
        """Parse the `## Verdict counts` section's `- <type>: <n>` lines back into a dict."""
        section = report_text.split("## Verdict counts", 1)[1].split(
            "## Skipped via dedup", 1
        )[0]
        counts = {}
        for line in section.strip().splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue
            vtype, n = line[2:].rsplit(": ", 1)
            counts[vtype] = int(n)
        return counts

    def _verdicts(self):
        return [
            qt.Verdict(
                brief_id="repo-a-1",
                verdict="stale-close",
                duplicate_of=None,
                evidence="PR #42 already shipped this",
                confidence="high",
            ),
            qt.Verdict(
                brief_id="repo-a-2",
                verdict="stale-close",
                duplicate_of=None,
                evidence="repo archived",
                confidence="high",
            ),
            qt.Verdict(
                brief_id="repo-b-1",
                verdict="needs-update",
                duplicate_of=None,
                evidence="target file renamed",
                confidence="medium",
            ),
            qt.Verdict(
                brief_id="repo-b-2",
                verdict="duplicate-of",
                duplicate_of="repo-a-1",
                evidence="same premise as repo-a-1",
                confidence="medium",
            ),
            qt.Verdict(
                brief_id="__none__-1",
                verdict="keep",
                duplicate_of=None,
                evidence="still relevant",
                confidence="low",
            ),
            qt.Verdict(
                brief_id="__none__-2",
                verdict="keep",
                duplicate_of=None,
                evidence="the evaluator rambled and never emitted any JSON at all",
                confidence=None,
            ),
            qt.Verdict(
                brief_id="repo-a-3",
                verdict="fold-into-change",
                duplicate_of=None,
                evidence="overlaps an active change",
                confidence="high",
                target_change="intake-to-spec-triage",
            ),
            qt.Verdict(
                brief_id="repo-a-4",
                verdict="propose-change",
                duplicate_of=None,
                evidence="no existing change covers this",
                confidence="medium",
                proposed_change_name="new-thing",
            ),
            qt.Verdict(
                brief_id="repo-b-3",
                verdict="work-directly",
                duplicate_of=None,
                evidence="trivial enough to just do",
                confidence="high",
            ),
            qt.Verdict(
                brief_id="repo-b-4",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="ambiguous ownership",
                confidence="low",
                question="who owns this repo?",
            ),
        ]

    def test_report_verdict_counts_match_json_file_exactly(self):
        verdicts = self._verdicts()
        skipped = [self.write("skipped-1.md"), self.write("skipped-2.md")]
        out_dir = self.base / "out"

        verdict_path = qt.write_verdict_file(verdicts, out_dir)
        report_path = qt.write_report(verdicts, skipped, out_dir)

        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        json_counts: dict = {}
        for entry in json_verdicts:
            json_counts[entry["verdict"]] = json_counts.get(entry["verdict"], 0) + 1

        report_text = report_path.read_text(encoding="utf-8")
        report_counts = self._report_counts(report_text)

        # every verdict type is listed in the report (zero-filled), so compare
        # against the JSON counts zero-filled the same way rather than assuming
        # the report only lists types that actually occurred. The zero-fill set
        # is a literal here (not `qt.VALID_VERDICT_TYPES`) so a wrong member in
        # that constant can't cancel out on both sides of the comparison.
        expected_counts = {
            vtype: json_counts.get(vtype, 0)
            for vtype in (
                "keep",
                "stale-close",
                "needs-update",
                "duplicate-of",
                "fold-into-change",
                "propose-change",
                "work-directly",
                "needs-decision",
            )
        }
        self.assertEqual(report_counts, expected_counts)

        # and the JSON file itself carries no verdict type absent from the report
        for vtype in json_counts:
            self.assertIn(vtype, report_counts)

        # the four verdict types 2.4 added (M3): pin each to a non-zero count
        # tied to the JSON file, not just to 0, so a mis-rendered count line
        # for one of these types would fail this test.
        self.assertEqual(
            report_counts["fold-into-change"], json_counts["fold-into-change"]
        )
        self.assertEqual(report_counts["fold-into-change"], 1)
        self.assertEqual(report_counts["propose-change"], json_counts["propose-change"])
        self.assertEqual(report_counts["propose-change"], 1)
        self.assertEqual(report_counts["work-directly"], json_counts["work-directly"])
        self.assertEqual(report_counts["work-directly"], 1)
        self.assertEqual(report_counts["needs-decision"], json_counts["needs-decision"])
        self.assertEqual(report_counts["needs-decision"], 1)

        self.assertEqual(len(json_verdicts), len(verdicts))
        self.assertIn(f"Briefs evaluated: {len(verdicts)}", report_text)
        self.assertIn(f"Briefs skipped (recently triaged): {len(skipped)}", report_text)
        self.assertIn("## Skipped via dedup", report_text)
        for path in skipped:
            self.assertIn(f"- {path.stem}", report_text)

    def test_multi_group_evaluate_run_report_matches_json(self):
        """Drives `cmd_evaluate()`'s real accumulate-across-groups step
        (`queue_triage.py:701-712`), not the writers in isolation: two distinct
        `repo:` groups plus the `__none__` bucket, `evaluate_group()` patched per
        group, one brief skipped via a recent `## Triage` section.
        """
        self.write("a1.md", repo="behindthedash/repo-a")
        self.write("a2.md", repo="behindthedash/repo-a")
        self.write("b1.md", repo="behindthedash/repo-b")
        self.write("b2.md", repo="behindthedash/repo-b")
        self.write("n1.md")
        self.write("n2.md")
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        self.write(
            "skipped.md",
            repo="behindthedash/repo-a",
            body=f"## Triage {today}\n\nstill fresh, do not re-evaluate\n",
        )

        def fake_evaluate_group(
            repo, briefs, *, agent="claude", model=None, cwd=None, repos_root=None
        ):
            ids = [p.stem for p in briefs]
            if repo == "behindthedash/repo-a":
                raw = "\n".join(
                    [
                        json.dumps(
                            {
                                "brief_id": ids[0],
                                "verdict": "stale-close",
                                "duplicate_of": None,
                                "evidence": "PR #42 already shipped this",
                                "confidence": "high",
                            }
                        ),
                        json.dumps(
                            {
                                "brief_id": ids[1],
                                "verdict": "stale-close",
                                "duplicate_of": None,
                                "evidence": "repo archived",
                                "confidence": "high",
                            }
                        ),
                    ]
                )
            elif repo == "behindthedash/repo-b":
                raw = "\n".join(
                    [
                        json.dumps(
                            {
                                "brief_id": ids[0],
                                "verdict": "needs-update",
                                "duplicate_of": None,
                                "evidence": "target file renamed",
                                "confidence": "medium",
                            }
                        ),
                        json.dumps(
                            {
                                "brief_id": ids[1],
                                "verdict": "duplicate-of",
                                "duplicate_of": ids[0],
                                "evidence": "same premise",
                                "confidence": "medium",
                            }
                        ),
                    ]
                )
            else:
                raw = "\n".join(
                    [
                        json.dumps(
                            {
                                "brief_id": ids[0],
                                "verdict": "keep",
                                "duplicate_of": None,
                                "evidence": "still relevant",
                                "confidence": "low",
                            }
                        ),
                        "the evaluator rambled and never emitted valid JSON for the second brief",
                    ]
                )
            return [
                {
                    "repo": repo,
                    "brief_ids": ids,
                    "raw_text": raw,
                    "candidates_by_brief": {bid: [] for bid in ids},
                    "known_repos_by_brief": {},
                }
            ]

        out_dir = self.base / "out-run"
        with mock.patch(
            "worktrail.workqueue.queue_triage.evaluate_group",
            side_effect=fake_evaluate_group,
        ) as mock_eval:
            exit_code = qt.main(["evaluate", "--out-dir", str(out_dir)])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            {call.args[0] for call in mock_eval.call_args_list},
            {"behindthedash/repo-a", "behindthedash/repo-b", qt.NO_REPO_KEY},
        )

        verdict_path = out_dir / "verdict.json"
        report_path = out_dir / "report.md"
        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        json_counts: dict = {}
        for entry in json_verdicts:
            json_counts[entry["verdict"]] = json_counts.get(entry["verdict"], 0) + 1

        report_text = report_path.read_text(encoding="utf-8")
        report_counts = self._report_counts(report_text)

        expected_counts = {
            "keep": 0,
            "stale-close": 0,
            "needs-update": 0,
            "duplicate-of": 0,
            "fold-into-change": 0,
            "propose-change": 0,
            "work-directly": 0,
            "needs-decision": 0,
        }
        expected_counts.update(json_counts)
        self.assertEqual(report_counts, expected_counts)
        # The `__none__` group's two `keep`-resolving verdicts (one clean, one
        # a fallback from unparsable JSON) are both converted to
        # `needs-decision` with `REPO_ASSIGNMENT_QUESTION` by `parse_verdicts(
        # no_repo=True)` (4.1(d)) -- a repo-less brief must never be left as a
        # plain `keep`.
        self.assertEqual(
            report_counts,
            {
                "keep": 0,
                "stale-close": 2,
                "needs-update": 1,
                "duplicate-of": 1,
                "fold-into-change": 0,
                "propose-change": 0,
                "work-directly": 0,
                "needs-decision": 2,
            },
        )

        self.assertEqual(len(json_verdicts), 6)
        self.assertIn("Briefs evaluated: 6", report_text)
        self.assertIn("Briefs skipped (recently triaged): 1", report_text)
        self.assertIn("- skipped", report_text)

    def test_report_and_json_agree_on_empty_verdict_list(self):
        out_dir = self.base / "out-empty"

        verdict_path = qt.write_verdict_file([], out_dir)
        report_path = qt.write_report([], [], out_dir)

        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        self.assertEqual(json_verdicts, [])

        report_counts = self._report_counts(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report_counts, {vtype: 0 for vtype in qt.VALID_VERDICT_TYPES})

    def test_json_file_preserves_every_verdict_field_the_report_summarizes(self):
        verdicts = self._verdicts()
        out_dir = self.base / "out-fields"

        verdict_path = qt.write_verdict_file(verdicts, out_dir)
        report_path = qt.write_report(verdicts, [], out_dir)

        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        report_text = report_path.read_text(encoding="utf-8")

        self.assertEqual(
            [e["brief_id"] for e in json_verdicts], [v.brief_id for v in verdicts]
        )
        for entry in json_verdicts:
            # tie brief_id and verdict together, not just each present somewhere in
            # the report: `write_report()` zero-fills every verdict type in the
            # counts section regardless of the per-brief table, so checking
            # `entry["verdict"]` alone can never fail.
            self.assertIn(f"| {entry['brief_id']} | {entry['verdict']} |", report_text)
            if entry["duplicate_of"] is not None:
                self.assertIn(entry["duplicate_of"], report_text)
            if entry["confidence"] is not None:
                self.assertIn(entry["confidence"], report_text)


class TestWipCapPreviewAndCounts(QueueTriageTestBase):
    """2.4: `apply_wip_cap_preview()`'s `repo`/`held_by_wip_cap` stamping,
    `compute_run_summary()`'s `pull_requests_opened`/`held_by_wip_cap` counts,
    and their appearance (identically) in `write_report()`'s Markdown and
    `evaluate`'s `--json` output.
    """

    def _make_active_change(self, repo_root: Path, change_id: str) -> None:
        change_dir = repo_root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            f"# {change_id}\n\n## Why\nsome change.\n", encoding="utf-8"
        )
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 do the thing\n", encoding="utf-8"
        )

    def _write_policy(self, repo_root: Path, max_active_changes: int) -> None:
        policy_dir = repo_root / ".worktrail"
        policy_dir.mkdir(parents=True, exist_ok=True)
        (policy_dir / "policy.yaml").write_text(
            f"max_active_changes: {max_active_changes}\n", encoding="utf-8"
        )

    def _verdict(self, brief_id: str, verdict: str) -> qt.Verdict:
        return qt.Verdict(
            brief_id=brief_id,
            verdict=verdict,
            duplicate_of=None,
            evidence="evidence",
            confidence="high",
        )

    def test_no_repo_group_never_held(self):
        verdicts = [self._verdict("a", "keep")]

        stamped = qt.apply_wip_cap_preview(qt.NO_REPO_KEY, verdicts)

        self.assertEqual(stamped[0].repo, qt.NO_REPO_KEY)
        self.assertFalse(stamped[0].held_by_wip_cap)

    def test_cap_unset_never_holds_propose_change(self):
        repo_root = self.base / "repo-no-policy"
        self._make_active_change(repo_root, "change-1")
        self._make_active_change(repo_root, "change-2")
        verdicts = [self._verdict("a", "propose-change")]

        stamped = qt.apply_wip_cap_preview(str(repo_root), verdicts)

        self.assertEqual(stamped[0].repo, str(repo_root))
        self.assertFalse(stamped[0].held_by_wip_cap)

    def test_propose_change_held_when_at_cap(self):
        repo_root = self.base / "repo-at-cap"
        self._make_active_change(repo_root, "change-1")
        self._make_active_change(repo_root, "change-2")
        self._write_policy(repo_root, max_active_changes=2)
        verdicts = [
            self._verdict("a", "propose-change"),
            self._verdict("b", "keep"),
        ]

        stamped = qt.apply_wip_cap_preview(str(repo_root), verdicts)

        held = {v.brief_id: v.held_by_wip_cap for v in stamped}
        self.assertEqual(held, {"a": True, "b": False})

    def test_propose_change_not_held_under_cap(self):
        repo_root = self.base / "repo-under-cap"
        self._make_active_change(repo_root, "change-1")
        self._write_policy(repo_root, max_active_changes=2)
        verdicts = [self._verdict("a", "propose-change")]

        stamped = qt.apply_wip_cap_preview(str(repo_root), verdicts)

        self.assertFalse(stamped[0].held_by_wip_cap)

    def test_fold_into_change_never_held_even_at_cap(self):
        repo_root = self.base / "repo-fold-at-cap"
        self._make_active_change(repo_root, "change-1")
        self._write_policy(repo_root, max_active_changes=1)
        verdicts = [self._verdict("a", "fold-into-change")]

        stamped = qt.apply_wip_cap_preview(str(repo_root), verdicts)

        self.assertFalse(stamped[0].held_by_wip_cap)

    def test_compute_run_summary_counts(self):
        verdicts = [
            self._verdict("a", "fold-into-change"),
            self._verdict("b", "propose-change"),
            self._verdict("c", "keep"),
        ]
        verdicts[1].repo = "behindthedash/repo-a"
        verdicts[1].held_by_wip_cap = True

        summary = qt.compute_run_summary(verdicts)

        # "b" is a propose-change held by the WIP cap: 3.6 downgrades it to
        # `keep` at apply time, so it must not be counted as a PR that will
        # open (M1) -- only "a" (fold-into-change) counts.
        self.assertEqual(summary["pull_requests_opened"], 1)
        self.assertEqual(summary["held_by_wip_cap"], {"behindthedash/repo-a": 1})

    def test_write_report_pull_requests_and_wip_cap_sections_match_json(self):
        repo_root = self.base / "repo-report"
        self._make_active_change(repo_root, "change-1")
        self._write_policy(repo_root, max_active_changes=1)
        verdicts = qt.apply_wip_cap_preview(
            str(repo_root),
            [
                self._verdict("a", "propose-change"),
                self._verdict("b", "fold-into-change"),
            ],
        )
        out_dir = self.base / "out-wip"

        verdict_path = qt.write_verdict_file(verdicts, out_dir)
        report_path = qt.write_report(verdicts, [], out_dir)

        json_verdicts = json.loads(verdict_path.read_text(encoding="utf-8"))
        # a propose-change verdict held by the WIP cap will not open a PR (3.6
        # downgrades it to `keep` at apply time), so it must be excluded here
        # the same way `compute_run_summary()` excludes it (M1).
        json_pull_requests_opened = sum(
            1
            for e in json_verdicts
            if e["verdict"] in ("fold-into-change", "propose-change")
            and not (e["verdict"] == "propose-change" and e["held_by_wip_cap"])
        )
        json_held_by_repo: dict = {}
        for e in json_verdicts:
            if e["held_by_wip_cap"] and e["repo"]:
                json_held_by_repo[e["repo"]] = json_held_by_repo.get(e["repo"], 0) + 1

        report_text = report_path.read_text(encoding="utf-8")
        self.assertIn(
            f"- pull_requests_opened: {json_pull_requests_opened}", report_text
        )
        self.assertEqual(json_pull_requests_opened, 1)
        for repo, count in json_held_by_repo.items():
            self.assertIn(f"- {repo}: {count}", report_text)
        self.assertEqual(json_held_by_repo, {str(repo_root): 1})

    def test_evaluate_json_output_includes_pull_requests_and_wip_cap(self):
        repo_root = self.base / "repo-cmd-evaluate"
        self._make_active_change(repo_root, "change-1")
        self._write_policy(repo_root, max_active_changes=1)
        self.write("a.md", repo=str(repo_root))

        def fake_evaluate_group(
            repo, briefs, *, agent="claude", cwd=None, repos_root=None
        ):
            ids = [p.stem for p in briefs]
            raw = json.dumps(
                {
                    "brief_id": ids[0],
                    "verdict": "propose-change",
                    "duplicate_of": None,
                    "target_repo": repo,
                    "proposed_change_name": "new-thing",
                    "evidence": "clearly belongs here",
                    "confidence": "high",
                }
            )
            return [
                {
                    "repo": repo,
                    "brief_ids": ids,
                    "raw_text": raw,
                    "candidates_by_brief": {bid: [] for bid in ids},
                    "known_repos_by_brief": {},
                }
            ]

        out_dir = self.base / "out-cmd-evaluate"
        buf = io.StringIO()
        with (
            mock.patch(
                "worktrail.workqueue.queue_triage.evaluate_group",
                side_effect=fake_evaluate_group,
            ),
            redirect_stdout(buf),
        ):
            exit_code = qt.main(["evaluate", "--out-dir", str(out_dir), "--json"])

        self.assertEqual(exit_code, 0)
        printed = json.loads(buf.getvalue())
        # this run's one brief is a propose-change held by the WIP cap: 3.6
        # downgrades it to `keep` at apply time, so it will not open a PR --
        # `pull_requests_opened` and `held_by_wip_cap` must not both claim it
        # (M1).
        self.assertEqual(printed["pull_requests_opened"], 0)
        self.assertEqual(printed["held_by_wip_cap"], {str(repo_root): 1})

        report_text = (out_dir / "report.md").read_text(encoding="utf-8")
        self.assertIn("- pull_requests_opened: 0", report_text)
        self.assertIn(f"- {repo_root}: 1", report_text)


class TestRankChangeCandidates(QueueTriageTestBase):
    """2.1's `rank_change_candidates()`: repo's active OpenSpec changes ranked
    against a brief's focus text by the duplicate-brief-detection focus-overlap
    coefficient, over each change's proposal summary + tasks.md task tokens.
    """

    def _make_change(
        self,
        repo_root: Path,
        change_id: str,
        *,
        why: str,
        tasks: list[tuple[bool, str]],
    ) -> None:
        change_dir = repo_root / "openspec" / "changes" / change_id
        change_dir.mkdir(parents=True, exist_ok=True)
        (change_dir / "proposal.md").write_text(
            f"# {change_id}\n\n## Why\n{why}\n", encoding="utf-8"
        )
        lines = [
            f"- [{'x' if checked else ' '}] {i + 1}.1 {text}"
            for i, (checked, text) in enumerate(tasks)
        ]
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n" + "\n".join(lines) + "\n", encoding="utf-8"
        )

    def test_strong_overlap_ranks_first_with_open_task_count(self):
        repo_root = self.base / "repo"
        self._make_change(
            repo_root,
            "widget-export-pipeline",
            why="Add a widget export pipeline for downstream reporting consumers.",
            tasks=[
                (False, "Implement widget export pipeline serializer"),
                (True, "Write export pipeline docs"),
            ],
        )
        self._make_change(
            repo_root,
            "finance-dashboard-export",
            why="Export pipeline reporting for the finance dashboards.",
            tasks=[(False, "Wire finance dashboards into the export pipeline")],
        )
        brief_path = self.queue / "brief.md"
        brief_path.write_text(
            "---\nstatus: queued\n---\n\n"
            "## Focus\n\nwidget export pipeline serializer downstream reporting\n",
            encoding="utf-8",
        )

        results = qt.rank_change_candidates(brief_path, str(repo_root), top_k=5)

        self.assertEqual(results[0]["id"], "widget-export-pipeline")
        self.assertEqual(results[0]["open_task_count"], 1)
        self.assertIn("widget export pipeline", results[0]["feature_summary"])
        self.assertGreater(results[0]["score"], results[1]["score"])
        self.assertEqual(results[1]["id"], "finance-dashboard-export")

    def test_below_floor_change_is_excluded_even_with_room_in_top_k(self):
        """A weak overlap is noise the evaluator must read past -- and a
        presented candidate is a legal `fold-into-change` target."""
        repo_root = self.base / "repo-floor"
        self._make_change(
            repo_root,
            "widget-export-pipeline",
            why="Add a widget export pipeline for downstream reporting consumers.",
            tasks=[(False, "Implement widget export pipeline serializer")],
        )
        self._make_change(
            repo_root,
            "unrelated-billing-cleanup",
            why="Clean up stale billing invoice reconciliation cron jobs.",
            tasks=[(False, "Remove stale billing invoice cron entries")],
        )
        brief_path = self.queue / "brief.md"
        brief_path.write_text(
            "---\nstatus: queued\n---\n\n"
            "## Focus\n\nwidget export pipeline serializer downstream reporting\n",
            encoding="utf-8",
        )

        results = qt.rank_change_candidates(brief_path, str(repo_root), top_k=5)

        # top_k=5 leaves room for the weak candidate; the floor excludes it
        self.assertEqual([r["id"] for r in results], ["widget-export-pipeline"])
        self.assertGreaterEqual(results[0]["score"], qt._MIN_CANDIDATE_SCORE)

    def test_change_exactly_at_the_floor_is_still_returned(self):
        """The floor is inclusive: a change scoring exactly
        `_MIN_CANDIDATE_SCORE` is kept, so the filter is `<` and not `<=`."""
        shared = "alpha bravo charlie delta echo foxtrot golf hotel india"  # 9
        change_only = (
            "juliett kilo lima mike november oscar papa quebec romeo sierra tango"  # 11
        )
        brief_only = (
            "uniform victor whiskey xray yankee zulu zero onex twox threex fourx"  # 11
        )
        # change tokens 9 + 11 == 20, brief tokens 9 + 11 == 20, overlap 9, so
        # |A n B| / min(|A|, |B|) == 9/20 == 0.45 == _MIN_CANDIDATE_SCORE exactly.
        repo_root = self.base / "repo-at-floor"
        self._make_change(
            repo_root,
            "at-the-floor-change",
            why=f"{shared} {change_only}",
            tasks=[(False, f"{shared} {change_only}")],
        )
        brief_path = self.queue / "brief.md"
        brief_path.write_text(
            f"---\nstatus: queued\n---\n\n## Focus\n\n{shared} {brief_only}\n",
            encoding="utf-8",
        )

        results = qt.rank_change_candidates(brief_path, str(repo_root), top_k=5)

        self.assertEqual([r["id"] for r in results], ["at-the-floor-change"])
        self.assertEqual(results[0]["score"], qt._MIN_CANDIDATE_SCORE)

    def test_all_changes_below_floor_returns_empty_list(self):
        """Same empty-list contract as a repo with no active changes at all."""
        repo_root = self.base / "repo-all-weak"
        for i in range(3):
            self._make_change(
                repo_root,
                f"unrelated-{i}",
                why="Clean up stale billing invoice reconciliation cron jobs.",
                tasks=[(False, "Remove stale billing invoice cron entries")],
            )
        brief_path = self.queue / "brief.md"
        brief_path.write_text(
            "---\nstatus: queued\n---\n\n"
            "## Focus\n\nwidget export pipeline serializer downstream reporting\n",
            encoding="utf-8",
        )

        self.assertEqual(
            qt.rank_change_candidates(brief_path, str(repo_root), top_k=5), []
        )

    def test_no_active_changes_returns_empty_list(self):
        repo_root = self.base / "repo-empty"
        (repo_root / "openspec" / "changes").mkdir(parents=True, exist_ok=True)
        brief_path = self.write("brief.md", body="## Focus\n\nanything at all\n")

        self.assertEqual(
            qt.rank_change_candidates(brief_path, str(repo_root), top_k=5), []
        )

    def test_null_repo_returns_empty_list(self):
        brief_path = self.write("brief.md", body="## Focus\n\nanything at all\n")

        self.assertEqual(qt.rank_change_candidates(brief_path, None, top_k=5), [])

    def test_top_k_truncates_result(self):
        repo_root = self.base / "repo-many"
        for i in range(3):
            self._make_change(
                repo_root,
                f"change-{i}",
                why="widget export pipeline serializer downstream reporting",
                tasks=[
                    (False, "widget export pipeline serializer downstream reporting")
                ],
            )
        brief_path = self.write(
            "brief.md",
            body="## Focus\n\nwidget export pipeline serializer downstream reporting\n",
            focus="widget export pipeline serializer downstream reporting",
        )

        results = qt.rank_change_candidates(brief_path, str(repo_root), top_k=2)

        self.assertEqual(len(results), 2)


class TestEscalationLimitsAndDue(QueueTriageTestBase):
    """4.1(b): `_escalation_limits()`/`escalation_due()`, design D5."""

    def test_defaults_apply_for_null_repo(self):
        self.assertEqual(qt._escalation_limits(None), (2, 14))
        self.assertEqual(qt._escalation_limits(qt.NO_REPO_KEY), (2, 14))

    def test_defaults_apply_when_repo_has_no_policy_file(self):
        repo = self.base / "unconfigured-repo"
        repo.mkdir()
        self.assertEqual(qt._escalation_limits(str(repo)), (2, 14))

    def test_policy_overrides_both_limits(self):
        repo = self.base / "configured-repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "triage_keep_limit: 5\ntriage_max_queue_age_days: 30\n", encoding="utf-8"
        )
        self.assertEqual(qt._escalation_limits(str(repo)), (5, 30))

    def test_due_by_keep_limit(self):
        path = self.write("a.md")
        for _ in range(2):
            qt._apply_keep(
                qt.Verdict(
                    brief_id="a", verdict="keep", duplicate_of=None, evidence="still ok"
                ),
                datetime.date.today().isoformat(),  # noqa: DTZ011
            )
        self.assertEqual(qt.consecutive_keep_count(path), 2)
        self.assertEqual(qt.escalation_due(path, None), "keep-limit")

    def test_due_by_queue_age(self):
        path = self.write("a.md")
        old = (datetime.date.today() - datetime.timedelta(days=30)).isoformat()  # noqa: DTZ011
        qt._set_fm_fields(path, {"created": old})
        self.assertEqual(qt.escalation_due(path, None), "queue-age")

    def test_neither_condition_met_returns_none(self):
        path = self.write("a.md")
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        qt._set_fm_fields(path, {"created": today})
        self.assertIsNone(qt.escalation_due(path, None))

    def test_keep_limit_checked_before_queue_age(self):
        repo = self.base / "repo-with-policy"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "triage_keep_limit: 1\ntriage_max_queue_age_days: 9999\n", encoding="utf-8"
        )
        path = self.write("a.md", repo=str(repo))
        qt._apply_keep(
            qt.Verdict(
                brief_id="a", verdict="keep", duplicate_of=None, evidence="still ok"
            ),
            datetime.date.today().isoformat(),  # noqa: DTZ011
        )
        self.assertEqual(qt.escalation_due(path, str(repo)), "keep-limit")


class TestWorkDirectlyAccepted(unittest.TestCase):
    """4.1(b): `_work_directly_accepted()`, design D6 -- either half alone accepts."""

    def test_true_on_regex_alone(self):
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="reproduces via pytest tests/foo.py -k bar",
        )
        self.assertTrue(qt._work_directly_accepted(v))

    def test_true_on_confirmed_premise_alone(self):
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="this brief describes a real problem",
            premise_check=[
                {"kind": "path", "needle": "src/x.py", "confirmed": True, "detail": ""}
            ],
        )
        self.assertTrue(qt._work_directly_accepted(v))

    def test_false_on_neither(self):
        v = qt.Verdict(
            brief_id="a",
            verdict="work-directly",
            duplicate_of=None,
            evidence="this brief describes a real problem",
            premise_check=[
                {"kind": "path", "needle": "src/x.py", "confirmed": False, "detail": ""}
            ],
        )
        self.assertFalse(qt._work_directly_accepted(v))


class TestKebabFromBriefId(unittest.TestCase):
    def test_strips_timestamp_prefix(self):
        self.assertEqual(
            qt._kebab_from_brief_id("20260901-120000-fix-the-widget-exporter"),
            "fix-the-widget-exporter",
        )

    def test_falls_back_to_slugify_for_non_kebab_remainder(self):
        result = qt._kebab_from_brief_id("not a valid brief id!!")
        self.assertTrue(qt._KEBAB_CASE_RE.fullmatch(result))


class TestEscalate(QueueTriageTestBase):
    """4.1(b): `escalate()`'s design D5 matrix."""

    def _keep(self, evidence="still relevant", premise_check=None):
        return qt.Verdict(
            brief_id="a",
            verdict="keep",
            duplicate_of=None,
            evidence=evidence,
            premise_check=premise_check or [],
        )

    def test_not_due_returns_verdict_unchanged(self):
        path = self.write("a.md")
        v = self._keep()
        self.assertIs(qt.escalate(v, path, None, []), v)

    def test_non_keep_verdict_never_rewritten_even_if_due(self):
        path = self.write("a.md")
        for _ in range(2):
            qt._apply_keep(self._keep(), datetime.date.today().isoformat())  # noqa: DTZ011
        v = qt.Verdict(
            brief_id="a", verdict="stale-close", duplicate_of=None, evidence="gone"
        )
        self.assertIs(qt.escalate(v, path, None, []), v)

    def test_null_repo_due_becomes_needs_decision(self):
        path = self.write("a.md")
        for _ in range(2):
            qt._apply_keep(self._keep(), datetime.date.today().isoformat())  # noqa: DTZ011
        result = qt.escalate(self._keep(), path, None, [])
        self.assertEqual(result.verdict, "needs-decision")
        self.assertEqual(result.question, qt.REPO_ASSIGNMENT_QUESTION)
        self.assertEqual(result.escalation, "keep-limit")

    def test_confirmed_premise_becomes_work_directly(self):
        repo = self.base / "repo"
        repo.mkdir()
        path = self.write("a.md", repo=str(repo))
        for _ in range(2):
            qt._apply_keep(self._keep(), datetime.date.today().isoformat())  # noqa: DTZ011
        v = self._keep(evidence="reproduces via pytest tests/foo.py -k bar")
        result = qt.escalate(v, path, str(repo), [])
        self.assertEqual(result.verdict, "work-directly")
        self.assertEqual(result.escalation, "keep-limit")

    def test_under_cap_becomes_propose_change_with_kebab_name(self):
        repo = self.base / "repo"
        repo.mkdir()
        brief_id = "20260901-120000-fix-the-widget-exporter"
        path = self.write(f"{brief_id}.md", repo=str(repo))
        for _ in range(2):
            qt._apply_keep(
                qt.Verdict(
                    brief_id=brief_id, verdict="keep", duplicate_of=None, evidence="ok"
                ),
                datetime.date.today().isoformat(),  # noqa: DTZ011
            )
        v = qt.Verdict(
            brief_id=brief_id,
            verdict="keep",
            duplicate_of=None,
            evidence="still relevant",
        )
        result = qt.escalate(v, path, str(repo), [])
        self.assertEqual(result.verdict, "propose-change")
        self.assertEqual(result.target_repo, str(repo))
        self.assertEqual(result.proposed_change_name, "fix-the-widget-exporter")

    def test_over_cap_with_candidates_folds_into_first_candidate(self):
        repo = self.base / "repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "max_active_changes: 1\n", encoding="utf-8"
        )
        change_dir = repo / "openspec" / "changes" / "existing-change"
        change_dir.mkdir(parents=True)
        (change_dir / "proposal.md").write_text("# existing-change\n", encoding="utf-8")
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 x\n", encoding="utf-8"
        )
        path = self.write("a.md", repo=str(repo))
        for _ in range(2):
            qt._apply_keep(self._keep(), datetime.date.today().isoformat())  # noqa: DTZ011
        result = qt.escalate(self._keep(), path, str(repo), ["widget-export-pipeline"])
        self.assertEqual(result.verdict, "fold-into-change")
        self.assertEqual(result.target_change, f"{repo}:change:widget-export-pipeline")

    def test_over_cap_without_candidates_becomes_needs_decision(self):
        repo = self.base / "repo"
        (repo / ".worktrail").mkdir(parents=True)
        (repo / ".worktrail" / "policy.yaml").write_text(
            "max_active_changes: 1\n", encoding="utf-8"
        )
        change_dir = repo / "openspec" / "changes" / "existing-change"
        change_dir.mkdir(parents=True)
        (change_dir / "proposal.md").write_text("# existing-change\n", encoding="utf-8")
        (change_dir / "tasks.md").write_text(
            "## 1. Tasks\n\n- [ ] 1.1 x\n", encoding="utf-8"
        )
        path = self.write("a.md", repo=str(repo))
        for _ in range(2):
            qt._apply_keep(self._keep(), datetime.date.today().isoformat())  # noqa: DTZ011
        result = qt.escalate(self._keep(), path, str(repo), [])
        self.assertEqual(result.verdict, "needs-decision")
        self.assertIsNotNone(result.question)
        self.assertNotEqual(result.question, qt.REPO_ASSIGNMENT_QUESTION)


class TestConsumeRepoDecision(QueueTriageTestBase):
    """4.1(c): `consume_repo_decision()`, design D8."""

    def test_no_awaiting_decision_link_returns_none(self):
        path = self.write("a.md")
        self.assertIsNone(qt.consume_repo_decision(path, str(self.base)))

    def test_answered_non_repo_question_returns_none(self):
        from worktrail.workqueue import decisions

        path = self.write("a.md")
        result = decisions.ask(
            "Should we do this at all?",
            background="unclear",
            why="ambiguous scope",
            context="checked and unsure",
            options=["Yes", "No"],
            brief="a",
            queue_base=self.base,
        )
        decisions.answer(result["id"], "Yes", queue_base=self.base)

        self.assertIsNone(qt.consume_repo_decision(path, str(self.base)))

    def test_open_decision_returns_none(self):
        from worktrail.workqueue import decisions

        path = self.write("a.md")
        decisions.ask(
            qt.REPO_ASSIGNMENT_QUESTION,
            background="ambiguous",
            why="cannot infer",
            context="checked, no clear match",
            options=["Option A", "Option B"],
            brief="a",
            queue_base=self.base,
        )

        self.assertIsNone(qt.consume_repo_decision(path, str(self.base)))

    def test_answered_repo_question_resolving_to_known_checkout_stamps_and_notes(self):
        from worktrail.workqueue import decisions

        target = self.base / "known-repo"
        target.mkdir()
        path = self.write("a.md")
        result = decisions.ask(
            qt.REPO_ASSIGNMENT_QUESTION,
            background="ambiguous",
            why="cannot infer",
            context="checked, no clear match",
            options=["Option A", "Option B"],
            brief="a",
            queue_base=self.base,
        )
        decisions.answer(result["id"], str(target), queue_base=self.base)

        outcome = qt.consume_repo_decision(path, str(self.base))

        self.assertIsNotNone(outcome)
        self.assertTrue(outcome["resolved"])
        self.assertEqual(outcome["repo"], str(target.resolve()))
        fm = qt.read_frontmatter(path)
        self.assertEqual(fm["repo"], str(target.resolve()))
        history = qt.triage_history(path)
        self.assertEqual(history[-1].verdict, "repo-inferred")
        self.assertEqual(decisions.decision_status(result["id"], self.base), "resolved")

    def test_unresolvable_answer_leaves_brief_and_decision_untouched(self):
        from worktrail.workqueue import decisions

        path = self.write("a.md")
        result = decisions.ask(
            qt.REPO_ASSIGNMENT_QUESTION,
            background="ambiguous",
            why="cannot infer",
            context="checked, no clear match",
            options=["Option A", "Option B"],
            brief="a",
            queue_base=self.base,
        )
        decisions.answer(result["id"], "no-such-repo-anywhere", queue_base=self.base)
        before = path.read_text(encoding="utf-8")

        outcome = qt.consume_repo_decision(path, str(self.base))

        self.assertIsNotNone(outcome)
        self.assertFalse(outcome["resolved"])
        self.assertEqual(path.read_text(encoding="utf-8"), before)
        self.assertEqual(decisions.decision_status(result["id"], self.base), "answered")


class TestGroupQueueByRepoPrePass(QueueTriageTestBase):
    """4.1(c): `group_queue_by_repo()`'s decision-consumption/inference pre-pass."""

    def test_null_repo_brief_is_inferred_and_grouped_and_noted(self):
        target = self.base / "worktrail"
        (target / ".git").mkdir(parents=True)
        path = self.write("a.md")
        qt._set_fm_fields(path, {"focus": "Repo: worktrail -- fix this"})

        groups, inferred, unresolvable = qt.group_queue_by_repo(str(self.base))

        self.assertEqual(unresolvable, [])
        self.assertEqual(len(inferred), 1)
        self.assertNotIn(qt.NO_REPO_KEY, groups)
        [(key, _paths)] = [(k, v) for k, v in groups.items() if path in v]
        self.assertEqual(key, str(target.resolve()))
        fm = qt.read_frontmatter(path)
        self.assertEqual(fm["repo"], str(target.resolve()))

    def test_already_inferred_brief_is_not_re_noted_on_second_call(self):
        target = self.base / "worktrail"
        (target / ".git").mkdir(parents=True)
        path = self.write("a.md")
        qt._set_fm_fields(path, {"focus": "Repo: worktrail -- fix this"})

        _groups, first_inferred, _ = qt.group_queue_by_repo(str(self.base))
        self.assertEqual(len(first_inferred), 1)

        _groups2, second_inferred, _ = qt.group_queue_by_repo(str(self.base))
        self.assertEqual(second_inferred, [])

    def test_ambiguous_focus_stays_in_no_repo_key(self):
        self.write("a.md", body="## Focus\n\nnothing repo-specific here\n")

        groups, inferred, unresolvable = qt.group_queue_by_repo(str(self.base))

        self.assertEqual(inferred, [])
        self.assertEqual(unresolvable, [])
        self.assertIn(qt.NO_REPO_KEY, groups)


class TestInventoryEscalation(QueueTriageTestBase):
    """4.1(c): `inventory()`'s escalation-due dedup bypass and `escalate_without_evaluator`."""

    def test_due_brief_bypasses_dedup_window(self):
        repo = self.base / "repo"
        repo.mkdir()
        path = self.write("a.md", repo=str(repo))
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        for _ in range(2):
            qt._apply_keep(
                qt.Verdict(
                    brief_id="a", verdict="keep", duplicate_of=None, evidence="ok"
                ),
                today,
            )

        groups, skipped, escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25, repos_root=str(self.base))
        )

        self.assertNotIn(path, skipped)
        self.assertIn(str(repo), groups)
        self.assertEqual(groups[str(repo)], [path])
        self.assertEqual(escalate_without_evaluator, [])

    def test_non_due_recently_triaged_brief_is_still_skipped(self):
        repo = self.base / "repo"
        repo.mkdir()
        path = self.write("a.md", repo=str(repo))
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        qt._apply_keep(
            qt.Verdict(brief_id="a", verdict="keep", duplicate_of=None, evidence="ok"),
            today,
        )

        groups, skipped, escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25, repos_root=str(self.base))
        )

        self.assertIn(path, skipped)
        self.assertEqual(groups, {})
        self.assertEqual(escalate_without_evaluator, [])

    def test_due_null_repo_brief_lands_in_escalate_without_evaluator(self):
        path = self.write("a.md")
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        for _ in range(2):
            qt._apply_keep(
                qt.Verdict(
                    brief_id="a", verdict="keep", duplicate_of=None, evidence="ok"
                ),
                today,
            )

        groups, _skipped, escalate_without_evaluator, _inferred, _unresolvable = (
            qt.inventory(within_days=25, repos_root=str(self.base))
        )

        self.assertNotIn(qt.NO_REPO_KEY, groups)
        self.assertEqual(escalate_without_evaluator, [path])


class TestParseVerdictsPremiseAndNoRepo(unittest.TestCase):
    """4.1(d): `parse_verdicts()`'s `premise_by_brief`/`no_repo` handling."""

    def test_premise_check_lands_on_verdict(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "stale-close",
                "duplicate_of": None,
                "evidence": "gone",
            }
        )
        premise = [{"kind": "path", "needle": "x", "confirmed": True, "detail": "d"}]
        [v] = qt.parse_verdicts(raw, ["a"], premise_by_brief={"a": premise})
        self.assertEqual(v.premise_check, premise)

    def test_no_repo_converts_clean_keep_to_needs_decision(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "keep",
                "duplicate_of": None,
                "evidence": "still relevant",
            }
        )
        [v] = qt.parse_verdicts(raw, ["a"], no_repo=True)
        self.assertEqual(v.verdict, "needs-decision")
        self.assertEqual(v.question, qt.REPO_ASSIGNMENT_QUESTION)

    def test_no_repo_converts_fallback_keep_to_needs_decision(self):
        [v] = qt.parse_verdicts(
            "the evaluator rambled with no JSON", ["a"], no_repo=True
        )
        self.assertEqual(v.verdict, "needs-decision")
        self.assertEqual(v.question, qt.REPO_ASSIGNMENT_QUESTION)

    def test_no_repo_converts_malformed_verdict_fallback_to_needs_decision(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "not-a-real-verdict-type",
                "duplicate_of": None,
                "evidence": "whatever",
            }
        )
        [v] = qt.parse_verdicts(raw, ["a"], no_repo=True)
        self.assertEqual(v.verdict, "needs-decision")
        self.assertEqual(v.question, qt.REPO_ASSIGNMENT_QUESTION)

    def test_no_repo_does_not_touch_stale_close(self):
        raw = json.dumps(
            {
                "brief_id": "a",
                "verdict": "stale-close",
                "duplicate_of": None,
                "evidence": "gone",
            }
        )
        [v] = qt.parse_verdicts(raw, ["a"], no_repo=True)
        self.assertEqual(v.verdict, "stale-close")


class TestEvaluateBriefs(QueueTriageTestBase):
    """4.1(d): `evaluate_briefs()` chains evaluate_group -> parse_verdicts ->
    apply_wip_cap_preview -> escalate (design D9)."""

    def test_chains_pipeline_and_applies_escalation(self):
        repo = self.base / "repo"
        repo.mkdir()
        path = self.write("a.md", repo=str(repo))
        today = datetime.date.today().isoformat()  # noqa: DTZ011
        for _ in range(2):
            qt._apply_keep(
                qt.Verdict(
                    brief_id="a", verdict="keep", duplicate_of=None, evidence="ok"
                ),
                today,
            )

        def fake_evaluate_group(
            repo, briefs, *, agent="claude", cwd=None, repos_root=None
        ):
            ids = [p.stem for p in briefs]
            raw = json.dumps(
                {
                    "brief_id": ids[0],
                    "verdict": "keep",
                    "duplicate_of": None,
                    "evidence": "still relevant",
                }
            )
            return [
                {
                    "repo": repo,
                    "brief_ids": ids,
                    "raw_text": raw,
                    "candidates_by_brief": {bid: [] for bid in ids},
                    "premise_by_brief": {bid: [] for bid in ids},
                }
            ]

        with mock.patch(
            "worktrail.workqueue.queue_triage.evaluate_group",
            side_effect=fake_evaluate_group,
        ):
            [v] = qt.evaluate_briefs(str(repo), [path], cwd=str(repo))

        self.assertEqual(v.verdict, "propose-change")
        self.assertEqual(v.escalation, "keep-limit")
        self.assertEqual(v.repo, str(repo))


class TestComputeRunSummaryAndReportEscalations(unittest.TestCase):
    """4.1(e): `compute_run_summary()`/`write_report()`'s escalation/repos-inferred fields."""

    def _verdicts(self):
        return [
            qt.Verdict(
                brief_id="a",
                verdict="work-directly",
                duplicate_of=None,
                evidence="reproduces via pytest tests/foo -k bar",
                escalation="keep-limit",
            ),
            qt.Verdict(
                brief_id="b",
                verdict="needs-decision",
                duplicate_of=None,
                evidence="due",
                question="repo over cap",
                escalation="queue-age",
            ),
            qt.Verdict(
                brief_id="c",
                verdict="stale-close",
                duplicate_of=None,
                evidence="gone",
            ),
        ]

    def test_compute_run_summary_escalations_and_repos_inferred(self):
        summary = qt.compute_run_summary(self._verdicts(), repos_inferred=3)
        self.assertEqual(
            summary["escalations"]["by_reason"],
            {"keep-limit": 1, "queue-age": 1},
        )
        self.assertEqual(
            summary["escalations"]["by_verdict"],
            {"work-directly": 1, "needs-decision": 1},
        )
        self.assertEqual(summary["repos_inferred"], 3)

    def test_write_report_includes_repos_inferred_and_escalations_sections(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_path = qt.write_report(self._verdicts(), [], tmp, repos_inferred=2)
            text = report_path.read_text(encoding="utf-8")
        self.assertIn("## Repos inferred", text)
        self.assertIn("repos_inferred: 2", text)
        self.assertIn("## Escalations", text)
        self.assertIn("keep-limit: 1", text)
        self.assertIn("queue-age: 1", text)


if __name__ == "__main__":
    unittest.main()
