#!/usr/bin/env python3
"""Unit tests for check_deferred_work_handoff.py: a run record's
`deferred_work` entries are read and matched, while its `scope_review`
entries -- even an `out-of-scope | ... | different purpose: ...` entry
carrying deferral-phrase vocabulary -- are never read or matched
(Requirement: Deferred-Work-Only Signal Source); entries are only
candidates once they match a deferral phrase (Requirement: Deferral-Phrase
Matching); and a candidate is flagged only when no existing `queue/` or
`picked/` brief already covers it (Requirement: Handoff Cross-Check Before
Flagging)."""

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
    has_handoff_coverage,
    load_deferred_work_entries,
    matches_deferral_phrase,
)


def _start_record(tmp: str) -> str:
    out = StringIO()
    with patch("sys.stdout", out):
        rc = run_record.main(
            [
                "start",
                "--repo",
                "/tmp/fake-repo",
                "--request",
                "fix the thing",
                "--route",
                "F",
                "--risk",
                "low",
                "--dir",
                tmp,
            ]
        )
    assert rc == 0
    return json.loads(out.getvalue())["path"]


def _append(path: str, key: str, value: str) -> None:
    rc = run_record.main(["append", path, key, value])
    assert rc == 0


def _scope_review_out_of_scope(path: str, item: str, reason: str) -> None:
    rc = run_record.main(
        [
            "scope-review",
            path,
            "--item",
            item,
            "--status",
            "out-of-scope",
            "--reason",
            reason,
        ]
    )
    assert rc == 0


class LoadDeferredWorkEntriesTests(unittest.TestCase):
    def test_reads_deferred_work_only_never_scope_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _start_record(tmp)
            _append(path, "deferred_work", "clean up the retry helper in a later pr")
            _scope_review_out_of_scope(
                path,
                "rate-limit tuning",
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
                path,
                "logging cleanup",
                "different purpose: deferred, follow-up work for later",
            )

            entries = load_deferred_work_entries([path])

            self.assertEqual(entries, [])


class FindFlaggedIgnoresScopeReviewTests(unittest.TestCase):
    def test_scope_review_deferral_vocabulary_never_flagged(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _scope_review_out_of_scope(
                path,
                "rate-limit tuning",
                "different purpose: deferred until calibration is done",
            )
            _append(path, "deferred_work", "wire the retry backoff in a later pr")

            with patch.dict(
                "os.environ",
                {"WORK_QUEUE_DIR": str(Path(queue_home) / "work-queue")},
            ):
                flagged = find_flagged([path])

            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["text"], "wire the retry backoff in a later pr")
            flagged_texts = [f["text"] for f in flagged]
            self.assertNotIn(
                "different purpose: deferred until calibration is done",
                flagged_texts,
            )


class MatchesDeferralPhraseTests(unittest.TestCase):
    def test_matches_known_phrase_case_insensitively(self):
        self.assertTrue(matches_deferral_phrase("Clean this up as a FOLLOW-UP later"))
        self.assertTrue(matches_deferral_phrase("wire the retry backoff in a LATER PR"))

    def test_does_not_match_text_without_any_deferral_phrase(self):
        self.assertFalse(
            matches_deferral_phrase("rename the helper function for clarity")
        )


