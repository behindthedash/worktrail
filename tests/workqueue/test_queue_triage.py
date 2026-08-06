#!/usr/bin/env python3
"""Tests for queue_triage.py -- repo grouping and dedup-marker detection.

Run: python3 -m pytest tests/workqueue/test_queue_triage.py -q
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from worktrail.workqueue import queue_triage as qt


def _brief(focus: str, repo: str = None, body: str = "") -> str:
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

    def write(self, name: str, repo: str = None, body: str = "") -> Path:
        p = self.queue / name
        p.write_text(_brief(name, repo=repo, body=body), encoding="utf-8")
        return p


class TestGroupQueueByRepo(QueueTriageTestBase):
    def test_groups_by_repo_value(self):
        self.write("a.md", repo="behindthedash/worktrail")
        self.write("b.md", repo="behindthedash/worktrail")
        self.write("c.md", repo="behindthedash/devops")

        groups = qt.group_queue_by_repo()

        self.assertEqual(
            {k: sorted(p.name for p in v) for k, v in groups.items()},
            {
                "behindthedash/worktrail": ["a.md", "b.md"],
                "behindthedash/devops": ["c.md"],
            },
        )

    def test_missing_repo_field_collapses_to_none_key(self):
        self.write("a.md")  # no repo: field at all

        groups = qt.group_queue_by_repo()

        self.assertEqual(list(groups), [qt.NO_REPO_KEY])
        self.assertEqual([p.name for p in groups[qt.NO_REPO_KEY]], ["a.md"])

    def test_null_and_blank_repo_collapse_to_none_key(self):
        self.write("a.md", repo="null")
        self.write("b.md", repo='""')

        groups = qt.group_queue_by_repo()

        self.assertEqual(list(groups), [qt.NO_REPO_KEY])
        self.assertEqual(
            sorted(p.name for p in groups[qt.NO_REPO_KEY]), ["a.md", "b.md"]
        )

    def test_none_and_named_repo_briefs_collapse_into_same_none_bucket(self):
        self.write("a.md")
        self.write("b.md", repo="null")
        self.write("c.md", repo="behindthedash/worktrail")

        groups = qt.group_queue_by_repo()

        self.assertEqual(
            sorted(p.name for p in groups[qt.NO_REPO_KEY]), ["a.md", "b.md"]
        )
        self.assertEqual(
            [p.name for p in groups["behindthedash/worktrail"]], ["c.md"]
        )

    def test_empty_queue_dir_yields_no_groups(self):
        self.assertEqual(qt.group_queue_by_repo(), {})

    def test_missing_queue_dir_yields_no_groups(self):
        for f in self.queue.iterdir():
            f.unlink()
        self.queue.rmdir()
        self.assertEqual(qt.group_queue_by_repo(), {})

    def test_non_markdown_files_are_ignored(self):
        (self.queue / "notes.txt").write_text("not a brief", encoding="utf-8")
        self.write("a.md")

        groups = qt.group_queue_by_repo()

        self.assertEqual([p.name for p in groups[qt.NO_REPO_KEY]], ["a.md"])


class TestIsRecentlyTriaged(QueueTriageTestBase):
    def test_recent_triage_section_is_within_window(self):
        import datetime

        recent = datetime.date.today() - datetime.timedelta(days=1)
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

        recent = datetime.date.today() - datetime.timedelta(days=1)
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

        recent = datetime.date.today() - datetime.timedelta(days=1)
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

        today = datetime.date.today()
        boundary_date = today - datetime.timedelta(days=30)
        p = self.write(
            "a.md",
            body=f"## Triage {boundary_date.isoformat()}\n\nkeep\n",
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_one_day_past_boundary_is_stale(self):
        import datetime

        today = datetime.date.today()
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
            'reasoning text here\n'
            '{"brief_id": "b", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "still relevant", "confidence": "low"}\n'
            'more reasoning\n'
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

    def test_invalid_verdict_type_falls_back_to_keep_with_snippet_retained(self):
        snippet = (
            '{"brief_id": "a", "verdict": "not-a-real-verdict", "duplicate_of": null, '
            '"evidence": "some evidence", "confidence": "high"}'
        )
        raw = f"here is my answer: {snippet}"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, snippet)

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

    def test_second_candidate_used_when_first_is_malformed(self):
        good = (
            '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, '
            '"evidence": "second attempt is valid", "confidence": "medium"}'
        )
        bad = '{"brief_id": "a", "verdict": "keep", "duplicate_of": null, "evidence": ""}'
        raw = f"{bad}\n{good}"
        verdicts = qt.parse_verdicts(raw, ["a"])

        v = verdicts[0]
        self.assertEqual(v.verdict, "keep")
        self.assertEqual(v.evidence, "second attempt is valid")


if __name__ == "__main__":
    unittest.main()
