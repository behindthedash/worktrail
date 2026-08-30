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