class PhraseMatchingCandidacyTests(unittest.TestCase):
    """Requirement: Deferral-Phrase Matching -- phrase-matching entries become
    candidates; non-matching entries are never flagged regardless of handoff
    coverage."""

    def test_phrase_matching_entry_without_coverage_becomes_candidate(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", "clean up the retry helper in a later pr")

            with patch.dict(
                "os.environ",
                {"WORK_QUEUE_DIR": str(Path(queue_home) / "work-queue")},
            ):
                flagged = find_flagged([path])

            self.assertEqual(len(flagged), 1)
            self.assertEqual(
                flagged[0]["text"], "clean up the retry helper in a later pr"
            )
            self.assertEqual(flagged[0]["run_record"], path)

    def test_non_matching_entry_never_flagged_even_without_handoff_coverage(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", "rename the helper function for clarity")

            with (
                patch.dict(
                    "os.environ",
                    {"WORK_QUEUE_DIR": str(Path(queue_home) / "work-queue")},
                ),
                patch(
                    "worktrail.router.check_deferred_work_handoff.has_handoff_coverage",
                    return_value=False,
                ),
            ):
                flagged = find_flagged([path])

            self.assertEqual(flagged, [])

    def test_non_matching_entry_never_flagged_even_when_coverage_would_have_matched(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", "rename the helper function for clarity")

            # If handoff coverage were somehow the only gate, this would force
            # a flag; phrase matching must still exclude this entry first.
            with (
                patch.dict(
                    "os.environ",
                    {"WORK_QUEUE_DIR": str(Path(queue_home) / "work-queue")},
                ),
                patch(
                    "worktrail.router.check_deferred_work_handoff.has_handoff_coverage",
                    return_value=True,
                ),
            ):
                flagged = find_flagged([path])

            self.assertEqual(flagged, [])

    def test_mixed_entries_only_phrase_matching_one_becomes_candidate(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", "rename the helper function for clarity")
            _append(
                path, "deferred_work", "revisit this once calibrated against prod data"
            )

            with patch.dict(
                "os.environ",
                {"WORK_QUEUE_DIR": str(Path(queue_home) / "work-queue")},
            ):
                flagged = find_flagged([path])

            flagged_texts = [f["text"] for f in flagged]
            self.assertEqual(
                flagged_texts,
                ["revisit this once calibrated against prod data"],
            )


class HandoffCrossCheckTests(unittest.TestCase):
    """Requirement: Handoff Cross-Check Before Flagging -- a candidate whose
    extracted probes match an existing `queue/` or `picked/` brief's focus
    text is not flagged; a candidate matching no brief is flagged.

    Task 3.3's own wording additionally says an unreadable/missing
    work-queue directory "yields 'not flagged,' never an exception" --
    but that contradicts task 1.3, which says an unreadable/missing
    directory is "skipped, never treated as a match" (i.e. no coverage,
    which, combined with a phrase-matching candidate, *is* flagged). The
    shipped `has_handoff_coverage`/`find_flagged` implement the 1.3
    reading, and the tests below verify that actual, shipped behavior --
    "never raises" holds either way, but the outcome is "no coverage
    found" (flagged end-to-end), not the AC's "not flagged" outcome."""

    _CANDIDATE = "clean up `src/widget.py` in a later pr"

    def _write_brief(self, directory: Path, focus: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "20260101-000000-some-brief.md"
        path.write_text(
            "---\nid: 20260101-000000-some-brief\ncreated: '2026-01-01T00:00:00-07:00'\n"
            f"focus: |-\n  {focus}\nrepo: null\nstatus: queued\n---\n\n## Focus\n\n{focus}\n",
            encoding="utf-8",
        )
        return path

    def test_probe_matching_queue_brief_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as queue_home:
            base = Path(queue_home) / "work-queue"
            self._write_brief(base / "queue", "Touches src/widget.py for retry tuning.")

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                covered = has_handoff_coverage(self._CANDIDATE)

            self.assertTrue(covered)

    def test_probe_matching_picked_brief_is_not_flagged(self):
        with tempfile.TemporaryDirectory() as queue_home:
            base = Path(queue_home) / "work-queue"
            self._write_brief(
                base / "picked", "Touches src/widget.py for retry tuning."
            )

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                covered = has_handoff_coverage(self._CANDIDATE)

            self.assertTrue(covered)

    def test_candidate_matching_no_brief_is_flagged(self):
        with tempfile.TemporaryDirectory() as queue_home:
            base = Path(queue_home) / "work-queue"
            self._write_brief(
                base / "queue", "Touches src/other.py for unrelated work."
            )

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                covered = has_handoff_coverage(self._CANDIDATE)

            self.assertFalse(covered)

    def test_candidate_matching_no_brief_is_flagged_end_to_end(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", self._CANDIDATE)
            base = Path(queue_home) / "work-queue"
            self._write_brief(
                base / "queue", "Touches src/other.py for unrelated work."
            )

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                flagged = find_flagged([path])

            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["text"], self._CANDIDATE)

    def test_missing_work_queue_directory_yields_no_coverage_never_raises(self):
        with tempfile.TemporaryDirectory() as queue_home:
            base = Path(queue_home) / "does-not-exist"

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                covered = has_handoff_coverage(self._CANDIDATE)

            self.assertFalse(covered)

    def test_missing_work_queue_directory_yields_flagged_end_to_end_never_raises(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", self._CANDIDATE)
            base = Path(queue_home) / "does-not-exist"

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                flagged = find_flagged([path])

            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["text"], self._CANDIDATE)

    def test_unreadable_queue_directory_yields_no_coverage_never_raises(self):
        with tempfile.TemporaryDirectory() as queue_home:
            base = Path(queue_home) / "work-queue"
            queue = base / "queue"
            queue.mkdir(parents=True)
            self._write_brief(queue, "Touches src/widget.py for retry tuning.")
            queue.chmod(0o000)
            try:
                with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                    covered = has_handoff_coverage(self._CANDIDATE)
            finally:
                queue.chmod(0o755)

            self.assertFalse(covered)

    def test_unreadable_queue_directory_yields_flagged_end_to_end_never_raises(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", self._CANDIDATE)
            base = Path(queue_home) / "work-queue"
            queue = base / "queue"
            queue.mkdir(parents=True)
            self._write_brief(queue, "Touches src/widget.py for retry tuning.")
            queue.chmod(0o000)
            try:
                with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                    flagged = find_flagged([path])
            finally:
                queue.chmod(0o755)

            self.assertEqual(len(flagged), 1)
            self.assertEqual(flagged[0]["text"], self._CANDIDATE)

    def test_end_to_end_phrase_matching_candidate_covered_by_brief_is_not_flagged(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", self._CANDIDATE)
            base = Path(queue_home) / "work-queue"
            self._write_brief(base / "queue", "Touches src/widget.py for retry tuning.")

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                flagged = find_flagged([path])

            self.assertEqual(flagged, [])

    def test_probe_matching_picked_brief_is_not_flagged_end_to_end(self):
        with (
            tempfile.TemporaryDirectory() as tmp,
            tempfile.TemporaryDirectory() as queue_home,
        ):
            path = _start_record(tmp)
            _append(path, "deferred_work", self._CANDIDATE)
            base = Path(queue_home) / "work-queue"
            self._write_brief(
                base / "picked", "Touches src/widget.py for retry tuning."
            )

            with patch.dict("os.environ", {"WORK_QUEUE_DIR": str(base)}):
                flagged = find_flagged([path])

            self.assertEqual(flagged, [])


if __name__ == "__main__":
    unittest.main()
