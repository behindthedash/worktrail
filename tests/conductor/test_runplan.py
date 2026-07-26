"""Tests for the compiled RunPlan: fingerprinting, caching, and safe application.

The application tests carry the weight here. A RunPlan is partly LLM-inferred,
so `apply_to_tasks` is the boundary where an inference either earns its way into
the run's concurrency decisions or gets discarded. Each test below names the
specific way a bad plan could make a run less safe than no plan at all.
"""

from __future__ import annotations

import json
from pathlib import Path

from worktrail.conductor import runplan
from worktrail.conductor.runplan import RunPlan, TaskPlan


def _task(tid, *, deps=(), files=(), kind="impl", title="", status="pending", path="tasks.md"):
    return {
        "id": tid,
        "title": title or f"task {tid}",
        "status": status,
        "deps": list(deps),
        "files": list(files),
        "kind": kind,
        "path": path,
    }


def _plan(*task_plans, spec_id="chg", fp="f" * 64, source=runplan.SOURCE_COMPILED):
    return RunPlan(spec_id=spec_id, fingerprint=fp, source=source, tasks=tuple(task_plans))


# --------------------------------------------------------------------------- #
# Fingerprinting
# --------------------------------------------------------------------------- #
def _change_dir(tmp_path: Path, proposal: str = "why\n") -> Path:
    d = tmp_path / "openspec" / "changes" / "chg"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text(proposal)
    (d / "tasks.md").write_text("## 1. G\n\n- [ ] 1.1 a\n- [ ] 1.2 b\n")
    return d


def test_fingerprint_ignores_status_so_a_resume_still_hits_the_cache(tmp_path):
    """The cache exists to make resumes free. A fingerprint that moved when a
    task completed would miss on exactly the run it was built for."""
    d = _change_dir(tmp_path)
    pending = [_task("1.1"), _task("1.2")]
    done = [_task("1.1", status="completed"), _task("1.2")]
    assert runplan.fingerprint(d, pending) == runplan.fingerprint(d, done)


def test_fingerprint_tracks_supporting_artifacts(tmp_path):
    """proposal.md is compile input, so editing it must invalidate the plan."""
    d = _change_dir(tmp_path)
    tasks = [_task("1.1"), _task("1.2")]
    before = runplan.fingerprint(d, tasks)
    (d / "proposal.md").write_text("why, revised\n")
    assert runplan.fingerprint(d, tasks) != before


def test_fingerprint_tracks_planning_fields_not_just_titles(tmp_path):
    d = _change_dir(tmp_path)
    base = runplan.fingerprint(d, [_task("1.1"), _task("1.2")])
    assert runplan.fingerprint(d, [_task("1.1", deps=["1.2"]), _task("1.2")]) != base
    assert runplan.fingerprint(d, [_task("1.1", files=["a.py"]), _task("1.2")]) != base
    assert runplan.fingerprint(d, [_task("1.1", kind="e2e"), _task("1.2")]) != base


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def test_cache_round_trip(tmp_path):
    plan = _plan(TaskPlan(id="1.1", files=("a.py",)), fp="abc123" + "0" * 58)
    runplan.store(tmp_path, plan)
    got = runplan.load_cached(tmp_path, "chg", plan.fingerprint)
    assert got is not None and got.tasks[0].files == ("a.py",)


def test_cache_miss_on_a_different_fingerprint(tmp_path):
    runplan.store(tmp_path, _plan(TaskPlan(id="1.1"), fp="a" * 64))
    assert runplan.load_cached(tmp_path, "chg", "b" * 64) is None


def test_cache_rejects_a_plan_written_by_another_plan_version(tmp_path):
    """An older worktrail's plan may mean something different under today's
    application rules. Treat it as absent rather than reinterpreting it."""
    plan = _plan(TaskPlan(id="1.1"), fp="c" * 64)
    p = runplan.store(tmp_path, plan)
    d = json.loads(p.read_text())
    d["plan_version"] = runplan.PLAN_VERSION - 1
    p.write_text(json.dumps(d))
    assert runplan.load_cached(tmp_path, "chg", plan.fingerprint) is None


def test_cache_survives_a_corrupt_file(tmp_path):
    plan = _plan(TaskPlan(id="1.1"), fp="d" * 64)
    runplan.cache_path(tmp_path, "chg", plan.fingerprint).parent.mkdir(parents=True, exist_ok=True)
    runplan.cache_path(tmp_path, "chg", plan.fingerprint).write_text("{not json")
    assert runplan.load_cached(tmp_path, "chg", plan.fingerprint) is None


# --------------------------------------------------------------------------- #
# Application -- the safety invariant
# --------------------------------------------------------------------------- #
def test_an_edge_is_dropped_when_both_ends_declare_file_scope():
    """The point of compiling: conservative sequential deps give way to real
    parallelism, because `runnable_frontier` can now see the two are disjoint."""
    tasks = [_task("1.1"), _task("1.2", deps=["1.1"])]
    plan = _plan(
        TaskPlan(id="1.1", files=("src/a.py",)),
        TaskPlan(id="1.2", files=("src/b.py",)),
    )
    merged, notes = runplan.apply_to_tasks(tasks, plan)
    assert merged[1]["deps"] == []
    assert merged[1]["files"] == ["src/b.py"]
    assert any("1 loosened" in n for n in notes)


