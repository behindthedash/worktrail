#!/usr/bin/env python3
"""Tests for backfill_created_quoting.py (handoff 20260820-073044).

Covers the preview/execute contract: which briefs get proposed (unquoted
`created:` only), and that execute rewrites only the `created:` line while
leaving every other field, key order, and the body untouched.

Run:
    python3 -m pytest tests/workqueue/test_backfill_created_quoting.py -q
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

from worktrail.workqueue import backfill_created_quoting as bc
from worktrail.workqueue import work_queue as q


def _write_raw(dirpath: Path, filename: str, content: str) -> Path:
    path = dirpath / filename
    path.write_text(content, encoding="utf-8")
    return path


UNQUOTED_BRIEF = (
    "---\n"
    "id: 20260101-000001-a\n"
    "created: 2026-01-01T00:00:01-07:00\n"
    "focus: fix the thing\n"
    "status: queued\n"
    "---\n\n"
    "## Discovery context\n\nsome notes\n"
)

QUOTED_BRIEF = (
    "---\n"
    "id: 20260101-000002-b\n"
    "created: '2026-01-01T00:00:02-07:00'\n"
    "focus: already canonical\n"
    "status: queued\n"
    "---\n\n"
)

NO_CREATED_BRIEF = (
    "---\nid: 20260101-000003-c\nfocus: no created field\nstatus: queued\n---\n\n"
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
        importlib.reload(bc)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def test_proposes_unquoted_created(self):
        _write_raw(self.queue_dir, "20260101-000001-a.md", UNQUOTED_BRIEF)
        result = bc.build_preview(self.tmp_path)
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(result["proposals"][0]["id"], "20260101-000001-a")
        self.assertEqual(
            result["proposals"][0]["created_raw"], "2026-01-01T00:00:01-07:00"
        )
        self.assertEqual(result["skipped"], [])

    def test_skips_already_quoted_created(self):
        _write_raw(self.queue_dir, "20260101-000002-b.md", QUOTED_BRIEF)
        result = bc.build_preview(self.tmp_path)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["skipped"], [])

    def test_skips_missing_created_line(self):
        _write_raw(self.queue_dir, "20260101-000003-c.md", NO_CREATED_BRIEF)
        result = bc.build_preview(self.tmp_path)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("no created:", result["skipped"][0]["reason"])

    def test_scans_both_queue_and_picked(self):
        _write_raw(self.queue_dir, "20260101-000001-a.md", UNQUOTED_BRIEF)
        picked_content = UNQUOTED_BRIEF.replace("000001-a", "000004-d")
        _write_raw(self.picked_dir, "20260101-000004-d.md", picked_content)
        result = bc.build_preview(self.tmp_path)
        ids = {p["id"] for p in result["proposals"]}
        self.assertEqual(ids, {"20260101-000001-a", "20260101-000004-d"})


class ExecuteApplyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.queue_dir = self.tmp_path / "queue"
        self.queue_dir.mkdir(parents=True)
        os.environ["WORK_QUEUE_DIR"] = str(self.tmp_path)
        importlib.reload(q)
        importlib.reload(bc)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def _preview_for(self, path: Path, created_raw: str) -> dict[str, Any]:
        return {
            "proposals": [
                {"id": path.stem, "path": str(path), "created_raw": created_raw}
            ],
            "skipped": [],
        }

    def test_decline_writes_nothing(self):
        path = _write_raw(self.queue_dir, "20260101-000001-a.md", UNQUOTED_BRIEF)
        preview = self._preview_for(path, "2026-01-01T00:00:01-07:00")
        result = bc.execute_apply(preview, self.tmp_path, confirm=False)
        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(path.read_text(encoding="utf-8"), UNQUOTED_BRIEF)

    def test_confirm_requotes_and_preserves_everything_else(self):
        path = _write_raw(self.queue_dir, "20260101-000001-a.md", UNQUOTED_BRIEF)
        preview = self._preview_for(path, "2026-01-01T00:00:01-07:00")
        result = bc.execute_apply(preview, self.tmp_path, confirm=True)
        self.assertEqual(result["stamped"], ["20260101-000001-a"])
        self.assertEqual(result["skipped"], [])

        new_content = path.read_text(encoding="utf-8")
        self.assertIn("created: '2026-01-01T00:00:01-07:00'", new_content)
        # Everything outside the created: line is byte-for-byte unchanged.
        self.assertEqual(
            new_content.replace(
                "created: '2026-01-01T00:00:01-07:00'",
                "created: 2026-01-01T00:00:01-07:00",
            ),
            UNQUOTED_BRIEF,
        )

        fm = q._read_frontmatter(path)
        self.assertEqual(fm["created"], "2026-01-01T00:00:01-07:00")
        self.assertIsInstance(fm["created"], str)
        self.assertEqual(fm["id"], "20260101-000001-a")
        self.assertEqual(fm["focus"], "fix the thing")

    def test_confirm_is_idempotent_does_not_reclobber(self):
        path = _write_raw(self.queue_dir, "20260101-000001-a.md", UNQUOTED_BRIEF)
        preview = self._preview_for(path, "2026-01-01T00:00:01-07:00")
        bc.execute_apply(preview, self.tmp_path, confirm=True)
        # Re-run the SAME (now-stale) preview -- must not double-quote.
        result = bc.execute_apply(preview, self.tmp_path, confirm=True)
        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("already quoted since preview", result["skipped"][0]["reason"])
        content = path.read_text(encoding="utf-8")
        self.assertEqual(content.count("'2026-01-01T00:00:01-07:00'"), 1)

    def test_confirm_skips_file_removed_since_preview(self):
        path = _write_raw(self.queue_dir, "20260101-000001-a.md", UNQUOTED_BRIEF)
        preview = self._preview_for(path, "2026-01-01T00:00:01-07:00")
        path.unlink()
        result = bc.execute_apply(preview, self.tmp_path, confirm=True)
        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("no longer present", result["skipped"][0]["reason"])


class ResolvePreviewPayloadTestCase(unittest.TestCase):
    def test_accepts_well_formed_payload(self):
        payload = bc._resolve_preview_payload('{"proposals": [], "skipped": []}')
        self.assertEqual(payload["proposals"], [])

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            bc._resolve_preview_payload("not json")

    def test_rejects_missing_proposals_key(self):
        with self.assertRaises(ValueError):
            bc._resolve_preview_payload('{"skipped": []}')


class MainCliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.queue_dir = self.tmp_path / "queue"
        self.queue_dir.mkdir(parents=True)
        os.environ["WORK_QUEUE_DIR"] = str(self.tmp_path)
        importlib.reload(q)
        importlib.reload(bc)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def test_execute_reads_preview_from_stdin_when_flag_omitted(self):
        # A full-corpus preview routinely exceeds the shell argv size limit,
        # so `execute` must accept it via stdin instead of only `--preview`.
        path = _write_raw(self.queue_dir, "20260101-000001-a.md", UNQUOTED_BRIEF)
        preview_json = json.dumps(
            {
                "proposals": [
                    {
                        "id": path.stem,
                        "path": str(path),
                        "created_raw": "2026-01-01T00:00:01-07:00",
                    }
                ],
                "skipped": [],
            }
        )
        with mock.patch("sys.stdin", io.StringIO(preview_json)):
            rc = bc.main(["execute", "--queue-dir", str(self.tmp_path), "--confirm"])
        self.assertEqual(rc, 0)
        self.assertIn(
            "created: '2026-01-01T00:00:01-07:00'", path.read_text(encoding="utf-8")
        )


if __name__ == "__main__":
    unittest.main()
