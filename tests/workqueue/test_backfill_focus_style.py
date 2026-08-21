#!/usr/bin/env python3
"""Tests for backfill_focus_style.py.

Covers the `build_preview` contract: which pre-#582 `focus:` scalar styles
get proposed for backfill, which are skipped and why, and that both
`queue/` and `picked/` are scanned. Also covers the span-splice rewrite
engine (`_locate_current_focus` + `_splice_focus`): for every pre-#582
style, the rewritten file's `focus:` becomes canonical, the re-parsed
value is byte-identical to the original, and every other line plus the
body is left untouched.

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


def _splice_in_place(path: Path):
    """Run the span-splice engine (`_locate_current_focus` + `_splice_focus`)
    against `path`'s *current* on-disk content and write the result back,
    mirroring the per-file rewrite `execute_apply` will perform. Returns the
    pre-rewrite parsed `focus` value."""
    content = path.read_text(encoding="utf-8")
    located = bf._locate_current_focus(content)
    assert located is not None, f"expected a focus: span in {path}"
    m, value, raw_yaml, start, end = located
    new_content = bf._splice_focus(content, m, raw_yaml, start, end, value)
    path.write_text(new_content, encoding="utf-8")
    return value


# Pre-#582 styles PyYAML's default (non-LiteralStr) resolver can produce for
# a `focus:` value, each paired with the line(s) it renders on disk and the
# exact string it parses to. Every fixture below also carries a deliberately
# non-canonical `created:` line (unquoted -- backfill_created_quoting.py's
# own defect shape) on at least one case, to prove a focus-only splice never
# touches an unrelated frontmatter field.
FOCUS_VALUE = "fix the bug"

PLAIN_SCALAR_FOCUS_LINES = "focus: fix the bug\n"
SINGLE_QUOTED_FOCUS_LINES = "focus: 'fix the bug'\n"
FOLDED_FOCUS_LINES = "focus: >-\n  Fix the bug\n\n  Add a regression test\n"
FOLDED_FOCUS_VALUE = "Fix the bug\nAdd a regression test"
LITERAL_TRAILING_WS_FOCUS_LINES = "focus: |-\n  fix the bug\n  \n"

# Real defect shape reused from BuildPreviewTestCase: a pre-#582 writer that
# didn't set allow_unicode=True renders any non-ASCII focus text as an
# escaped, folded double-quoted scalar.
DOUBLE_QUOTED_FOLDED_FOCUS_LINES = (
    "focus: \"Fix the \\u201Cbroken\\u201D link so every navigation menu item routes users\\\n"
    "  \\ to the correct destination page without any errors at all whatsoever today.\"\n"
)
DOUBLE_QUOTED_FOLDED_FOCUS_VALUE = (
    "Fix the “broken” link so every navigation menu item routes users "
    "to the correct destination page without any errors at all whatsoever today."
)


def _make_brief(brief_id: str, focus_lines: str, *, created_canonical: bool = True) -> str:
    created = (
        f"created: '2026-01-01T00:00:00-07:00'\n"
        if created_canonical
        else f"created: 2026-01-01T00:00:00-07:00\n"
    )
    return (
        "---\n"
        f"id: {brief_id}\n"
        f"{created}"
        f"{focus_lines}"
        "status: queued\n"
        "---\n\n"
        "## Discovery context\n\nsome notes that must survive untouched\n"
    )


class SpanSpliceExecuteTestCase(unittest.TestCase):
    """Exercises the span-splice rewrite engine directly against every
    pre-#582 style, per task 2.2 -- execute_apply's own safety-contract and
    CLI coverage live in the sibling tasks that build on top of this."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.queue_dir = self.tmp_path / "queue"
        self.queue_dir.mkdir(parents=True)
        os.environ["WORK_QUEUE_DIR"] = str(self.tmp_path)
        importlib.reload(q)
        importlib.reload(bf)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def _assert_spliced_correctly(
        self, brief_id: str, original_content: str, expected_value: str
    ):
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(original_content, encoding="utf-8")

        # Preview must actually propose this file -- otherwise the fixture
        # doesn't exercise the splice engine at all.
        preview = bf.build_preview(self.tmp_path)
        proposed_ids = {p["id"] for p in preview["proposals"]}
        self.assertIn(brief_id, proposed_ids)

        pre_rewrite_value = _splice_in_place(path)
        self.assertEqual(pre_rewrite_value, expected_value)

        new_content = path.read_text(encoding="utf-8")

        # focus: is now canonical -- re-preview finds nothing left to do.
        post_preview = bf.build_preview(self.tmp_path)
        self.assertNotIn(
            brief_id, {p["id"] for p in post_preview["proposals"]}
        )

        # The re-parsed focus value is byte-identical to the original.
        m = q._FM_RE.match(new_content)
        self.assertIsNotNone(m)
        reparsed = bf._find_focus_span(m.group(1))
        self.assertIsNotNone(reparsed)
        reparsed_value, _, _ = reparsed
        self.assertEqual(reparsed_value, expected_value)

        # Every line outside the focus: value span, and the full body, is
        # byte-for-byte unchanged.
        original_lines = original_content.split("\n")
        new_lines = new_content.split("\n")
        focus_line_prefixes = ("focus:",)

        def _non_focus_lines(lines):
            out = []
            skip = False
            for line in lines:
                if line.startswith(focus_line_prefixes):
                    skip = True
                    continue
                if skip:
                    # Continuation lines of a multi-line focus: value are
                    # indented under it; stop skipping once we hit a
                    # top-level (non-indented) line again.
                    if line.startswith((" ", "\t")) or line == "":
                        continue
                    skip = False
                out.append(line)
            return out

        self.assertEqual(
            _non_focus_lines(original_lines), _non_focus_lines(new_lines)
        )

    def test_splices_plain_scalar_focus(self):
        content = _make_brief("20260201-000001-a", PLAIN_SCALAR_FOCUS_LINES)
        self._assert_spliced_correctly("20260201-000001-a", content, FOCUS_VALUE)

    def test_splices_single_quoted_focus(self):
        content = _make_brief("20260201-000002-b", SINGLE_QUOTED_FOCUS_LINES)
        self._assert_spliced_correctly("20260201-000002-b", content, FOCUS_VALUE)

    def test_splices_double_quoted_folded_focus(self):
        content = _make_brief(
            "20260201-000003-c", DOUBLE_QUOTED_FOLDED_FOCUS_LINES
        )
        self._assert_spliced_correctly(
            "20260201-000003-c", content, DOUBLE_QUOTED_FOLDED_FOCUS_VALUE
        )

    def test_splices_folded_focus(self):
        content = _make_brief("20260201-000004-d", FOLDED_FOCUS_LINES)
        self._assert_spliced_correctly(
            "20260201-000004-d", content, FOLDED_FOCUS_VALUE
        )

    def test_splices_literal_style_differing_only_in_trailing_whitespace(self):
        # Already `|-` on disk -- differs from canonical only by a trailing
        # whitespace-only blank line inside the block, which the strip (`-`)
        # chomping indicator discards from the parsed value but which
        # `build_preview`'s newline-only rstrip comparison still flags.
        content = _make_brief(
            "20260201-000005-e", LITERAL_TRAILING_WS_FOCUS_LINES
        )
        self._assert_spliced_correctly("20260201-000005-e", content, FOCUS_VALUE)

    def test_leaves_non_canonical_created_untouched(self):
        # A focus:-only splice must never touch a sibling field's own
        # non-canonical style, even one backfill_created_quoting.py exists
        # specifically to fix.
        brief_id = "20260201-000006-f"
        content = _make_brief(
            brief_id, PLAIN_SCALAR_FOCUS_LINES, created_canonical=False
        )
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")

        _splice_in_place(path)
        new_content = path.read_text(encoding="utf-8")

        self.assertIn("created: 2026-01-01T00:00:00-07:00\n", new_content)
        self.assertIn(
            "## Discovery context\n\nsome notes that must survive untouched\n",
            new_content,
        )


if __name__ == "__main__":
    unittest.main()
