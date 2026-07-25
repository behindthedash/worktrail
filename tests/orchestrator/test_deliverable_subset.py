#!/usr/bin/env python3
"""Test suite for deliverable_subset enhancements (TASK-002, refined by #72).

Tests that ALREADY_INTEGRATED ("completed") tasks are excluded from the
deliverable subset, while "done" tasks (worker completed in the current run,
branch freshly committed) remain deliverable. Cascade-drop follows failed edges
only: an already-integrated dependency does NOT cascade-drop its pending
dependents, but a failed dependency does.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from worktrail.orchestrator import coordinator  # noqa: E402


def test_ac001_done_tasks_are_deliverable():
    """AC-001 (#72): "done" tasks ship; only ALREADY_INTEGRATED is dropped.

    A "done" task completed in the current run with a freshly-committed branch
    must be merged into the group PR, not quarantined.
    """
    tasks = [
        {"id": "TASK-001", "deps": [], "files": ["a.ts"], "kind": "impl"},
        {"id": "TASK-002", "deps": ["TASK-001"], "files": ["b.ts"], "kind": "impl"},
    ]
    status = {"TASK-001": "done", "TASK-002": "pending"}
    deliverable, dropped = coordinator.deliverable_subset(["TASK-001", "TASK-002"], tasks, status)
    assert deliverable == ["TASK-001", "TASK-002"]
    assert dropped == []


def test_ac002_preompleted_absent():
    """AC-002: Pre-completed task is absent from every deliverable subset."""
    tasks = [
        {"id": "TASK-001", "deps": [], "files": ["a.ts"], "kind": "impl"},
        {"id": "TASK-002", "deps": ["TASK-001"], "files": ["b.ts"], "kind": "impl"},
    ]
    status = {"TASK-001": "completed", "TASK-002": "pending"}
    deliverable, dropped = coordinator.deliverable_subset(["TASK-001", "TASK-002"], tasks, status)
    assert "TASK-001" not in deliverable
    assert "TASK-001" in dropped


def test_ac003_integrated_dependent_stays_deliverable():
    """AC-003: Pending dependent of an ALREADY_INTEGRATED dependency stays deliverable.

    The integrated dependency is dropped (already on a group branch from a prior
    run) but does NOT cascade-drop its pending dependents.
    """
    tasks = [
        {"id": "TASK-A", "deps": [], "files": ["a.ts"], "kind": "impl"},
        {"id": "TASK-B", "deps": ["TASK-A"], "files": ["b.ts"], "kind": "impl"},
        {"id": "TASK-C", "deps": ["TASK-B"], "files": ["c.ts"], "kind": "impl"},
    ]
    status = {"TASK-A": "completed", "TASK-B": "pending", "TASK-C": "pending"}
    deliverable, dropped = coordinator.deliverable_subset(
        ["TASK-A", "TASK-B", "TASK-C"], tasks, status
    )
    # Already-integrated dependency doesn't cascade-drop
    assert "TASK-B" in deliverable
    assert "TASK-C" in deliverable
    assert "TASK-A" in dropped


def test_ac003_failed_dependent_dropped():
    """AC-003 (contrast): Pending dependent of failed dependency is cascade-dropped."""
    tasks = [
        {"id": "TASK-X", "deps": [], "files": ["x.ts"], "kind": "impl"},
        {"id": "TASK-Y", "deps": ["TASK-X"], "files": ["y.ts"], "kind": "impl"},
    ]
    status = {"TASK-X": "failed", "TASK-Y": "pending"}
    deliverable, dropped = coordinator.deliverable_subset(["TASK-X", "TASK-Y"], tasks, status)
    # Failed dependency DOES cascade-drop
    assert "TASK-Y" in dropped


def test_ac004_uses_already_integrated_constant():
    """AC-004 (#72): exclusion uses coordinator.ALREADY_INTEGRATED, not DONE.

    Only "completed" (already integrated) is dropped; "done" (this run) ships.
    """
    assert hasattr(coordinator, "ALREADY_INTEGRATED")
    assert coordinator.ALREADY_INTEGRATED == {"completed"}

    # "completed" is dropped (already on a group branch from a prior run)
    for status_val in coordinator.ALREADY_INTEGRATED:
        tasks = [{"id": "T1", "deps": [], "files": ["a.ts"], "kind": "impl"}]
        deliverable, dropped = coordinator.deliverable_subset(["T1"], tasks, {"T1": status_val})
        assert "T1" in dropped
        assert "T1" not in deliverable

    # "done" is NOT excluded — its fresh branch must ship
    tasks = [{"id": "T1", "deps": [], "files": ["a.ts"], "kind": "impl"}]
    deliverable, dropped = coordinator.deliverable_subset(["T1"], tasks, {"T1": "done"})
    assert "T1" in deliverable
    assert "T1" not in dropped


def test_ac005_failed_sibling_unaffected():
    """AC-005: Failed task doesn't affect independent passing sibling."""
    tasks = [
        {"id": "TASK-1", "deps": [], "files": ["1.ts"], "kind": "impl"},
        {"id": "TASK-2", "deps": [], "files": ["2.ts"], "kind": "impl"},
        {"id": "TASK-3", "deps": ["TASK-1"], "files": ["3.ts"], "kind": "impl"},
    ]
    status = {"TASK-1": "failed", "TASK-2": "pending", "TASK-3": "pending"}
    deliverable, dropped = coordinator.deliverable_subset(
        ["TASK-1", "TASK-2", "TASK-3"], tasks, status
    )
    # Independent sibling unaffected by failure
    assert "TASK-2" in deliverable
    assert "TASK-1" in dropped
    assert "TASK-3" in dropped


def test_signature_unchanged():
    """Verify function signature and return shape are unchanged."""
    tasks = [{"id": "T1", "deps": [], "files": ["a.ts"], "kind": "impl"}]
    status = {"T1": "pending"}
    result = coordinator.deliverable_subset(["T1"], tasks, status)

    # Should be tuple of 2 elements
    assert isinstance(result, tuple)
    assert len(result) == 2

    # Both elements should be sorted lists
    deliverable, dropped = result
    assert isinstance(deliverable, list)
    assert isinstance(dropped, list)
    assert deliverable == sorted(deliverable)
    assert dropped == sorted(dropped)


def test_complex_dag_done_completed_and_failed():
    """Complex scenario: mix of DONE, ALREADY_INTEGRATED, FAILED, and PENDING."""
    tasks = [
        {"id": "T1", "deps": [], "files": ["1.ts"], "kind": "impl"},
        {"id": "T2", "deps": ["T1"], "files": ["2.ts"], "kind": "impl"},
        {"id": "T3", "deps": [], "files": ["3.ts"], "kind": "impl"},
        {"id": "T4", "deps": ["T3"], "files": ["4.ts"], "kind": "impl"},
        {"id": "T5", "deps": ["T2", "T4"], "files": ["5.ts"], "kind": "impl"},
        {"id": "T6", "deps": ["T0"], "files": ["6.ts"], "kind": "impl"},
        {"id": "T0", "deps": [], "files": ["0.ts"], "kind": "impl"},
    ]
    status = {
        "T1": "done",
        "T2": "pending",
        "T3": "failed",
        "T4": "pending",
        "T5": "pending",
        "T0": "completed",
        "T6": "pending",
    }
    deliverable, dropped = coordinator.deliverable_subset(
        ["T1", "T2", "T3", "T4", "T5", "T6", "T0"], tasks, status
    )

    # T1 is done -> ships; T2 depends on a shipped task -> ships
    assert "T1" in deliverable
    assert "T2" in deliverable

    # T3 is failed (dropped), T4 depends on failed (cascade-dropped)
    assert "T3" in dropped
    assert "T4" in dropped

    # T5 depends on T2 and T4; T4 is dropped so T5 is dropped
    assert "T5" in dropped

    # T0 is already integrated (dropped) but does NOT cascade-drop its dependent T6
    assert "T0" in dropped
    assert "T6" in deliverable


if __name__ == "__main__":
    test_ac001_done_tasks_are_deliverable()
    test_ac002_preompleted_absent()
    test_ac003_integrated_dependent_stays_deliverable()
    test_ac003_failed_dependent_dropped()
    test_ac004_uses_already_integrated_constant()
    test_ac005_failed_sibling_unaffected()
    test_signature_unchanged()
    test_complex_dag_done_completed_and_failed()
    print("All tests passed ✓")
