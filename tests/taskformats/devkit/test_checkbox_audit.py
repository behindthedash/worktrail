"""Tests for checkbox_audit.py's Reconciliation-note exclusion.

Regression coverage for the false-positive drift reported in queue brief
20260830-152212: a checkbox individually verified and left unchecked with a
cited "Reconciliation note:" (PR #669's convention) was still counted as
drift, forcing every future sweep to re-flag already-reconciled files.
"""

from pathlib import Path

from worktrail.taskformats.devkit.checkbox_audit import audit_repo

TASK_HEADER = """---
id: TASK-001
title: Example task
spec: 001-example
status: completed
---

"""


def _write_task(repo: Path, body: str) -> Path:
    task_dir = repo / "docs" / "specs" / "001-example" / "tasks"
    task_dir.mkdir(parents=True, exist_ok=True)
    path = task_dir / "TASK-001.md"
    path.write_text(TASK_HEADER + body)
    return path


def test_reconciled_checkbox_is_not_flagged(tmp_path):
    body = (
        "## Acceptance Criteria\n"
        "- [ ] Requires live DB state that can't be verified from the repo (AC-021).\n"
        "  - Reconciliation note: documented as a manual `psql` check rather than "
        "asserting it.\n"
    )
    _write_task(tmp_path, body)

    hits = audit_repo(tmp_path)

    assert hits == []


def test_genuinely_unchecked_checkbox_is_still_flagged(tmp_path):
    body = "## Acceptance Criteria\n- [ ] Never verified, no reconciliation note (AC-001).\n"
    _write_task(tmp_path, body)

    hits = audit_repo(tmp_path)

    assert len(hits) == 1
    assert hits[0].unchecked_count == 1


def test_mixed_reconciled_and_genuine_drift_counts_only_genuine(tmp_path):
    body = (
        "## Acceptance Criteria\n"
        "- [ ] Reconciled item (AC-001).\n"
        "  - Reconciliation note: verified manually, evidence cited.\n"
        "- [ ] Genuinely unreconciled item (AC-002).\n"
    )
    _write_task(tmp_path, body)

    hits = audit_repo(tmp_path)

    assert len(hits) == 1
    assert hits[0].unchecked_count == 1
    assert hits[0].total_count == 1


def test_reconciliation_note_with_parenthetical_qualifier_is_not_flagged(tmp_path):
    """The convention is also written `Reconciliation note (<what>):` -- the
    qualifier names which clause of a multi-clause criterion the note addresses.
    Regression for queue brief 20260907-060010, where every one of the eight
    flagged gracefully-giving-back files used this spelling.
    """
    body = (
        "## Acceptance Criteria\n"
        "- [ ] Save button uploads the image to Cloudinary (AC-016).\n"
        "  - Reconciliation note (Cloudinary reference): storage migrated to Bunny; "
        "the observable save behavior is confirmed.\n"
    )
    _write_task(tmp_path, body)

    assert audit_repo(tmp_path) == []


def test_reconciliation_note_qualifier_containing_parentheses_is_not_flagged(tmp_path):
    body = (
        "## Acceptance Criteria\n"
        "- [ ] `render()` wraps `_render()` in a `p-limit(2)` singleton (AC-003).\n"
        "  - Reconciliation note (p-limit(2) singleton): a hand-rolled "
        "`ConcurrencyLimiter` is used instead; the 2-slot guard is verified.\n"
    )
    _write_task(tmp_path, body)

    assert audit_repo(tmp_path) == []


def test_reconciliation_note_after_wrapped_criterion_is_not_flagged(tmp_path):
    """A criterion long enough to wrap puts its note several lines below the
    `- [ ]` marker. Only inspecting the immediately-following line missed it.
    """
    body = (
        "## Acceptance Criteria\n"
        "- [ ] **AC-013 [SEF]** -- the admin review UI continues to list, "
        "mark-delivered, restore, and\n"
        "      delete requests with no rendering or styling rewrite. Verify by "
        "using\n"
        "      `/admin/feedback` in a running dev instance.\n"
        "  - Reconciliation note: covered at the component-test level rather than "
        "a live session.\n"
    )
    _write_task(tmp_path, body)

    assert audit_repo(tmp_path) == []


def test_wrapped_criterion_without_note_is_still_flagged(tmp_path):
    """The forward scan must not swallow genuine drift on a wrapped criterion."""
    body = (
        "## Acceptance Criteria\n"
        "- [ ] **AC-014 [EXT]** -- `npm run build`, `npm test`, and `npm run e2e` "
        "all pass in\n"
        "      the worktree after the re-wire.\n"
        "- [ ] A second, separate unverified criterion (AC-015).\n"
    )
    _write_task(tmp_path, body)

    hits = audit_repo(tmp_path)

    assert len(hits) == 1
    assert hits[0].unchecked_count == 2


def test_note_separated_by_blank_line_does_not_reconcile(tmp_path):
    """A note detached by a blank line belongs to the section, not the item."""
    body = (
        "## Acceptance Criteria\n"
        "- [ ] Unverified criterion (AC-001).\n"
        "\n"
        "  - Reconciliation note: detached from the item above.\n"
    )
    _write_task(tmp_path, body)

    hits = audit_repo(tmp_path)

    assert len(hits) == 1
    assert hits[0].unchecked_count == 1
