#!/usr/bin/env python3
"""Unit tests for check_deferred_work_handoff.py's deferred-work-only signal
source: a run record's `deferred_work` entries are read and matched, while
its `scope_review` entries -- even an `out-of-scope | ... | different
purpose: ...` entry carrying deferral-phrase vocabulary -- are never read
or matched (Requirement: Deferred-Work-Only Signal Source)."""
from __future__ import annotations

import json
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from worktrail.router import run_record
from worktrail.router.check_deferred_work_handoff import (
    find_flagged,
    load_deferred_work_entries,
)


def _start_record(tmp: str) -> str:
    out = StringIO()
    with patch("sys.stdout", out):
        rc = run_record.main([
            "start", "--repo", "/tmp/fake-repo", "--request", "fix the thing",
            "--route", "F", "--risk", "low", "--dir", tmp,
        ])
    assert rc == 0
    return json.loads(out.getvalue())["path"]


def _append(path: str, key: str, value: str) -> None:
    rc = run_record.main(["append", path, key, value])
    assert rc == 0


def _scope_review_out_of_scope(path: str, item: str, reason: str) -> None:
    rc = run_record.main([
        "scope-review", path, "--item", item, "--status", "out-of-scope",
        "--reason", reason,
    ])
    assert rc == 0


class LoadDeferredWorkEntriesTests(unittest.TestCase):
    def test_reads_deferred_work_only_never_scope_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _append(path, "deferred_work", "clean up the retry helper in a later pr")
            _scope_review_out_of_scope(
                path, "rate-limit tuning",
                "different purpose: deferred until calibration is done",
            )

            entries = load_deferred_work_entries([path])

            texts = [e["text"] for e in entries]
            self.assertEqual(texts, ["clean up the retry helper in a later pr"])
            for text in texts:
                self.assertNotIn("different purpose", text)
                self.assertNotIn("rate-limit tuning", text)
            for entry in entries:
                self.assertEqual(entry["run_record"], path)

    def test_scope_review_never_surfaced_even_when_deferred_work_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _scope_review_out_of_scope(
                path, "logging cleanup",
                "different purpose: deferred, follow-up work for later",
            )

            entries = load_deferred_work_entries([path])

            self.assertEqual(entries, [])


class FindFlaggedIgnoresScopeReviewTests(unittest.TestCase):
    def test_scope_review_deferral_vocabulary_never_flagged(self):
        with tempfile.TemporaryDirectory() as tmp, \
                tempfile.TemporaryDirectory() as queue_home:
            path = _start_record(tmp)
            _scope_review_out_of_scope(
                path, "rate-limit tuning",
                "different purpose: deferred until calibration is done",
            )
            _append(path, "deferred_work", "wire the retry backoff in a later pr")

            with patch.dict(
                "os.environ", {"WORK_QUEUE_DIR": str(Path(queue_home) / "work-queue")},
            ):
                flagged = find_flagged([path])

            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["text"], "wire the retry backoff in a later pr")
            flagged_texts = [f["text"] for f in flagged]
            self.assertNotIn(
                "different purpose: deferred until calibration is done", flagged_texts,
            )


if __name__ == "__main__":
    unittest.main()
