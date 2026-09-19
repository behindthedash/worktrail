"""`review_names_decision` and the round-2 `decision_required` reviewer contract."""

from __future__ import annotations

from worktrail.orchestrator import dispatch


def _ctx():
    return {
        "spec_id": "004-test",
        "spec_folder": "docs/specs/004-test/",
        "worktree_path": "/tmp/wt/004-test",
        "branch": "task/TASK-001",
        "base_commit": "abc1234",
    }


def _task(retry_count: int) -> dict:
    return {
        "id": "TASK-001",
        "title": "Do the thing",
        "retry_count": retry_count,
        "review_critical_issues": 1,
        "review_major_issues": 0,
        "review_notes": "AC contradicts test_x.",
    }


def test_structured_field_wins_over_notes():
    rep = {
        "decision_required": "AC 2 vs test_repo_guard: pick one",
        "notes": "planner/human decision needed elsewhere",
    }
    assert dispatch.review_names_decision(rep) == "AC 2 vs test_repo_guard: pick one"


def test_phrase_in_notes_fallback_case_insensitive():
    for phrase in ("Planner/Human Decision", "HUMAN decision", "planner Decision"):
        rep = {"decision_required": None, "notes": f"Needs a {phrase} on AC 2."}
        assert dispatch.review_names_decision(rep) == f"Needs a {phrase} on AC 2."
    assert (
        dispatch.review_names_decision(
            {"decision_required": "  ", "notes": "x human decision"}
        )
        == "x human decision"
    )


def test_ordinary_notes_return_none():
    assert dispatch.review_names_decision({"notes": "Two lint nits, fixed."}) is None
    assert dispatch.review_names_decision({"notes": ""}) is None
    assert dispatch.review_names_decision({}) is None


def test_round2_prompt_names_decision_required_round1_does_not():
    ctx = _ctx()
    p1 = dispatch.build_worker_prompt(dispatch.ROLE_REVIEW, _task(0), ctx)
    p2 = dispatch.build_worker_prompt(dispatch.ROLE_REVIEW, _task(1), ctx)
    # Round 1: unchanged -- the field is not mentioned anywhere.
    assert "decision_required" not in p1
    assert "review round" not in p1
    # Round 2: schema line present and reviewer told when (and only when) to set it.
    assert '"decision_required": "<text>|null"' in p2
    assert "This is review round 2." in p2
    assert "Set `decision_required`" in p2
    assert "Still Present" in p2
    assert "cite the AC and the conflicting test/behaviour" in p2
    # Non-review roles never see the field.
    p_impl = dispatch.build_worker_prompt(dispatch.ROLE_IMPLEMENT, _task(0), ctx)
    assert "decision_required" not in p_impl
