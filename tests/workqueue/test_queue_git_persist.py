from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from worktrail.workqueue.queue_git_persist import (
    REQUIRE_ENV,
    SYNC_ENV,
    persist_external_brief,
)


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return proc.stdout


@pytest.fixture
def queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A git-backed queue root with a bare `origin` and an upstream-tracked branch."""
    monkeypatch.setenv(SYNC_ENV, "1")
    monkeypatch.delenv(REQUIRE_ENV, raising=False)

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--bare", "-b", "main", str(remote))

    root = tmp_path / "work-queue"
    (root / "queue").mkdir(parents=True)
    (root / ".worktrail").mkdir()
    _git(tmp_path, "init", "-b", "main", str(root))
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "commit.gpgsign", "false")
    (root / "README.md").write_text("queue\n")
    _git(root, "add", "README.md")
    _git(root, "commit", "-m", "init")
    _git(root, "remote", "add", "origin", str(remote))
    _git(root, "push", "-u", "origin", "main")
    return root


def _capture(root: Path) -> tuple[Path, Path]:
    brief = root / "queue" / "20260920-external.md"
    brief.write_text("# external brief\n")
    marker = root / ".worktrail" / "external-events.json"
    marker.write_text('{"evt-1": "20260920-external.md"}\n')
    return brief, marker


def test_commit_and_push_stages_only_the_given_paths(queue: Path):
    brief, marker = _capture(queue)
    unrelated = queue / "queue" / "someone-elses-brief.md"
    unrelated.write_text("in flight\n")

    result = persist_external_brief([brief, marker], event_id="evt-1", queue_base=queue)

    assert result.status == "pushed"
    assert result.ok and result.committed and result.pushed
    assert "evt-1" in _git(queue, "log", "-1", "--pretty=%s")
    committed = sorted(_git(queue, "show", "--name-only", "--pretty=", "HEAD").split())
    assert committed == [
        ".worktrail/external-events.json",
        "queue/20260920-external.md",
    ]
    assert "?? queue/someone-elses-brief.md" in _git(queue, "status", "--porcelain")
    assert (
        _git(queue, "rev-parse", "HEAD").strip()
        == _git(queue, "rev-parse", "origin/main").strip()
    )


def test_push_failure_is_reported_to_the_caller(queue: Path, tmp_path: Path):
    brief, marker = _capture(queue)
    shutil.rmtree(tmp_path / "remote.git")

    result = persist_external_brief([brief, marker], event_id="evt-1", queue_base=queue)

    assert result.status == "failed"
    assert result.reason == "push-failed"
    assert not result.ok and not result.pushed
    assert result.committed
    assert result.error


def test_retry_after_push_failure_completes_persistence_idempotently(
    queue: Path, tmp_path: Path
):
    brief, marker = _capture(queue)
    remote = tmp_path / "remote.git"
    backup = tmp_path / "remote-backup.git"
    shutil.move(str(remote), str(backup))

    first = persist_external_brief([brief, marker], event_id="evt-1", queue_base=queue)
    assert first.status == "failed"
    after_first = _git(queue, "rev-parse", "HEAD").strip()

    shutil.move(str(backup), str(remote))
    retry = persist_external_brief([brief, marker], event_id="evt-1", queue_base=queue)

    assert retry.status == "pushed"
    assert retry.committed is False  # nothing left to commit; only the push was redone
    assert _git(queue, "rev-parse", "HEAD").strip() == after_first
    assert (
        len(_git(queue, "log", "--pretty=%s", "--grep", "evt-1").strip().splitlines())
        == 1
    )
    assert _git(queue, "rev-parse", "origin/main").strip() == after_first


def test_non_git_queue_dir_is_a_skip_when_git_is_not_required(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(SYNC_ENV, "1")
    monkeypatch.delenv(REQUIRE_ENV, raising=False)
    root = tmp_path / "plain-queue"
    (root / "queue").mkdir(parents=True)
    brief = root / "queue" / "b.md"
    brief.write_text("x\n")

    result = persist_external_brief([brief], event_id="evt-1", queue_base=root)

    assert result.status == "skipped"
    assert result.reason == "not-a-git-repo"
    assert result.ok  # local durable creation stands; the caller may still ack


def test_non_git_queue_dir_fails_when_configuration_requires_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(REQUIRE_ENV, "1")
    monkeypatch.delenv(SYNC_ENV, raising=False)
    root = tmp_path / "plain-queue"
    (root / "queue").mkdir(parents=True)
    brief = root / "queue" / "b.md"
    brief.write_text("x\n")

    result = persist_external_brief([brief], event_id="evt-1", queue_base=root)

    assert result.status == "failed"
    assert result.reason == "not-a-git-repo"
    assert not result.ok


def test_sync_disabled_is_a_skip(queue: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(SYNC_ENV, raising=False)
    brief, marker = _capture(queue)

    result = persist_external_brief([brief, marker], event_id="evt-1", queue_base=queue)

    assert result.status == "skipped"
    assert result.reason == "sync-disabled"
    assert "?? queue/20260920-external.md" in _git(
        queue, "status", "--porcelain", "--untracked-files=all"
    )


def test_never_pulls(queue: Path):
    brief, marker = _capture(queue)
    calls: list[list[str]] = []

    def fake_run(args, cwd, timeout):
        calls.append(list(args))
        rc = 1 if args[1] == "diff" else 0  # 1 = paths differ from HEAD
        return subprocess.CompletedProcess(list(args), rc, "", "")

    persist_external_brief(
        [brief, marker], event_id="evt-1", queue_base=queue, run=fake_run
    )

    assert [c[1] for c in calls] == ["add", "diff", "commit", "push"]
    assert not any("pull" in c or "fetch" in c for c in calls)


def test_push_timeout_is_reported_not_raised(queue: Path):
    """A stalled push is the failure the timeout bound exists to catch: the caller
    has to see it as a result so it can withhold the ack, not lose the process."""
    brief, marker = _capture(queue)

    def fake_run(args, cwd, timeout):
        if args[1] == "push":
            raise subprocess.TimeoutExpired(list(args), timeout)
        return subprocess.CompletedProcess(list(args), 0, "", "")

    result = persist_external_brief(
        [brief, marker], event_id="evt-1", queue_base=queue, run=fake_run
    )

    assert result.status == "failed"
    assert result.reason == "push-timeout"
    assert not result.ok
    assert not result.pushed


def test_git_binary_missing_is_reported_not_raised(queue: Path):
    brief, marker = _capture(queue)

    def fake_run(args, cwd, timeout):
        raise FileNotFoundError(2, "No such file or directory", "git")

    result = persist_external_brief(
        [brief, marker], event_id="evt-1", queue_base=queue, run=fake_run
    )

    assert result.status == "failed"
    assert result.reason == "add-error"
    assert not result.ok


def test_retry_is_idempotent_with_unrelated_uncommitted_content(
    queue: Path, tmp_path: Path
):
    """The live-queue case: the root holds other in-flight content at retry time.

    Deciding "already committed" from `git commit`'s prose misreads git's
    "nothing added to commit but untracked files present" here as a hard
    failure, so the retry never reaches the push and the brief stays local.
    """
    brief, marker = _capture(queue)
    remote = tmp_path / "remote.git"
    backup = tmp_path / "remote-backup.git"
    shutil.move(str(remote), str(backup))

    first = persist_external_brief([brief, marker], event_id="evt-1", queue_base=queue)
    assert first.status == "failed"
    after_first = _git(queue, "rev-parse", "HEAD").strip()

    (queue / "queue" / "someone-elses-brief.md").write_text("in flight\n")
    staged = queue / "queue" / "staged-by-someone-else.md"
    staged.write_text("staged\n")
    _git(queue, "add", "--", "queue/staged-by-someone-else.md")

    shutil.move(str(backup), str(remote))
    retry = persist_external_brief([brief, marker], event_id="evt-1", queue_base=queue)

    assert retry.status == "pushed"
    assert retry.committed is False
    assert _git(queue, "rev-parse", "HEAD").strip() == after_first
    assert _git(queue, "rev-parse", "origin/main").strip() == after_first
    # the unrelated in-flight paths were neither committed nor unstaged
    assert "A  queue/staged-by-someone-else.md" in _git(queue, "status", "--porcelain")
    assert "?? queue/someone-elses-brief.md" in _git(queue, "status", "--porcelain")


def test_genuine_commit_failure_is_reported(queue: Path):
    """A real non-zero `git commit` (not the already-committed case) fails."""
    brief, marker = _capture(queue)

    def fake_run(args, cwd, timeout):
        if args[1] == "diff":
            return subprocess.CompletedProcess(list(args), 1, "", "")
        if args[1] == "commit":
            return subprocess.CompletedProcess(
                list(args), 128, "", "fatal: could not read Username\n"
            )
        return subprocess.CompletedProcess(list(args), 0, "", "")

    result = persist_external_brief(
        [brief, marker], event_id="evt-1", queue_base=queue, run=fake_run
    )

    assert result.status == "failed"
    assert result.reason == "commit-failed"
    assert "could not read Username" in (result.error or "")
    assert not result.ok and not result.pushed


def test_add_failure_is_reported(queue: Path):
    brief, marker = _capture(queue)

    def fake_run(args, cwd, timeout):
        if args[1] == "add":
            return subprocess.CompletedProcess(
                list(args), 128, "", "fatal: pathspec did not match\n"
            )
        return subprocess.CompletedProcess(list(args), 0, "", "")

    result = persist_external_brief(
        [brief, marker], event_id="evt-1", queue_base=queue, run=fake_run
    )

    assert result.status == "failed"
    assert result.reason == "add-failed"
    assert "pathspec" in (result.error or "")


def test_path_outside_the_queue_root_is_reported_not_raised(
    queue: Path, tmp_path: Path
):
    outside = tmp_path / "elsewhere.md"
    outside.write_text("nope\n")

    result = persist_external_brief([outside], event_id="evt-1", queue_base=queue)

    assert result.status == "failed"
    assert result.reason == "path-outside-root"
    assert not result.ok
