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
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from worktrail.workqueue import backfill_focus_style as bf
from worktrail.workqueue import work_queue as q


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
    'focus: "Fix the \\u201Csmart quotes\\u201D rendering bug so briefs with curly punctuation\\\n'
    "  \\ display correctly everywhere across every single client that renders them without\\\n"
    '  \\ breaking."\n'
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

# Real defect shape: a plain (unquoted) scalar containing a bare ` #NNN`
# reference (e.g. a PR number) is silently truncated by PyYAML's own
# scanner, which treats ` #` as a comment start.
PLAIN_UNQUOTED_WITH_HASH_BRIEF = (
    "---\n"
    "id: 20260101-000005-e\n"
    "created: 2026-01-01T00:00:05-07:00\n"
    "focus: fix the thing (PR #171) and clean up\n"
    "status: queued\n"
    "---\n\n"
)
PLAIN_UNQUOTED_WITH_HASH_VALUE = "fix the thing (PR #171) and clean up"


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

    def test_proposes_plain_unquoted_focus_with_bare_hash(self):
        # A bare ` #NNN` inside a plain scalar is a PyYAML comment-start
        # trap, not an intentional comment -- the full line must be
        # recovered, not silently truncated before the `#`.
        _write_raw(
            self.queue_dir,
            "20260101-000005-e.md",
            PLAIN_UNQUOTED_WITH_HASH_BRIEF,
        )
        result = bf.build_preview(self.tmp_path)
        self.assertEqual(len(result["proposals"]), 1)
        proposal = result["proposals"][0]
        self.assertEqual(proposal["id"], "20260101-000005-e")
        self.assertEqual(proposal["focus_value"], PLAIN_UNQUOTED_WITH_HASH_VALUE)
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
PLAIN_SCALAR_WITH_HASH_FOCUS_LINES = "focus: fix the thing (PR #171) and clean up\n"
PLAIN_SCALAR_WITH_HASH_FOCUS_VALUE = "fix the thing (PR #171) and clean up"
SINGLE_QUOTED_FOCUS_LINES = "focus: 'fix the bug'\n"
FOLDED_FOCUS_LINES = "focus: >-\n  Fix the bug\n\n  Add a regression test\n"
FOLDED_FOCUS_VALUE = "Fix the bug\nAdd a regression test"
LITERAL_TRAILING_WS_FOCUS_LINES = "focus: |-\n  fix the bug\n  \n"

# Real defect shape reused from BuildPreviewTestCase: a pre-#582 writer that
# didn't set allow_unicode=True renders any non-ASCII focus text as an
# escaped, folded double-quoted scalar.
DOUBLE_QUOTED_FOLDED_FOCUS_LINES = (
    'focus: "Fix the \\u201Cbroken\\u201D link so every navigation menu item routes users\\\n'
    '  \\ to the correct destination page without any errors at all whatsoever today."\n'
)
DOUBLE_QUOTED_FOLDED_FOCUS_VALUE = (
    "Fix the “broken” link so every navigation menu item routes users "
    "to the correct destination page without any errors at all whatsoever today."
)


