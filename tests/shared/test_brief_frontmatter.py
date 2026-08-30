#!/usr/bin/env python3
"""Tests for brief_frontmatter.py -- the shared frontmatter reader/validator.

Run: python3 -m pytest tests/shared/test_brief_frontmatter.py -q
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from worktrail.shared import brief_frontmatter as bf


class SplitFrontmatterRegression(unittest.TestCase):
    """The lenient reader's existing behavior must survive the
    `_find_frontmatter_block` refactor unchanged."""

    def test_well_formed(self):
        fm, body = bf.split_frontmatter(
            "---\nid: a\nstatus: queued\n---\n\nbody text\n"
        )
        self.assertEqual(fm, {"id": "a", "status": "queued"})
        self.assertEqual(body, "\nbody text\n")

    def test_no_fence_returns_empty_and_full_content(self):
        content = "no frontmatter here\n"
        fm, body = bf.split_frontmatter(content)
        self.assertEqual(fm, {})
        self.assertEqual(body, content)

    def test_unclosed_fence_returns_empty_and_full_content(self):
        content = "---\nid: a\nstatus: queued\nno closing fence\n"
        fm, body = bf.split_frontmatter(content)
        self.assertEqual(fm, {})
        self.assertEqual(body, content)

    def test_invalid_yaml_degrades_to_empty(self):
        content = 'focus: "unterminated quote\n---\nbody\n'
        content = "---\n" + content
        fm, _ = bf.split_frontmatter(content)
        self.assertEqual(fm, {})

    def test_bom_stripped(self):
        fm, _ = bf.split_frontmatter("﻿---\nid: a\nstatus: queued\n---\nbody\n")
        self.assertEqual(fm, {"id": "a", "status": "queued"})

    def test_bom_with_no_fence_still_strips_bom_from_body(self):
        fm, body = bf.split_frontmatter("﻿no frontmatter\n")
        self.assertEqual(fm, {})
        self.assertEqual(body, "no frontmatter\n")

    def test_non_dict_yaml_degrades_to_empty(self):
        fm, _ = bf.split_frontmatter("---\n- a\n- b\n---\nbody\n")
        self.assertEqual(fm, {})


class ReadFrontmatter(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(bf.read_frontmatter(Path("/no/such/file.md")), {})


class ValidateBriefText(unittest.TestCase):
    def test_well_formed_passes(self):
        ok, err = bf.validate_brief_text("---\nid: a\nstatus: queued\n---\n\nbody\n")
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_missing_fence_fails(self):
        ok, err = bf.validate_brief_text("no frontmatter\n")
        self.assertFalse(ok)
        self.assertIn("frontmatter", err)

    def test_unclosed_fence_fails(self):
        ok, err = bf.validate_brief_text(
            "---\nid: a\nstatus: queued\nno closing fence\n"
        )
        self.assertFalse(ok)
        self.assertIn("frontmatter", err)

    def test_invalid_yaml_fails_with_reason(self):
        content = '---\nfocus: "unterminated\nstatus: queued\n---\nbody\n'
        ok, err = bf.validate_brief_text(content)
        self.assertFalse(ok)
        self.assertIn("invalid YAML", err)

    def test_non_mapping_yaml_fails(self):
        ok, err = bf.validate_brief_text("---\n- a\n- b\n---\nbody\n")
        self.assertFalse(ok)
        self.assertIn("mapping", err)

    def test_empty_required_field_fails(self):
        ok, err = bf.validate_brief_text('---\nid: a\nstatus: ""\n---\nbody\n')
        self.assertFalse(ok)
        self.assertIn("status", err)

    def test_missing_required_field_fails(self):
        ok, err = bf.validate_brief_text("---\nid: a\n---\nbody\n")
        self.assertFalse(ok)
        self.assertIn("status", err)

    def test_default_required_does_not_demand_id_or_focus(self):
        """Real historical briefs omit frontmatter `id` (filename/stem is
        the canonical identifier) and some omit `focus` (body-only). The
        default `required` must not treat either as a hard failure."""
        ok, err = bf.validate_brief_text(
            "---\nstatus: queued\n---\n\n# Focus\n\nbody\n"
        )
        self.assertTrue(ok, msg=err)

    def test_custom_required_can_demand_focus(self):
        ok, err = bf.validate_brief_text(
            '---\nid: a\nstatus: queued\nfocus: ""\n---\nbody\n',
            required=("id", "status", "focus"),
        )
        self.assertFalse(ok)
        self.assertIn("focus", err)

    def test_whitespace_only_field_fails(self):
        ok, _err = bf.validate_brief_text('---\nid: a\nstatus: "   "\n---\nbody\n')
        self.assertFalse(ok)

    def test_non_string_field_fails(self):
        ok, err = bf.validate_brief_text("---\nid: a\nstatus: 123\n---\nbody\n")
        self.assertFalse(ok)
        self.assertIn("status", err)


class ValidateBrief(unittest.TestCase):
    def test_reads_and_validates_real_file(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "brief.md"
            p.write_text("---\nid: a\nstatus: queued\n---\nbody\n", encoding="utf-8")
            ok, err = bf.validate_brief(p)
            self.assertTrue(ok, msg=err)

    def test_missing_file_fails_with_reason(self):
        ok, err = bf.validate_brief(Path("/no/such/file.md"))
        self.assertFalse(ok)
        self.assertIn("cannot read", err)


class SerializeFrontmatter(unittest.TestCase):
    def test_focus_renders_as_literal_block(self):
        text = bf.serialize_frontmatter(
            {"id": "a", "focus": "fix the thing", "status": "queued"}
        )
        self.assertIn("focus: |-\n  fix the thing\n", text)

    def test_focus_with_colon_and_hash_needs_no_quoting(self):
        # A plain scalar would be forced into a quoted style by a colon+space
        # or space+# in the value; the literal block needs neither.
        text = bf.serialize_frontmatter({"focus": "fix: the bug #123 (P1)"})
        self.assertIn("focus: |-\n", text)
        self.assertNotIn('"', text)

    def test_embedded_newline_stays_a_single_block_no_folding(self):
        focus = "first line\nsecond line, " + ("padding " * 20).strip()
        text = bf.serialize_frontmatter({"focus": focus})
        self.assertNotIn("\\\n", text)  # no double-quoted fold continuation
        self.assertEqual(yaml.safe_load(text)["focus"], focus)

    def test_trailing_whitespace_is_stripped(self):
        text = bf.serialize_frontmatter({"focus": "  padded on both sides   "})
        self.assertEqual(yaml.safe_load(text)["focus"], "padded on both sides")

    def test_non_literal_keys_use_normal_pyyaml_styling(self):
        text = bf.serialize_frontmatter({"id": "a", "status": "queued", "focus": "x"})
        self.assertIn("id: a\n", text)
        self.assertIn("status: queued\n", text)

    def test_created_timestamp_is_quoted(self):
        text = bf.serialize_frontmatter(
            {"created": "2026-08-20T09:00:00-07:00", "focus": "x"}
        )
        self.assertIn("created: '2026-08-20T09:00:00-07:00'\n", text)

    def test_unicode_round_trips(self):
        text = bf.serialize_frontmatter({"focus": "an em dash — right here"})
        self.assertEqual(yaml.safe_load(text)["focus"], "an em dash — right here")
        self.assertNotIn("\\u2014", text)  # allow_unicode: literal char, not an escape


class IsCanonicalStyle(unittest.TestCase):
    def test_serialize_frontmatter_output_is_canonical(self):
        text = bf.serialize_frontmatter(
            {"id": "a", "focus": "fix the thing", "status": "queued"}
        )
        self.assertTrue(bf.is_canonical_style("---\n" + text + "---\n\nbody\n"))

    def test_double_quoted_folded_focus_is_not_canonical(self):
        # The exact non-canonical shape from the cited live example: a
        # double-quoted, ~80-column-folded focus scalar.
        content = (
            "---\n"
            'focus: "first line continues\\\n'
            '  \\ onto a folded second line"\n'
            "status: queued\n"
            "---\n\nbody\n"
        )
        self.assertFalse(bf.is_canonical_style(content))

    def test_unparseable_yaml_is_not_canonical(self):
        self.assertFalse(
            bf.is_canonical_style("---\nfocus: [unterminated\n---\n\nbody\n")
        )

    def test_no_frontmatter_block_is_not_canonical(self):
        self.assertFalse(bf.is_canonical_style("no frontmatter here\n"))


if __name__ == "__main__":
    unittest.main()
