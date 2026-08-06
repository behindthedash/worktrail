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
        p = self.write(
            "a.md",
            body="## Triage 2026-08-01\n\nkeep\n",
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
        p = self.write(
            "a.md",
            body=(
                "## Triage 2020-01-01\n\nstale\n\n"
                "## Triage 2026-08-01\n\nrecent\n"
            ),
        )
        self.assertTrue(qt.is_recently_triaged(p, within_days=30))

    def test_unparsable_triage_date_is_ignored_not_raised(self):
        p = self.write("a.md", body="## Triage not-a-date\n\nkeep\n")
        self.assertFalse(qt.is_recently_triaged(p, within_days=30))

    def test_unparsable_date_does_not_shadow_a_valid_one(self):
        p = self.write(
            "a.md",
            body=(
                "## Triage not-a-date\n\nkeep\n\n"
                "## Triage 2026-08-01\n\nkeep\n"
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


if __name__ == "__main__":
    unittest.main()
