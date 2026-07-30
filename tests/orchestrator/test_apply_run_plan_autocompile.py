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

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))

from worktrail.orchestrator import live  # noqa: E402


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


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


def test_openspec_change_auto_compiles_without_a_manual_worktrail_compile_step(tmp_path):
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
    assert all(not t.get("files") for t in tasks), "OpenSpec tasks start with no file scope"

    spawn = RecordingSpawn(
        _reply(**{
            "1.1": {"files": ["src/parser.py"], "deps": []},
            "1.2": {"files": ["src/api.py"], "deps": ["1.1"]},
        })
    )

    merged = live.apply_run_plan(repo, spec_rel, spec_id, tasks, spawn=spawn)
    assert spawn.calls == 1, "a fresh OpenSpec change with no cached plan must auto-compile"
    by_id = {t["id"]: t for t in merged}
    assert by_id["1.1"]["files"] == ["src/parser.py"]
    assert by_id["1.2"]["files"] == ["src/api.py"]

    # A resumed/second run of the exact same content must hit the cache, not
    # spend a second model call.
    _, tasks_again = resolve.load_spec(str(change))
    live.apply_run_plan(repo, spec_rel, spec_id, tasks_again, spawn=spawn)
    assert spawn.calls == 1, "a re-run of the same content must reuse the cached plan"


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
