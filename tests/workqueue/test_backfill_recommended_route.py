#!/usr/bin/env python3
"""Tests for backfill_recommended_route.py (handoff 20260717-200600).

Stubs `classify_focus` rather than depending on classify.py's real routing
heuristics (already covered by devkit-pm-go/scripts/test_classify.py) -- this
suite is about the preview/execute contract: which briefs get proposed, and
that execute never clobbers or silently loses a write.

Run:
    python3 -m pytest tests/workqueue/test_backfill_recommended_route.py -q
"""

from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional
from unittest.mock import patch

from worktrail.workqueue import work_queue as q
from worktrail.workqueue import backfill_recommended_route as br


def _write_brief(dirpath: Path, filename: str, *, focus: str = "", recommended_route: Optional[str] = None) -> Path:
    fm = [f"id: {filename[:-3]}", f'focus: "{focus}"', "status: queued"]
    if recommended_route:
        fm.append(f"recommended-route: {recommended_route}")
    content = "---\n" + "\n".join(fm) + "\n---\n\n## Focus\n\n" + focus + "\n"
    path = dirpath / filename
    path.write_text(content, encoding="utf-8")
    return path


def _classify_result(route: str, confidence: str = "high", ambiguous=None) -> Dict[str, Any]:
    return {
        "route": route,
        "route_name": "stub",
        "confidence": confidence,
        "ambiguous_between": ambiguous or [],
        "reason": "stub reason",
    }


class BuildPreviewTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.queue_dir = self.tmp_path / "queue"
        self.queue_dir.mkdir(parents=True)
        os.environ["WORK_QUEUE_DIR"] = str(self.tmp_path)
        importlib.reload(q)
        importlib.reload(br)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def test_skips_already_stamped_brief(self):
        _write_brief(self.queue_dir, "20260101-000001-a.md", focus="fix the thing", recommended_route="F")
        with patch.object(br, "classify_focus") as mock_classify:
            result = br.build_preview(self.queue_dir, Path("/fake/classify.py"))
        mock_classify.assert_not_called()
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["skipped"], [])

    def test_skips_brief_with_no_focus_text(self):
        (self.queue_dir / "20260101-000002-b.md").write_text("---\nid: b\nstatus: queued\n---\n\nno focus section\n", encoding="utf-8")
        result = br.build_preview(self.queue_dir, Path("/fake/classify.py"))
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("no focus text", result["skipped"][0]["reason"])

    def test_skips_low_confidence(self):
        _write_brief(self.queue_dir, "20260101-000003-c.md", focus="do something vague")
        with patch.object(br, "classify_focus", return_value=_classify_result("A", confidence="low")):
            result = br.build_preview(self.queue_dir, Path("/fake/classify.py"))
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("confidence='low'", result["skipped"][0]["reason"])

    def test_skips_ambiguous_even_at_high_confidence(self):
        _write_brief(self.queue_dir, "20260101-000004-d.md", focus="fix or change the spec")
        with patch.object(br, "classify_focus", return_value=_classify_result("F", confidence="high", ambiguous=["F", "G"])):
            result = br.build_preview(self.queue_dir, Path("/fake/classify.py"))
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["skipped"]), 1)

    def test_skips_when_classify_fails(self):
        _write_brief(self.queue_dir, "20260101-000005-e.md", focus="fix the thing")
        with patch.object(br, "classify_focus", return_value=None):
            result = br.build_preview(self.queue_dir, Path("/fake/classify.py"))
        self.assertEqual(result["proposals"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("classify.py errored", result["skipped"][0]["reason"])

    def test_proposes_high_confidence_unambiguous(self):
        _write_brief(self.queue_dir, "20260101-000006-f.md", focus="fix the failing test")
        with patch.object(br, "classify_focus", return_value=_classify_result("F", confidence="high")):
            result = br.build_preview(self.queue_dir, Path("/fake/classify.py"))
        self.assertEqual(len(result["proposals"]), 1)
        self.assertEqual(result["proposals"][0]["id"], "20260101-000006-f")
        self.assertEqual(result["proposals"][0]["proposed_route"], "F")
        self.assertEqual(result["skipped"], [])

    def test_proposes_medium_confidence_unambiguous(self):
        _write_brief(self.queue_dir, "20260101-000007-g.md", focus="plan the new feature")
        with patch.object(br, "classify_focus", return_value=_classify_result("C", confidence="medium")):
            result = br.build_preview(self.queue_dir, Path("/fake/classify.py"))
        self.assertEqual(len(result["proposals"]), 1)


class ExecuteApplyTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.queue_dir = self.tmp_path / "queue"
        self.queue_dir.mkdir(parents=True)
        os.environ["WORK_QUEUE_DIR"] = str(self.tmp_path)
        importlib.reload(q)
        importlib.reload(br)

    def tearDown(self):
        os.environ.pop("WORK_QUEUE_DIR", None)
        self._tmp.cleanup()

    def _preview_for(self, path: Path, route: str = "F") -> Dict[str, Any]:
        return {"proposals": [{
            "id": path.stem, "path": str(path), "focus": "fix the thing",
            "proposed_route": route, "route_name": "defect-repair",
            "confidence": "high", "classify_reason": "stub",
        }], "skipped": []}

    def test_decline_writes_nothing(self):
        path = _write_brief(self.queue_dir, "20260101-000001-a.md", focus="fix the thing")
        preview = self._preview_for(path)
        result = br.execute_apply(preview, self.queue_dir, confirm=False)
        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertNotIn("recommended-route", path.read_text(encoding="utf-8"))

    def test_confirm_stamps_and_preserves_existing_fields(self):
        path = _write_brief(self.queue_dir, "20260101-000001-a.md", focus="fix the thing")
        preview = self._preview_for(path, route="F")
        result = br.execute_apply(preview, self.queue_dir, confirm=True)
        self.assertEqual(result["stamped"], ["20260101-000001-a"])
        self.assertEqual(result["skipped"], [])
        fm = q._read_frontmatter(path)
        self.assertEqual(fm["recommended-route"], "F")
        self.assertEqual(fm["id"], "20260101-000001-a")
        self.assertEqual(fm["focus"], "fix the thing")
        self.assertEqual(fm["status"], "queued")

    def test_confirm_is_idempotent_does_not_clobber(self):
        path = _write_brief(self.queue_dir, "20260101-000001-a.md", focus="fix the thing")
        preview = self._preview_for(path, route="F")
        br.execute_apply(preview, self.queue_dir, confirm=True)
        # Re-run the SAME (now-stale) preview -- must not clobber the existing stamp.
        result = br.execute_apply(preview, self.queue_dir, confirm=True)
        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("already stamped since preview", result["skipped"][0]["reason"])
        fm = q._read_frontmatter(path)
        self.assertEqual(fm["recommended-route"], "F")

    def test_confirm_skips_brief_removed_since_preview(self):
        path = _write_brief(self.queue_dir, "20260101-000001-a.md", focus="fix the thing")
        preview = self._preview_for(path)
        path.unlink()  # simulate the brief being claimed/moved between preview and execute
        result = br.execute_apply(preview, self.queue_dir, confirm=True)
        self.assertEqual(result["stamped"], [])
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("no longer present", result["skipped"][0]["reason"])


class ResolvePreviewPayloadTestCase(unittest.TestCase):
    def test_accepts_well_formed_payload(self):
        payload = br._resolve_preview_payload('{"proposals": [], "skipped": []}')
        self.assertEqual(payload["proposals"], [])

    def test_rejects_invalid_json(self):
        with self.assertRaises(ValueError):
            br._resolve_preview_payload("not json")

    def test_rejects_missing_proposals_key(self):
        with self.assertRaises(ValueError):
            br._resolve_preview_payload('{"skipped": []}')


if __name__ == "__main__":
    unittest.main()
