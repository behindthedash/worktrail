#!/usr/bin/env python3
"""Unit tests for cluster_telemetry.py (spec 018, change
2026-07-14--cluster-precision-telemetry).

Run with:
    python3 -m pytest plugins/.../go/scripts/test_cluster_telemetry.py -q
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from worktrail.router import cluster_telemetry as ct


class DefaultLogPathTests(unittest.TestCase):
    def test_default_is_under_worktrail_home(self):
        with unittest.mock.patch.dict(
            os.environ, {"WORKTRAIL_HOME": "/tmp/wt-home"}, clear=False
        ):
            os.environ.pop("GO_CLUSTER_LOG", None)
            path = ct.default_log_path()
        self.assertEqual(path, Path("/tmp/wt-home") / "cluster-log.jsonl")

    def test_env_override(self):
        with unittest.mock.patch.dict(
            os.environ, {"GO_CLUSTER_LOG": "/tmp/custom-cluster-log.jsonl"}
        ):
            path = ct.default_log_path()
        self.assertEqual(path, Path("/tmp/custom-cluster-log.jsonl"))


class LogShownTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmp.name) / "cluster-log.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_one_record_per_cluster(self):
        clusters = [
            {"members": ["a", "b", "c"], "signals": ["same-target-spec"], "size": 3},
            {"members": ["d", "e"], "signals": ["duplicate-slug"], "size": 2},
        ]
        ct.log_shown(clusters, self.log_path)
        records = ct.read_records(self.log_path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["kind"], "shown")
        self.assertEqual(records[0]["members"], ["a", "b", "c"])
        self.assertEqual(records[0]["signals"], ["same-target-spec"])
        self.assertEqual(records[0]["size"], 3)
        self.assertIn("at", records[0])

    def test_empty_clusters_writes_nothing(self):
        ct.log_shown([], self.log_path)
        self.assertEqual(ct.read_records(self.log_path), [])

    def test_creates_parent_directory(self):
        nested = Path(self._tmp.name) / "nested" / "dir" / "log.jsonl"
        ct.log_shown([{"members": ["a", "b"], "signals": [], "size": 2}], nested)
        self.assertTrue(nested.is_file())

    def test_never_raises_on_unwritable_path(self):
        # A directory path can never be opened for append -- this must
        # degrade silently, not raise.
        try:
            ct.log_shown(
                [{"members": ["a"], "signals": [], "size": 1}], Path(self._tmp.name)
            )
        except Exception as exc:  # noqa: BLE001
            self.fail(f"log_shown raised instead of degrading: {exc}")


class LogOutcomeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmp.name) / "cluster-log.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_outcome_record(self):
        ct.log_outcome("consolidated", ["a", "b"], self.log_path)
        records = ct.read_records(self.log_path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["kind"], "outcome")
        self.assertEqual(records[0]["status"], "consolidated")
        self.assertEqual(records[0]["members"], ["a", "b"])

    def test_appends_across_calls(self):
        ct.log_outcome("consolidated", ["a", "b"], self.log_path)
        ct.log_outcome("declined", ["c", "d"], self.log_path)
        records = ct.read_records(self.log_path)
        self.assertEqual(len(records), 2)
        self.assertEqual([r["status"] for r in records], ["consolidated", "declined"])


class ReadRecordsTests(unittest.TestCase):
    def test_missing_file_returns_empty(self):
        self.assertEqual(
            ct.read_records(Path("/tmp/definitely-not-a-real-cluster-log.jsonl")), []
        )

    def test_skips_unparseable_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "log.jsonl"
            log_path.write_text(
                json.dumps({"kind": "shown", "members": ["a"]}) + "\n"
                "not valid json\n"
                + json.dumps({"kind": "outcome", "status": "declined"})
                + "\n",
                encoding="utf-8",
            )
            records = ct.read_records(log_path)
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["kind"], "shown")
        self.assertEqual(records[1]["kind"], "outcome")


class SummarizeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log_path = Path(self._tmp.name) / "cluster-log.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def test_no_data_yields_none_precision(self):
        summary = ct.summarize(self.log_path)
        self.assertEqual(
            summary, {"shown": 0, "consolidated": 0, "declined": 0, "precision": None}
        )

    def test_shown_only_no_decisions_yet(self):
        ct.log_shown(
            [{"members": ["a", "b", "c"], "signals": [], "size": 3}], self.log_path
        )
        summary = ct.summarize(self.log_path)
        self.assertEqual(summary["shown"], 1)
        self.assertIsNone(summary["precision"])

    def test_precision_computed_from_outcomes(self):
        ct.log_outcome("consolidated", ["a", "b"], self.log_path)
        ct.log_outcome("consolidated", ["c", "d"], self.log_path)
        ct.log_outcome("declined", ["e", "f"], self.log_path)
        summary = ct.summarize(self.log_path)
        self.assertEqual(summary["consolidated"], 2)
        self.assertEqual(summary["declined"], 1)
        self.assertAlmostEqual(summary["precision"], 2 / 3)

    def test_all_declined_is_zero_precision_not_none(self):
        ct.log_outcome("declined", ["a", "b"], self.log_path)
        summary = ct.summarize(self.log_path)
        self.assertEqual(summary["precision"], 0.0)


if __name__ == "__main__":
    unittest.main()
