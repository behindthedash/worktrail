"""Hermetic recovery engine tests."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from worktrail.orchestrator import recover, worktree


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    )
    return result.stdout.strip()


def setup_repo(tmp_path: Path) -> tuple[Path, Path, str, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "dev")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test")
    (repo / "base.txt").write_text("base\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    spec = "openspec/changes/recovery"
    spec_id = "recovery"
    journal_path = repo.parent / "repo-worktrees" / "run-recovery.json"
    journal_path.parent.mkdir()
    return repo, journal_path, spec, spec_id


def failed(task: str) -> dict:
    return {
        "task": task,
        "role": "drive",
        "report": {"terminal_status": "failed"},
    }


def write_journal(path: Path, *, tasks: list[str], groups: dict | None = None) -> None:
    path.write_text(
        json.dumps(
            {"entries": [failed(task) for task in tasks], "groups": groups or {}},
            indent=2,
        )
        + "\n"
    )


def test_stale_retained_branch_without_checkout_is_merged_and_cleared(tmp_path, capsys):
    repo, journal, spec, spec_id = setup_repo(tmp_path)
    branch = worktree.task_branch(spec_id, "1.1")
    git(repo, "branch", branch)
    git(repo, "switch", branch)
    (repo / "task.txt").write_text("task\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "task")
    git(repo, "switch", "dev")
    (repo / "base.txt").write_text("new base\n")
    git(repo, "commit", "-am", "advance base")
    write_journal(journal, tasks=["1.1"])

    assert recover.recover(repo, spec, [], ["1.1"], remote="origin") == 0
    assert git(repo, "merge-base", "--is-ancestor", "dev", branch) == ""
    assert json.loads(journal.read_text())["entries"] == []
    assert "merged" in capsys.readouterr().out


def test_up_to_date_branch_has_no_merge_commit(tmp_path, capsys):
    repo, journal, spec, spec_id = setup_repo(tmp_path)
    branch = worktree.task_branch(spec_id, "1.1")
    git(repo, "branch", branch)
    before = git(repo, "rev-parse", branch)
    write_journal(journal, tasks=["1.1"])

    assert recover.recover(repo, spec, [], ["1.1"], remote="origin") == 0
    assert git(repo, "rev-parse", branch) == before
    assert "already current" in capsys.readouterr().out


def test_conflicting_repair_reports_exact_path_and_preserves_journal(tmp_path, capsys):
    repo, journal, spec, spec_id = setup_repo(tmp_path)
    branch = worktree.task_branch(spec_id, "1.1")
    git(repo, "branch", branch)
    git(repo, "switch", branch)
    (repo / "base.txt").write_text("branch\n")
    git(repo, "commit", "-am", "branch change")
    git(repo, "switch", "dev")
    (repo / "base.txt").write_text("base change\n")
    git(repo, "commit", "-am", "base change")
    write_journal(journal, tasks=["1.1"])
    before = journal.read_text()

    assert recover.recover(repo, spec, [], ["1.1"], remote="origin") == 1
    assert journal.read_text() == before
    assert "base.txt" in capsys.readouterr().out


def test_group_resolution_requires_cached_runplan(tmp_path):
    repo, journal, spec, _ = setup_repo(tmp_path)
    write_journal(journal, tasks=["1.1"], groups={"g": {"state": "QUARANTINED"}})
    before = journal.read_text()
    assert recover.recover(repo, spec, ["g"], [], remote="origin") == 1
    assert journal.read_text() == before


def test_group_resolves_tasks_from_cached_runplan_and_clears_group(tmp_path):
    repo, journal, spec, spec_id = setup_repo(tmp_path)
    branch = worktree.task_branch(spec_id, "1.1")
    git(repo, "branch", branch)
    write_journal(
        journal, tasks=["1.1"], groups={"feature-1": {"state": "QUARANTINED"}}
    )
    runplans = repo.parent / "repo-worktrees" / "runplans"
    runplans.mkdir()
    (runplans / "recovery-test.json").write_text(
        json.dumps(
            {
                "tasks": [
                    {"id": "1.1", "deps": [], "files": ["a.py"], "status": "pending"}
                ]
            }
        )
    )

    assert recover.recover(repo, spec, ["feature-1"], [], remote="origin") == 0
    after = json.loads(journal.read_text())
    assert after["entries"] == []
    assert after["groups"] == {}
    assert after["resumed_quarantines"][0]["group"] == "feature-1"


def test_dry_run_neither_merges_nor_writes(tmp_path):
    repo, journal, spec, spec_id = setup_repo(tmp_path)
    branch = worktree.task_branch(spec_id, "1.1")
    git(repo, "branch", branch)
    git(repo, "switch", branch)
    (repo / "task.txt").write_text("task\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "task")
    git(repo, "switch", "dev")
    (repo / "base.txt").write_text("new base\n")
    git(repo, "commit", "-am", "advance base")
    write_journal(journal, tasks=["1.1"])
    before_journal = journal.read_text()
    before_branch = git(repo, "rev-parse", branch)

    assert recover.recover(repo, spec, [], ["1.1"], remote="origin", dry_run=True) == 0
    assert journal.read_text() == before_journal
    assert git(repo, "rev-parse", branch) == before_branch


def test_missing_retained_branch_is_reported_and_skipped(tmp_path, capsys):
    repo, journal, spec, _ = setup_repo(tmp_path)
    write_journal(journal, tasks=["1.1"])

    assert recover.recover(repo, spec, [], ["1.1"], remote="origin") == 0
    assert json.loads(journal.read_text())["entries"] == []
    assert "no retained branch" in capsys.readouterr().out


def test_dirty_selected_checkout_refuses_before_journal_write(tmp_path):
    repo, journal, spec, spec_id = setup_repo(tmp_path)
    branch = worktree.task_branch(spec_id, "1.1")
    git(repo, "branch", branch)
    git(repo, "switch", branch)
    (repo / "dirty.txt").write_text("dirty\n")
    write_journal(journal, tasks=["1.1"])
    before = journal.read_text()

    assert recover.recover(repo, spec, [], ["1.1"], remote="origin") == 1
    assert journal.read_text() == before


def test_completion_record_and_mid_recovery_lock_leave_journal_unchanged(
    tmp_path, monkeypatch
):
    repo, journal, spec, spec_id = setup_repo(tmp_path)
    branch = worktree.task_branch(spec_id, "1.1")
    git(repo, "branch", branch)
    journal.write_text(
        json.dumps(
            {
                "entries": [
                    {"task": "1.1", "role": "cleanup", "report": {"status": "success"}}
                ]
            }
        )
        + "\n"
    )
    before = journal.read_text()
    assert recover.recover(repo, spec, [], ["1.1"], remote="origin") == 1
    assert journal.read_text() == before

    write_journal(journal, tasks=["1.1"])
    before = journal.read_text()
    checks = iter([False, True])
    monkeypatch.setattr(recover, "_runlock_held", lambda _path: next(checks))
    assert recover.recover(repo, spec, [], ["1.1"], remote="origin") == 1
    assert journal.read_text() == before


def test_no_selector_and_non_quarantined_group_refuse_without_writing(tmp_path):
    repo, journal, spec, _ = setup_repo(tmp_path)
    write_journal(journal, tasks=["1.1"], groups={"g": {"state": "MERGED"}})
    before = journal.read_text()
    assert recover.recover(repo, spec, [], [], remote="origin") == 1
    assert recover.recover(repo, spec, ["g"], [], remote="origin") == 1
    assert journal.read_text() == before
