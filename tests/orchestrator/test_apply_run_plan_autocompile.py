#!/usr/bin/env python3
"""Regression test: `apply_run_plan` must compile a RunPlan automatically when
none is cached, instead of requiring a separate `worktrail-compile` step first.

Reproduces the incident from `~/.go/runs/datalena/go-20260730-133115.yaml`:
a first `full-real` launch against a fresh OpenSpec change used to hard-fail
with `RuntimeError: implementation task(s) missing required frontmatter files`
because `apply_run_plan` only ever read a cached plan and OpenSpec's
`tasks.md` never declares one. The fix makes `apply_run_plan` compile (and
cache) one automatically -- but only when the format actually needs it, so a
devkit spec (which already carries file scope in frontmatter) must never
reach the model.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(
    0, __import__("os").path.dirname(__import__("os").path.abspath(__file__))
)

from worktrail.orchestrator import live


def _git(repo, *args):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )


def _init_repo(path: Path) -> Path:
    repo = path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


class RecordingSpawn:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    def __call__(self, prompt, cwd, timeout, log):
        self.calls += 1
        return self.reply


def _reply(**per_task) -> str:
    rows = [{"id": k, **v} for k, v in per_task.items()]
    return "```json\n" + json.dumps({"tasks": rows}) + "\n```\n"


def test_openspec_change_auto_compiles_without_a_manual_worktrail_compile_step(
    tmp_path,
):
    repo = _init_repo(tmp_path)
    change = repo / "openspec" / "changes" / "add-thing"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("## Why\nBecause.\n")
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    from worktrail.taskformats import resolve

    spec_rel = str(change.relative_to(repo))
    spec_id, tasks = resolve.load_spec(str(change))
    assert all(not t.get("files") for t in tasks), (
        "OpenSpec tasks start with no file scope"
    )

    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
            }
        )
    )

    merged = live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1, (
        "a fresh OpenSpec change with no cached plan must auto-compile"
    )
    by_id = {t["id"]: t for t in merged}
    assert by_id["1.1"]["files"] == ["src/parser.py"]
    assert by_id["1.2"]["files"] == ["src/api.py"]

    # A resumed/second run of the exact same content must hit the cache, not
    # spend a second model call.
    _, tasks_again = resolve.load_spec(str(change))
    live.apply_run_plan(repo, spec_rel, spec_id, tasks_again, spawn=spawn)
    assert spawn.calls == 1, "a re-run of the same content must reuse the cached plan"


class RaisingSpawn:
    """A spawn that fails the test the moment it is invoked -- used to prove a
    pinned run never reaches the compile seam at all."""

    def __call__(self, prompt, cwd, timeout, log):
        raise AssertionError("spawn must not be called for a pinned run")


def test_pinned_run_reuses_cached_plan_without_calling_spawn(tmp_path):
    repo = _init_repo(tmp_path)
    change = repo / "openspec" / "changes" / "add-thing"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("## Why\nBecause.\n")
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    from worktrail.taskformats import resolve

    spec_rel = str(change.relative_to(repo))
    spec_id, tasks = resolve.load_spec(str(change))

    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
            }
        )
    )
    live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1, "the first call establishes the pin via a real compile"

    _, tasks_again = resolve.load_spec(str(change))
    second_spawn = RecordingSpawn(
        _reply(**{"1.1": {"files": []}, "1.2": {"files": []}})
    )
    merged = live.apply_run_plan(
        repo, spec_rel, spec_id, tasks_again, spawn=second_spawn
    )
    assert second_spawn.calls == 0, "a pinned run must never reach the compile seam"
    by_id = {t["id"]: t for t in merged}
    assert by_id["1.1"]["files"] == ["src/parser.py"]
    assert by_id["1.1"]["deps"] == []
    assert by_id["1.2"]["files"] == ["src/api.py"]
    assert by_id["1.2"]["deps"] == ["1.1"]


def test_pinned_run_reuses_plan_even_if_change_content_since_changed(tmp_path):
    repo = _init_repo(tmp_path)
    change = repo / "openspec" / "changes" / "add-thing"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("## Why\nBecause.\n")
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    from worktrail.taskformats import resolve

    spec_rel = str(change.relative_to(repo))
    spec_id, tasks = resolve.load_spec(str(change))

    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
            }
        )
    )
    live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1

    # Content changes (would recompute a different fingerprint) but the task
    # set (ids) is unchanged -- the pin must still resolve without compiling.
    (change / "proposal.md").write_text("## Why\nBecause, revised.\n")
    _, tasks_again = resolve.load_spec(str(change))
    merged = live.apply_run_plan(
        repo, spec_rel, spec_id, tasks_again, spawn=RaisingSpawn()
    )
    by_id = {t["id"]: t for t in merged}
    assert by_id["1.1"]["files"] == ["src/parser.py"]
    assert by_id["1.2"]["files"] == ["src/api.py"]


def test_run_with_no_pin_compiles_and_records_matching_fingerprint(tmp_path):
    repo = _init_repo(tmp_path)
    change = repo / "openspec" / "changes" / "add-thing"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("## Why\nBecause.\n")
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    from worktrail.taskformats import resolve

    spec_rel = str(change.relative_to(repo))
    spec_id, tasks = resolve.load_spec(str(change))

    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
            }
        )
    )
    merged = live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1

    from worktrail.conductor import runplan as conductor_runplan

    fp = conductor_runplan.fingerprint(change, tasks)
    journal = json.loads(live.journal_path_for(repo, spec_rel).read_text())
    assert journal["plan_fingerprint"] == fp
    by_id = {t["id"]: t for t in merged}
    assert by_id["1.1"]["files"] == ["src/parser.py"]


def test_unresolvable_pin_raises_and_never_compiles(tmp_path):
    repo = _init_repo(tmp_path)
    change = repo / "openspec" / "changes" / "add-thing"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("## Why\nBecause.\n")
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    from worktrail.conductor import compile as conductor_compile
    from worktrail.taskformats import resolve

    spec_rel = str(change.relative_to(repo))
    spec_id, tasks = resolve.load_spec(str(change))

    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
            }
        )
    )
    live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1

    journal_path = live.journal_path_for(repo, spec_rel)
    pinned_fp = json.loads(journal_path.read_text())["plan_fingerprint"]
    cache_dir = conductor_compile.default_cache_dir(repo)
    for f in cache_dir.iterdir():
        f.unlink()

    _, tasks_again = resolve.load_spec(str(change))
    try:
        live.apply_run_plan(repo, spec_rel, spec_id, tasks_again, spawn=RaisingSpawn())
        assert False, "expected a RuntimeError for an unresolvable pin"
    except RuntimeError as exc:
        msg = str(exc)
        assert spec_id in msg
        assert pinned_fp[:12] in msg
        assert str(journal_path) in msg


def test_pinned_plan_with_task_set_drift_raises_instead_of_falling_back(tmp_path):
    """Reproduces brief 20260816-214009: a pinned plan that still resolves from the
    cache, but whose task ids no longer match the artifact's current task set (the
    artifact was edited between phases/resumes), used to fall through to
    `runplan.apply_to_tasks()`'s own drift-rejection branch -- which silently
    discards the pinned plan for the *whole* run and falls back to each task's own
    baseline deps/file-scope. For OpenSpec tasks (no native file scope) that starves
    every task of both a file scope and a dependency edge, which
    `validate_task_metadata` then refuses several frames later with an
    unrelated-looking "missing required frontmatter files" error. The fix makes
    `apply_run_plan` detect the mismatch itself and fail the same way an
    unresolvable pin already does, before ever reaching `apply_to_tasks`."""
    repo = _init_repo(tmp_path)
    change = repo / "openspec" / "changes" / "add-thing"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("## Why\nBecause.\n")
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    from worktrail.taskformats import resolve

    spec_rel = str(change.relative_to(repo))
    spec_id, tasks = resolve.load_spec(str(change))

    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
            }
        )
    )
    live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1, "the first call establishes the pin via a real compile"

    journal_path = live.journal_path_for(repo, spec_rel)
    pinned_fp = json.loads(journal_path.read_text())["plan_fingerprint"]

    # Artifact drift: a new task lands between phases, so the current task-id set
    # no longer matches the pinned plan's task-id set.
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
        "- [ ] 1.3 Add a third task\n"
    )
    _, tasks_drifted = resolve.load_spec(str(change))

    try:
        live.apply_run_plan(
            repo, spec_rel, spec_id, tasks_drifted, spawn=RaisingSpawn()
        )
        assert False, "expected a RuntimeError for a pinned plan with task-set drift"
    except RuntimeError as exc:
        msg = str(exc)
        assert spec_id in msg
        assert pinned_fp[:12] in msg
        assert str(journal_path) in msg
        assert "1.3" in msg, "the missing/unknown ids should be named in the error"


def test_unreadable_journal_is_treated_as_no_pin(tmp_path):
    repo = _init_repo(tmp_path)
    change = repo / "openspec" / "changes" / "add-thing"
    change.mkdir(parents=True)
    (change / "proposal.md").write_text("## Why\nBecause.\n")
    (change / "tasks.md").write_text(
        "## 1. Core\n\n- [ ] 1.1 Add the parser\n- [ ] 1.2 Wire the endpoint\n"
    )

    from worktrail.taskformats import resolve

    spec_rel = str(change.relative_to(repo))
    spec_id, tasks = resolve.load_spec(str(change))

    journal_path = live.journal_path_for(repo, spec_rel)
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text("{ this is not json")

    spawn = RecordingSpawn(
        _reply(
            **{
                "1.1": {"files": ["src/parser.py"], "deps": []},
                "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
            }
        )
    )
    merged = live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1, (
        "a malformed journal must be treated as no pin, not an error"
    )
    by_id = {t["id"]: t for t in merged}
    assert by_id["1.1"]["files"] == ["src/parser.py"]


def test_devkit_spec_never_reaches_the_model(tmp_path):
    """A devkit spec already declares file scope in its own frontmatter --
    `needs_compile` sees no gaps, so `compile_run_plan` must take the free
    seed path. `spawn` must never be invoked; a call would fail the test by
    itself since RecordingSpawn's reply is nonsense for this task set."""
    repo = _init_repo(tmp_path)
    tasks = [
        {
            "id": "TASK-001",
            "title": "t",
            "status": "pending",
            "deps": [],
            "files": ["a.py"],
            "kind": "impl",
            "path": "tasks/TASK-001.md",
        }
    ]
    spawn = RecordingSpawn("should never be called")

    merged = live.apply_run_plan(repo, "specs/001-x", "001-x", tasks, spawn=spawn)
    assert spawn.calls == 0
    assert merged[0]["files"] == ["a.py"]
