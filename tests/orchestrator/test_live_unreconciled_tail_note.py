#!/usr/bin/env python3
"""`_format_unreconciled_tail_note` must partition findings by `reconcile_state`.

The defect: the note rendered one fixed "commits never merged onto base" clause
over every finding, ignoring each finding's recorded reconciliation outcome. On
run `go-20261004-093132` the single tail finding had already been merged onto
base by `reconcile_unreconciled_tail_evidence` (`reconcile_state: "merged"`),
yet the run still printed the loud manual-reconcile warning -- a false alarm an
operator cannot act on. The fix drops `merged` findings from the note entirely,
splits `opened`/`already-open`/`superseded` onto a quieter awaiting-merge line,
and keeps the original wording for the genuinely manual residues
(`quarantined`, or a raw finding with no `reconcile_state` at all).

Pure unit tests against `live._format_unreconciled_tail_note` -- no repo, no
subprocess. The call-site behavior (timestamps, stdout) is covered by
`test_live_tail_reconciliation.py`.

Run: python3 -m pytest tests/orchestrator/test_live_unreconciled_tail_note.py
"""

from __future__ import annotations

import unittest

from worktrail.orchestrator import live

MANUAL_LEAD = (
    "!! 1 tail task(s) completed with unreconciled evidence "
    "(commits never merged onto base -- reconcile before worktree cleanup, "
    "see journal `unreconciled_tail_evidence`): "
)
AWAITING_LEAD = (
    "1 tail task(s) auto-reconciliation PR(s) awaiting merge "
    "(see journal `unreconciled_tail_evidence`): "
)


def _finding(task_id: str = "TASK-999", **overrides) -> dict:
    finding = {
        "task": task_id,
        "head_sha": "deadbeef",
        "worktree": "/tmp/fake-wt",
        "reason": "commit never merged onto base",
    }
    finding.update(overrides)
    return finding