def test_an_edge_is_restored_when_the_dependent_has_no_file_scope():
    """`runnable_frontier` reads an empty file set as "collides with nothing".
    Decoupling an unscoped task would let it race with unbounded blast radius,
    so the plan does not get to drop the edge it did not pay for."""
    tasks = [_task("1.1"), _task("1.2", deps=["1.1"])]
    plan = _plan(
        TaskPlan(id="1.1", files=("src/a.py",)),
        TaskPlan(id="1.2", files=()),  # model declined to guess
    )
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert merged[1]["deps"] == ["1.1"]


def test_an_edge_is_restored_when_the_dependency_has_no_file_scope():
    """Symmetric case: the *dependency* is the unscoped one, so it is the
    invisible party in the collision check."""
    tasks = [_task("1.1"), _task("1.2", deps=["1.1"])]
    plan = _plan(
        TaskPlan(id="1.1", files=()),
        TaskPlan(id="1.2", files=("src/b.py",)),
    )
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert merged[1]["deps"] == ["1.1"]


def test_a_plan_with_no_file_scope_at_all_is_exactly_the_baseline():
    """Degenerate case worth pinning: an empty compile is a no-op, never a
    loosening."""
    tasks = [_task("1.1"), _task("1.2", deps=["1.1"]), _task("1.3", deps=["1.2"])]
    plan = _plan(*(TaskPlan(id=t["id"]) for t in tasks))
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert [t["deps"] for t in merged] == [[], ["1.1"], ["1.2"]]


def test_the_plan_may_add_an_edge_the_artifact_did_not_have():
    """Tightening is always allowed -- it can only serialise, never race."""
    tasks = [_task("1.1", files=["a.py"]), _task("1.2", files=["b.py"])]
    plan = _plan(
        TaskPlan(id="1.1", files=("a.py",)),
        TaskPlan(id="1.2", files=("b.py",), deps=("1.1",)),
    )
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert merged[1]["deps"] == ["1.1"]


def test_an_invented_dependency_id_is_dropped_not_carried():
    """A dep on an id that does not exist would never be satisfied, parking the
    task forever. Silent deadlock is the worst available failure mode."""
    tasks = [_task("1.1", files=["a.py"]), _task("1.2", files=["b.py"])]
    plan = _plan(
        TaskPlan(id="1.1", files=("a.py",)),
        TaskPlan(id="1.2", files=("b.py",), deps=("9.9",)),
    )
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert merged[1]["deps"] == []


def test_a_tail_task_cannot_be_demoted_by_the_plan():
    """`kind` gates the fan-out holdout. If a plan could reclassify an e2e task
    as impl, the end-to-end test would run beside the work it verifies."""
    tasks = [_task("1.1", files=["a.py"]), _task("2.1", kind="e2e", files=["t.py"])]
    plan = _plan(
        TaskPlan(id="1.1", files=("a.py",), kind="impl"),
        TaskPlan(id="2.1", files=("t.py",), kind="impl"),
    )
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert merged[1]["kind"] == "e2e"


def test_a_drifted_task_set_rejects_the_whole_plan():
    """The artifact changed under a stale plan. Its file scopes were inferred
    against a task list that no longer exists, so none of it is trustworthy."""
    tasks = [_task("1.1"), _task("1.2", deps=["1.1"])]
    plan = _plan(TaskPlan(id="1.1", files=("a.py",)))  # 1.2 missing
    merged, notes = runplan.apply_to_tasks(tasks, plan)
    assert merged[1]["deps"] == ["1.1"] and merged[0]["files"] == []
    assert any("task set drifted" in n for n in notes)


def test_a_cycle_created_by_merging_rejects_the_plan():
    """Each graph is acyclic alone; their union need not be. Left unchecked
    this deadlocks the frontier instead of raising anywhere useful."""
    tasks = [_task("1.1", deps=["1.2"]), _task("1.2")]
    plan = _plan(
        TaskPlan(id="1.1", files=()),  # unscoped -> baseline edge 1.1->1.2 restored
        TaskPlan(id="1.2", files=(), deps=("1.1",)),  # plan asserts the reverse
    )
    merged, notes = runplan.apply_to_tasks(tasks, plan)
    assert merged == tasks
    assert any("cycle" in n for n in notes)


def test_complexity_and_review_fill_but_never_override():
    tasks = [_task("1.1", files=["a.py"]), _task("1.2", files=["b.py"])]
    tasks[0]["review"] = "deep"
    plan = _plan(
        TaskPlan(id="1.1", files=("a.py",), review="light", complexity="low"),
        TaskPlan(id="1.2", files=("b.py",), review="light", complexity="high"),
    )
    merged, _ = runplan.apply_to_tasks(tasks, plan)
    assert merged[0]["review"] == "deep"  # authored value wins
    assert merged[0]["complexity"] == "low"  # absent -> filled
    assert merged[1]["review"] == "light"


def test_apply_never_mutates_the_caller_list():
    tasks = [_task("1.1"), _task("1.2", deps=["1.1"])]
    before = [dict(t) for t in tasks]
    runplan.apply_to_tasks(tasks, _plan(TaskPlan(id="1.1", files=("a.py",)), TaskPlan(id="1.2", files=("b.py",))))
    assert tasks == before


def test_no_plan_is_a_no_op():
    tasks = [_task("1.1"), _task("1.2", deps=["1.1"])]
    merged, notes = runplan.apply_to_tasks(tasks, None)
    assert merged == tasks and notes == []
