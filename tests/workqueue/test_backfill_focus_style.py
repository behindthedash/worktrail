#!/usr/bin/env python3
"""Tests for backfill_focus_style.py.

Covers the `build_preview` contract: which pre-#582 `focus:` scalar styles
get proposed for backfill, which are skipped and why, and that both
`queue/` and `picked/` are scanned.

Run:
    python3 -m pytest tests/workqueue/test_backfill_focus_style.py -q
"""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path

from worktrail.workqueue import work_queue as q
from worktrail.workqueue import backfill_focus_style as bf


def _write_raw(dirpath: Path, filename: str, content: str) -> Path:
    path = dirpath / filename
    path.write_text(content, encoding="utf-8")
    return path


# Real defect shape: a pre-#582 writer that didn't set allow_unicode=True
# renders any non-ASCII focus text as an escaped, ~80-column-folded
# double-quoted scalar instead of the canonical `|-` literal block.
DOUBLE_QUOTED_FOLDED_BRIEF = (
    "---\n"
    "id: 20260101-000001-a\n"
    "created: 2026-01-01T00:00:01-07:00\n"
    "focus: \"Fix the \\u201Csmart quotes\\u201D rendering bug so briefs with curly punctuation\\\n"
    "  \\ display correctly everywhere across every single client that renders them without\\\n"
    "  \\ breaking.\"\n"
    "status: queued\n"
    "---\n\n"
    "## Discovery context\n\nsome notes\n"
)

PLAIN_UNQUOTED_BRIEF = (
    "---\n"
    "id: 20260101-000002-b\n"
    "created: 2026-01-01T00:00:02-07:00\n"
    "focus: fix the thing\n"
    "status: queued\n"
    "---\n\n"
)

CANONICAL_LITERAL_BRIEF = (
    "---\n"
    "id: 20260101-000003-c\n"
    "created: 2026-01-01T00:00:03-07:00\n"
    "focus: |-\n"
    "  already canonical\n"
    "status: queued\n"
    "---\n\n"
)

NO_FOCUS_KEY_BRIEF = (
    "---\n"
    "id: 20260101-000004-d\n"
    "created: 2026-01-01T00:00:04-07:00\n"
    "status: queued\n"
    "---\n\n"
    "## Focus\n\nno frontmatter focus, only a body section\n"
)


class BuildPreviewTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.queue_dir = self.tmp_path / "queue"
        self.picked_dir = self.tmp_path / "picked"
        self.queue_dir.mkdir(parents=True)
        self.picked_dir.mkdir(parents=True)
        os.environ["WORK_QUEUE_DIR"] = str(self.tmp_path)
        importlib.reload(q)
        importlib.reload(bf)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def test_proposes_double_quoted_folded_focus(self):
        _write_raw(self.queue_dir, "20260101-000001-a.md", DOUBLE_QUOTED_FOLDED_BRIEF)
        result = bf.build_preview(self.tmp_path)
        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["id"], "20260101-000001-a")
        self.assertEqual(
            proposal["focus_value"],
            "Fix the “smart quotes” rendering bug so briefs with curly "
            "punctuation display correctly everywhere across every single client "
            "that renders them without breaking.",
        )
        self.assertEqual(result["skipped"], [])

    def test_proposes_plain_unquoted_focus(self):
        _write_raw(self.queue_dir, "20260101-000002-b.md", PLAIN_UNQUOTED_BRIEF)
        result = bf.build_preview(self.tmp_path)
        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["id"], "20260101-000002-b")
        self.assertEqual(proposal["focus_value"], "fix the thing")
        self.assertEqual(result["skipped"], [])

    def test_skips_already_canonical_literal_focus(self):
        _write_raw(self.queue_dir, "20260101-000003-c.md", CANONICAL_LITERAL_BRIEF)
        result = bf.build_preview(self.tmp_path)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["skipped"], [])

    def test_skips_brief_with_no_focus_frontmatter_key(self):
        _write_raw(self.queue_dir, "20260101-000004-d.md", NO_FOCUS_KEY_BRIEF)
        result = bf.build_preview(self.tmp_path)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["id"], "20260101-000004-d")
        self.assertIn("no focus:", result["skipped"][0]["reason"])

    def test_scans_both_queue_and_picked(self):
        _write_raw(self.queue_dir, "20260101-000002-b.md", PLAIN_UNQUOTED_BRIEF)
        picked_content = PLAIN_UNQUOTED_BRIEF.replace("000002-b", "000005-e")
        _write_raw(self.picked_dir, "20260101-000005-e.md", picked_content)
        result = bf.build_preview(self.tmp_path)
        ids = {p["id"] for p in result["proposals"]}
        self.assertEqual(ids, {"20260101-000002-b", "20260101-000005-e"})


if __name__ == "__main__":
    unittest.main()
