"""Tests for the post-hoc declared-vs-actual RunPlan audit.

Real git repos with real branches, not mocks: the whole point of this module
is diffing what a task's branch actually touched, so a fake diff would test
nothing. `disjoint_batches`/`runnable_frontier` already prove the collision
check works correctly *given accurate `files`* (see `test_compile.py`'s
end-to-end test) -- this module is about catching it when `files` was wrong
in the first place, after the fact.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from worktrail.conductor import plan_audit, runplan
from worktrail.orchestrator import worktree


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "README.md").write_text("hello\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "branch", "-M", "main")
    return repo


def _make_task_branch(repo: Path, spec_id: str, task_id: str, *files: str) -> None:
    branch = worktree.task_branch(spec_id, task_id)
    _git(repo, "checkout", "-q", "-b", branch, "main")
    for f in files:
        p = repo / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"content for {f}\n")
        _git(repo, "add", f)
    _git(repo, "commit", "-q", "-m", f"{task_id}: touch {','.join(files)}")
    _git(repo, "checkout", "-q", "main")


def _plan(spec_id: str, **files_by_task: list) -> runplan.RunPlan:
    return runplan.RunPlan(
        spec_id=spec_id,
        fingerprint="deadbeef",
        source=runplan.SOURCE_COMPILED,
        tasks=tuple(
            runplan.TaskPlan(id=tid, files=tuple(files)) for tid, files in files_by_task.items()
        ),
    )


def test_a_task_that_touched_exactly_what_it_declared_is_not_flagged(tmp_path):
    repo = _repo(tmp_path)
    _make_task_branch(repo, "spec-1", "1.1", "src/a.py")
    plan = _plan("spec-1", **{"1.1": ["src/a.py"]})
    assert plan_audit.audit_plan(repo, "spec-1", plan, "main") == []


def test_an_undeclared_file_the_task_actually_touched_is_flagged(tmp_path):
    """The exact go-20260730-133115 failure shape: the model recorded a file on
    only one task, but the other task's branch touches it too."""
    repo = _repo(tmp_path)
    _make_task_branch(repo, "spec-1", "1.2", "api/security/approval_visibility.py")
    plan = _plan("spec-1", **{"1.2": ["api/security/other_file.py"]})
    mismatches = plan_audit.audit_plan(repo, "spec-1", plan, "main")
    assert len(mismatches) == 1
    m = mismatches[0]
    assert m.task_id == "1.2"
    assert m.undeclared == ("api/security/approval_visibility.py",)
    assert m.unused == ("api/security/other_file.py",)


def test_a_declared_file_that_was_never_touched_is_flagged_as_unused(tmp_path):
    repo = _repo(tmp_path)
    _make_task_branch(repo, "spec-1", "1.1", "src/a.py")
    plan = _plan("spec-1", **{"1.1": ["src/a.py", "src/b.py"]})
    mismatches = plan_audit.audit_plan(repo, "spec-1", plan, "main")
    assert len(mismatches) == 1
    assert mismatches[0].undeclared == ()
    assert mismatches[0].unused == ("src/b.py",)


def test_a_task_with_no_declared_files_is_skipped_not_flagged(tmp_path):
    """An empty `files` list already means 'unknown' to `apply_to_tasks` -- the
    audit has nothing to compare it against, so it is silent, not a mismatch."""
    repo = _repo(tmp_path)
    _make_task_branch(repo, "spec-1", "1.1", "src/a.py")
    plan = _plan("spec-1", **{"1.1": []})
    assert plan_audit.audit_plan(repo, "spec-1", plan, "main") == []


def test_a_task_whose_branch_no_longer_exists_is_skipped_not_flagged(tmp_path):
    """Best-effort and post-hoc: a torn-down or never-dispatched branch is
    nothing left to check, not a failure to report."""
    repo = _repo(tmp_path)
    plan = _plan("spec-1", **{"9.9": ["src/never-ran.py"]})
    assert plan_audit.audit_plan(repo, "spec-1", plan, "main") == []


def test_multiple_tasks_are_each_checked_independently(tmp_path):
    repo = _repo(tmp_path)
    _make_task_branch(repo, "spec-1", "1.1", "src/a.py")
    _make_task_branch(repo, "spec-1", "1.2", "src/a.py", "src/b.py")
    plan = _plan("spec-1", **{"1.1": ["src/a.py"], "1.2": ["src/a.py"]})
    mismatches = plan_audit.audit_plan(repo, "spec-1", plan, "main")
    assert {m.task_id for m in mismatches} == {"1.2"}
    assert mismatches[0].undeclared == ("src/b.py",)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def test_the_cli_reports_no_cached_plan_when_compile_never_ran(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")

    rc = plan_audit.main([str(d), "main"])
    assert rc == 1
    assert "no cached plan" in capsys.readouterr().err


def test_the_cli_reports_a_clean_run_with_no_mismatches(tmp_path, capsys):
    from worktrail.conductor import compile as conductor_compile
    from worktrail.taskformats import resolve

    repo = _repo(tmp_path)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")
    _git(repo, "add", "openspec")
    _git(repo, "commit", "-q", "-m", "add change")

    spec_id, tasks = resolve.load_spec(str(d))

    def spawn(prompt, cwd, timeout, log):
        import json

        return "```json\n" + json.dumps({"tasks": [{"id": "1.1", "files": ["src/a.py"], "deps": []}]}) + "\n```\n"

    conductor_compile.compile_run_plan(d, tasks, spec_id=spec_id, repo=repo, spawn=spawn)
    _make_task_branch(repo, spec_id, "1.1", "src/a.py")

    rc = plan_audit.main([str(d), "main"])
    assert rc == 0
    assert "no mismatches" in capsys.readouterr().out


def test_the_cli_json_output_reports_a_mismatch(tmp_path, capsys):
    from worktrail.conductor import compile as conductor_compile
    from worktrail.taskformats import resolve

    repo = _repo(tmp_path)
    d = repo / "openspec" / "changes" / "add-thing"
    d.mkdir(parents=True)
    (d / "proposal.md").write_text("## Why\nBecause.\n")
    (d / "tasks.md").write_text("## 1. Core\n\n- [ ] 1.1 Add the parser\n")
    _git(repo, "add", "openspec")
    _git(repo, "commit", "-q", "-m", "add change")

    spec_id, tasks = resolve.load_spec(str(d))

    def spawn(prompt, cwd, timeout, log):
        import json

        return "```json\n" + json.dumps({"tasks": [{"id": "1.1", "files": ["src/a.py"], "deps": []}]}) + "\n```\n"

    conductor_compile.compile_run_plan(d, tasks, spec_id=spec_id, repo=repo, spawn=spawn)
    _make_task_branch(repo, spec_id, "1.1", "src/a.py", "src/unexpected.py")

    rc = plan_audit.main([str(d), "main", "--json"])
    assert rc == 0
    import json as jsonlib

    out = jsonlib.loads(capsys.readouterr().out)
    assert out[0]["task_id"] == "1.1"
    assert out[0]["undeclared"] == ["src/unexpected.py"]
