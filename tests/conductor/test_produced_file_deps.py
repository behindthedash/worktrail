"""Ordering a task after the task that CREATES the file it names.

Brief 20260918-082155. The devops change render-fleet-health-monitor (run
go-20260918-073805, 2026-09-18) compiled `source=seed` with 3.1 and 4.1
carrying `deps=-`, although 3.1 writes tests for the script 2.2 creates and
4.1 registers the files 2.1/2.2 create. The fan-out ran them in parallel: 3.1
reported the script "absent from this worktree" and failed, 3.2/5.1/5.2
blocked behind it, and 4.1 opened PR #484 whose Lint, Test & Build failed on
MANAGED_SCRIPTS entries with no file.

`import_dep_edges` cannot close this: it reads imports already on disk, and a
file a sibling task has not created yet has nothing to parse. Nor does the
shared-`files:` collision check, since the two tasks declare disjoint files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from worktrail.conductor import compile as compile_mod
from worktrail.conductor.import_deps import produced_file_dep_edges


def _prose(task: dict) -> str:
    return str(task.get("text") or "")


def _task(
    tid: str,
    *files: str,
    text: str = "",
    deps: list[str] | None = None,
    kind: str = "",
) -> dict:
    return {
        "id": tid,
        "files": list(files),
        "text": text,
        "deps": deps or [],
        "kind": kind,
    }


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    return r


def test_the_devops_repro(repo: Path) -> None:
    tasks = [
        _task("2.2", "scripts/render-fleet-health-monitor.py", text="Write the script"),
        _task(
            "3.1",
            "tests/test_render_fleet_health_monitor.py",
            text="Add tests for scripts/render-fleet-health-monitor.py",
        ),
    ]
    edges, warnings = produced_file_dep_edges(tasks, repo, _prose)
    assert edges == {"3.1": ["2.2"]}
    assert warnings == []


def test_multiple_producers_all_become_deps(repo: Path) -> None:
    tasks = [
        _task("2.1", "scripts/config.yaml"),
        _task("2.2", "scripts/monitor.py"),
        _task(
            "4.1",
            "scripts/bin_sync.py",
            text="Register scripts/monitor.py and scripts/config.yaml in MANAGED_SCRIPTS",
        ),
    ]
    edges, _ = produced_file_dep_edges(tasks, repo, _prose)
    assert sorted(edges["4.1"]) == ["2.1", "2.2"]


def test_an_existing_file_is_not_a_produce_consume_edge(repo: Path) -> None:
    """A file already on disk is a modification: import inference and the
    shared-`files:` check own that case, and merely mentioning an existing
    path in prose is not evidence of an ordering."""
    (repo / "scripts").mkdir()
    (repo / "scripts" / "monitor.py").write_text("x = 1\n")
    tasks = [
        _task("2.2", "scripts/monitor.py"),
        _task("3.1", "tests/test_monitor.py", text="Add tests for scripts/monitor.py"),
    ]
    assert produced_file_dep_edges(tasks, repo, _prose)[0] == {}


def test_a_task_that_declares_the_path_itself_gets_no_edge(repo: Path) -> None:
    """Two tasks declaring the same file collide on it, and the planner
    already groups them -- adding an edge here would double-order them."""
    tasks = [
        _task("2.2", "scripts/monitor.py"),
        _task("3.1", "scripts/monitor.py", text="Extend scripts/monitor.py"),
    ]
    assert produced_file_dep_edges(tasks, repo, _prose)[0] == {}


def test_a_partial_path_match_is_not_an_edge(repo: Path) -> None:
    tasks = [
        _task("2.2", "scripts/run.py"),
        _task(
            "3.1",
            "a.py",
            text="Touch tools/scripts/run.py and scripts/run.py.bak, not the other one",
        ),
    ]
    assert produced_file_dep_edges(tasks, repo, _prose)[0] == {}


def test_a_leading_dot_slash_in_prose_still_matches(repo: Path) -> None:
    tasks = [
        _task("2.2", "scripts/run.py"),
        _task("3.1", "a.py", text="Invoke ./scripts/run.py from the hook"),
    ]
    assert produced_file_dep_edges(tasks, repo, _prose)[0] == {"3.1": ["2.2"]}


def test_tail_tasks_are_excluded(repo: Path) -> None:
    tasks = [
        _task("2.2", "scripts/run.py"),
        _task("9.1", "e2e.py", text="Run scripts/run.py end to end", kind="e2e"),
    ]
    assert produced_file_dep_edges(tasks, repo, _prose)[0] == {}


def test_an_existing_authored_dep_is_not_duplicated(repo: Path) -> None:
    tasks = [
        _task("2.2", "scripts/run.py"),
        _task("3.1", "a.py", text="Test scripts/run.py", deps=["2.2"]),
    ]
    assert produced_file_dep_edges(tasks, repo, _prose)[0] == {}


def test_a_cycle_is_refused_with_a_warning(repo: Path) -> None:
    tasks = [
        _task("2.2", "scripts/run.py", deps=["3.1"], text="Create it"),
        _task("3.1", "a.py", text="Test scripts/run.py"),
    ]
    edges, warnings = produced_file_dep_edges(tasks, repo, _prose)
    assert edges == {}
    assert len(warnings) == 1
    assert "already depends on" in warnings[0]


def test_a_path_escaping_the_repo_is_ignored(repo: Path) -> None:
    tasks = [
        _task("2.2", "../outside/run.py"),
        _task("3.1", "a.py", text="Test ../outside/run.py"),
    ]
    assert produced_file_dep_edges(tasks, repo, _prose)[0] == {}


def test_seed_plan_carries_the_inferred_edge(repo: Path) -> None:
    """End to end through `_plan_from_tasks`, which is what the `source=seed`
    path in the repro actually built."""
    tasks = [
        _task("2.2", "scripts/monitor.py", text="Write the monitor"),
        _task(
            "3.1",
            "tests/test_monitor.py",
            text="Add tests for scripts/monitor.py",
        ),
    ]
    plan, _warnings = compile_mod._plan_from_tasks(
        "render-fleet-health-monitor", "fp", tasks, compile_mod.SOURCE_SEED, repo
    )
    by_id = {t.id: t for t in plan.tasks}
    assert by_id["3.1"].deps == ("2.2",)
    assert by_id["2.2"].deps == ()