class PartitionByReconcileStateTest(unittest.TestCase):
    def test_empty_findings_returns_none(self):
        self.assertIsNone(live._format_unreconciled_tail_note([]))

    def test_single_merged_finding_returns_none(self):
        """The observed `go-20261004-093132` shape: assert None outright, not
        merely that the text lacks the phrase -- the run must print nothing."""
        findings = [
            _finding(
                "TASK-1",
                reconcile_state="merged",
                reconcile_pr_url="https://example.test/pr/4",
            )
        ]
        self.assertIsNone(live._format_unreconciled_tail_note(findings))

    def test_all_merged_findings_return_none(self):
        findings = [
            _finding("TASK-1", reconcile_state="merged"),
            _finding("TASK-2", reconcile_state="merged"),
        ]
        self.assertIsNone(live._format_unreconciled_tail_note(findings))

    def test_merged_entry_omitted_while_quarantined_keeps_manual_wording(self):
        findings = [
            _finding(
                "TASK-MERGED",
                reconcile_state="merged",
                reconcile_pr_url="https://example.test/pr/5",
            ),
            _finding("TASK-STUCK", reconcile_state="quarantined", reconcile_pr_url=""),
        ]
        note = live._format_unreconciled_tail_note(findings)
        self.assertIsNotNone(note)
        self.assertNotIn(
            "TASK-MERGED",
            note,
            "a merged finding must be omitted from the note entirely",
        )
        self.assertEqual(
            note,
            MANUAL_LEAD
            + "TASK-STUCK (sha deadbeef @ /tmp/fake-wt reconcile=quarantined)",
            "the manual line must keep the exact original wording, counting only "
            "the findings it reports",
        )

    def test_merged_plus_opened_emits_only_the_awaiting_line(self):
        findings = [
            _finding("TASK-MERGED", reconcile_state="merged"),
            _finding(
                "TASK-OPEN",
                reconcile_state="opened",
                reconcile_pr_url="https://example.test/pr/7",
            ),
        ]
        note = live._format_unreconciled_tail_note(findings)
        self.assertIsNotNone(note)
        self.assertNotIn("!!", note, "an awaiting PR is not a manual-reconcile case")
        self.assertNotIn("commits never merged onto base", note)
        self.assertEqual(
            note,
            AWAITING_LEAD + "TASK-OPEN (sha deadbeef @ /tmp/fake-wt reconcile=opened "
            "https://example.test/pr/7)",
            "the awaiting line must describe only that bucket, carry the PR url, "
            "and count only the findings it reports",
        )

    def test_lone_quarantined_finding_keeps_manual_warning(self):
        findings = [_finding("TASK-STUCK", reconcile_state="quarantined")]
        note = live._format_unreconciled_tail_note(findings)
        self.assertEqual(
            note,
            MANUAL_LEAD
            + "TASK-STUCK (sha deadbeef @ /tmp/fake-wt reconcile=quarantined)",
        )

    def test_finding_without_reconcile_state_keeps_manual_warning(self):
        """The pre-reconciliation journal shape: raw
        `detect_unreconciled_evidence` output carries no `reconcile_state`."""
        findings = [_finding("TASK-RAW")]
        note = live._format_unreconciled_tail_note(findings)
        self.assertEqual(
            note,
            MANUAL_LEAD + "TASK-RAW (sha deadbeef @ /tmp/fake-wt)",
            "a no-state finding is manual residue and must keep the `!!` wording",
        )

    def test_superseded_lands_in_awaiting_bucket_and_names_descendant(self):
        findings = [
            _finding(
                "TASK-ANCESTOR",
                reconcile_state="superseded",
                reconcile_pr_url="",
                reconcile_superseded_by="TASK-DESCENDANT",
            )
        ]
        note = live._format_unreconciled_tail_note(findings)
        self.assertIsNotNone(note)
        self.assertNotIn("!!", note)
        self.assertEqual(
            note,
            AWAITING_LEAD
            + "TASK-ANCESTOR (sha deadbeef @ /tmp/fake-wt reconcile=superseded "
            "by TASK-DESCENDANT)",
        )

    def test_already_open_lands_in_awaiting_bucket(self):
        findings = [
            _finding(
                "TASK-REUSED",
                reconcile_state="already-open",
                reconcile_pr_url="https://example.test/pr/8",
            )
        ]
        note = live._format_unreconciled_tail_note(findings)
        self.assertEqual(
            note,
            AWAITING_LEAD
            + "TASK-REUSED (sha deadbeef @ /tmp/fake-wt reconcile=already-open "
            "https://example.test/pr/8)",
        )

    def test_opened_plus_quarantined_emits_manual_line_first(self):
        findings = [
            _finding(
                "TASK-OPEN",
                reconcile_state="opened",
                reconcile_pr_url="https://example.test/pr/9",
            ),
            _finding("TASK-STUCK", reconcile_state="quarantined"),
        ]
        note = live._format_unreconciled_tail_note(findings)
        self.assertIsNotNone(note)
        lines = note.splitlines()
        self.assertEqual(len(lines), 2, f"expected two lines, got {note!r}")
        self.assertEqual(
            lines[0],
            MANUAL_LEAD
            + "TASK-STUCK (sha deadbeef @ /tmp/fake-wt reconcile=quarantined)",
            "the `!!` manual line must come first",
        )
        self.assertEqual(
            lines[1],
            AWAITING_LEAD + "TASK-OPEN (sha deadbeef @ /tmp/fake-wt reconcile=opened "
            "https://example.test/pr/9)",
        )

    def test_entry_rendering_unchanged_for_quarantined_finding(self):
        """`_entry` is reused verbatim: `task (sha <head_sha> @ <worktree>`
        plus the ` reconcile=<state>` suffix."""
        findings = [
            _finding(
                "TASK-1.1",
                head_sha="abc12345",
                worktree="/tmp/wt-1",
                reconcile_state="quarantined",
            )
        ]
        note = live._format_unreconciled_tail_note(findings)
        self.assertIn("TASK-1.1 (sha abc12345 @ /tmp/wt-1 reconcile=quarantined)", note)


if __name__ == "__main__":
    unittest.main(verbosity=2)