def _make_brief(
    brief_id: str, focus_lines: str, *, created_canonical: bool = True
) -> str:
    created = (
        "created: '2026-01-01T00:00:00-07:00'\n"
        if created_canonical
        else "created: 2026-01-01T00:00:00-07:00\n"
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
        self.assertNotIn(brief_id, {p["id"] for p in post_preview["proposals"]})

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

        self.assertEqual(_non_focus_lines(original_lines), _non_focus_lines(new_lines))

    def test_splices_plain_scalar_focus(self):
        content = _make_brief("20260201-000001-a", PLAIN_SCALAR_FOCUS_LINES)
        self._assert_spliced_correctly("20260201-000001-a", content, FOCUS_VALUE)

    def test_splices_plain_scalar_focus_with_bare_hash(self):
        content = _make_brief("20260201-000007-g", PLAIN_SCALAR_WITH_HASH_FOCUS_LINES)
        self._assert_spliced_correctly(
            "20260201-000007-g", content, PLAIN_SCALAR_WITH_HASH_FOCUS_VALUE
        )

    def test_splices_single_quoted_focus(self):
        content = _make_brief("20260201-000002-b", SINGLE_QUOTED_FOCUS_LINES)
        self._assert_spliced_correctly("20260201-000002-b", content, FOCUS_VALUE)

    def test_splices_double_quoted_folded_focus(self):
        content = _make_brief("20260201-000003-c", DOUBLE_QUOTED_FOLDED_FOCUS_LINES)
        self._assert_spliced_correctly(
            "20260201-000003-c", content, DOUBLE_QUOTED_FOLDED_FOCUS_VALUE
        )

    def test_splices_folded_focus(self):
        content = _make_brief("20260201-000004-d", FOLDED_FOCUS_LINES)
        self._assert_spliced_correctly("20260201-000004-d", content, FOLDED_FOCUS_VALUE)

    def test_splices_literal_style_differing_only_in_trailing_whitespace(self):
        # Already `|-` on disk -- differs from canonical only by a trailing
        # whitespace-only blank line inside the block, which the strip (`-`)
        # chomping indicator discards from the parsed value but which
        # `build_preview`'s newline-only rstrip comparison still flags.
        content = _make_brief("20260201-000005-e", LITERAL_TRAILING_WS_FOCUS_LINES)
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


class ExecuteApplyTestCase(unittest.TestCase):
    """Exercises `execute_apply`'s safety contract: `decline` never writes,
    a stale/re-run preview never re-clobbers or double-splices, a file
    that's gone or changed since preview is skipped rather than
    overwritten, and a forced post-write validation failure rolls the
    write back to the original content -- per task 2.3."""

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

    def _preview_for(self, path: Path) -> dict[str, Any]:
        result = bf.build_preview(self.tmp_path)
        proposal = next(p for p in result["proposals"] if p["path"] == str(path))
        return {"proposals": [proposal], "skipped": []}

    def test_decline_writes_nothing(self):
        brief_id = "20260301-000001-a"
        content = _make_brief(brief_id, PLAIN_SCALAR_FOCUS_LINES)
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")
        preview = self._preview_for(path)

        result = bf.execute_apply(preview, self.tmp_path, confirm=False)

        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(result["skipped"][0]["reason"], "declined")
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_confirm_is_idempotent_does_not_reclobber(self):
        brief_id = "20260301-000002-b"
        content = _make_brief(brief_id, PLAIN_SCALAR_FOCUS_LINES)
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")
        preview = self._preview_for(path)

        first = bf.execute_apply(preview, self.tmp_path, confirm=True)
        self.assertEqual(first["stamped"], [brief_id])
        self.assertEqual(first["skipped"], [])
        rewritten = path.read_text(encoding="utf-8")

        # Re-run the SAME (now-stale) preview against the already-fixed
        # file -- must be a no-op, not a double-splice.
        second = bf.execute_apply(preview, self.tmp_path, confirm=True)

        self.assertEqual(second["stamped"], [])
        self.assertEqual(len(second["skipped"]), 1)
        self.assertIn(
            "focus: span changed since preview", second["skipped"][0]["reason"]
        )
        self.assertEqual(path.read_text(encoding="utf-8"), rewritten)

    def test_confirm_skips_focus_changed_since_preview(self):
        brief_id = "20260301-000003-c"
        content = _make_brief(brief_id, PLAIN_SCALAR_FOCUS_LINES)
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")
        preview = self._preview_for(path)

        # Someone else rewrites the focus: value after preview was taken,
        # before execute --confirm runs.
        mutated = content.replace(
            "focus: fix the bug", "focus: something else entirely"
        )
        path.write_text(mutated, encoding="utf-8")

        result = bf.execute_apply(preview, self.tmp_path, confirm=True)

        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn(
            "focus: span changed since preview", result["skipped"][0]["reason"]
        )
        self.assertEqual(path.read_text(encoding="utf-8"), mutated)

    def test_confirm_skips_file_removed_since_preview(self):
        brief_id = "20260301-000004-d"
        content = _make_brief(brief_id, PLAIN_SCALAR_FOCUS_LINES)
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")
        preview = self._preview_for(path)
        path.unlink()

        result = bf.execute_apply(preview, self.tmp_path, confirm=True)

        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("no longer present", result["skipped"][0]["reason"])

    def test_confirm_rolls_back_on_forced_validation_failure(self):
        brief_id = "20260301-000005-e"
        content = _make_brief(brief_id, PLAIN_SCALAR_FOCUS_LINES)
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")
        preview = self._preview_for(path)

        with mock.patch.object(
            bf, "validate_brief", return_value=(False, "forced failure")
        ):
            result = bf.execute_apply(preview, self.tmp_path, confirm=True)

        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("rolled back", result["skipped"][0]["reason"])
        # Content restored exactly to its pre-write state.
        self.assertEqual(path.read_text(encoding="utf-8"), content)


class MainCliTestCase(unittest.TestCase):
    """CLI coverage per task 2.4: `execute` must accept the preview payload
    via stdin, not only `--preview`, since a full-corpus preview routinely
    exceeds the shell argv size limit."""

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

    def test_execute_reads_preview_from_stdin_when_flag_omitted(self):
        brief_id = "20260301-000006-f"
        content = _make_brief(brief_id, PLAIN_SCALAR_FOCUS_LINES)
        path = self.queue_dir / f"{brief_id}.md"
        path.write_text(content, encoding="utf-8")

        preview = bf.build_preview(self.tmp_path)
        preview_json = json.dumps(preview)

        with mock.patch("sys.stdin", io.StringIO(preview_json)):
            rc = bf.main(["execute", "--queue-dir", str(self.tmp_path), "--confirm"])

        self.assertEqual(rc, 0)
        rewritten = path.read_text(encoding="utf-8")
        self.assertIn(f"focus: |-\n  {FOCUS_VALUE}\n", rewritten)
        post_preview = bf.build_preview(self.tmp_path)
        self.assertEqual(post_preview["proposals"], [])


if __name__ == "__main__":
    unittest.main()
