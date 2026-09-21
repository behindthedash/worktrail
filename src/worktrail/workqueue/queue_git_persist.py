"""Durable git persistence for a newly captured external brief.

The ingress path (`pullhook_ingress`) may only acknowledge a relay event once the
brief it created is durable. When the work-queue root is a git repo and queue git
sync is enabled, "durable" means the brief -- plus the WorkTrail-owned
materialization metadata that records it -- is committed and pushed.

Two rules make this narrow helper different from `work_queue._git_backup`:

* It stages an explicit path list, never `add -A`. A relay ack must not be gated on
  (or silently carry along) unrelated in-flight queue mutations.
* A push failure is *reported to the caller* rather than swallowed. The backup path
  is best-effort; this path is the acknowledgement precondition, so the caller has
  to be able to withhold the ack and let the relay redeliver.

It never pulls: a pull would race the atomic rename that arbitrates claims.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from worktrail.workqueue.work_queue import base_dir

_TRUTHY = ("1", "true", "yes", "on")

#: Opt-in switch, shared with the queue's best-effort backup path.
SYNC_ENV = "WORK_QUEUE_GIT_SYNC"
#: Configuration that makes git persistence a hard requirement: without it, a
#: non-git queue root (or a failed push) is an error rather than a skip.
REQUIRE_ENV = "WORK_QUEUE_GIT_REQUIRED"


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class PersistResult:
    """Outcome of one persistence attempt.

    ``status`` is one of:

    * ``"pushed"``  -- the paths are on the remote (possibly by an earlier attempt).
    * ``"skipped"`` -- git persistence was not enabled/possible and is not required,
      so the local brief still counts as durably created.
    * ``"failed"``  -- git persistence was required and did not complete.
    """

    status: str
    reason: str | None = None
    committed: bool = False
    pushed: bool = False
    paths: tuple[str, ...] = ()
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True when the caller may acknowledge the relay event."""
        return self.status in ("pushed", "skipped")


RunFn = Callable[..., "subprocess.CompletedProcess[str]"]


def _default_run(args: Sequence[str], cwd: Path, timeout: int):
    return subprocess.run(
        list(args),
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


class _GitInvocationError(Exception):
    """A git command never produced an exit status (timed out, or could not run)."""

    def __init__(self, reason: str, error: str) -> None:
        super().__init__(error)
        self.reason = reason
        self.error = error


def _invoke(runner: RunFn, args: Sequence[str], root: Path, timeout: int, what: str):
    """Run one git command, turning a non-exit failure into `_GitInvocationError`.

    A stalled push (network, or a credential prompt) is the failure the timeout
    bound exists to catch, and it is exactly the case the caller has to see as a
    value so it can withhold the ack.
    """
    try:
        return runner(list(args), root, timeout)
    except subprocess.TimeoutExpired as exc:
        raise _GitInvocationError(
            f"{what}-timeout", f"git {what} timed out after {timeout}s"
        ) from exc
    except OSError as exc:
        raise _GitInvocationError(
            f"{what}-error", f"git {what} could not run: {exc}"
        ) from exc


class _PathOutsideRoot(Exception):
    """A caller-supplied path does not live under the queue root."""


def _relative(path: Path, root: Path) -> str:
    """Path relative to the queue root, as a git pathspec.

    A path outside the root cannot be staged; this helper reports failures as a
    value, so the caller turns that into a `PersistResult` rather than an
    escaping `ValueError` from `Path.relative_to`.
    """
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise _PathOutsideRoot(f"{p} is not under the queue root {root}") from exc


def persist_external_brief(
    paths: Sequence[Path | str],
    *,
    event_id: str,
    queue_base: Path | None = None,
    require_git: bool | None = None,
    run: RunFn | None = None,
    timeout: int = 30,
) -> PersistResult:
    """Stage/commit/push exactly ``paths`` for the materialized event ``event_id``.

    ``paths`` are the created brief and the WorkTrail-owned materialization
    metadata, absolute or relative to the queue root. ``require_git`` defaults to
    ``$WORK_QUEUE_GIT_REQUIRED``.

    Safe to call again after a partial failure: the commit is skipped when the
    paths are already committed, and the push is retried, so a retry completes
    persistence without creating a second commit.
    """
    root = Path(queue_base) if queue_base is not None else base_dir()
    required = _truthy(REQUIRE_ENV) if require_git is None else bool(require_git)

    if not required and not _truthy(SYNC_ENV):
        return PersistResult(status="skipped", reason="sync-disabled")

    if not (root / ".git").exists():
        if required:
            return PersistResult(
                status="failed",
                reason="not-a-git-repo",
                error=f"git persistence required but {root} is not a git repo",
            )
        return PersistResult(status="skipped", reason="not-a-git-repo")

    try:
        rel = tuple(_relative(Path(p), root) for p in paths)
    except _PathOutsideRoot as exc:
        return PersistResult(
            status="failed", reason="path-outside-root", error=str(exc)
        )
    if not rel:
        return PersistResult(status="skipped", reason="no-paths")

    runner = run or _default_run

    try:
        return _persist(runner, rel, event_id=event_id, root=root, timeout=timeout)
    except _GitInvocationError as exc:
        return PersistResult(
            status="failed", reason=exc.reason, paths=rel, error=exc.error
        )


def _persist(
    runner: RunFn,
    rel: tuple[str, ...],
    *,
    event_id: str,
    root: Path,
    timeout: int,
) -> PersistResult:
    add = _invoke(runner, ["git", "add", "--", *rel], root, timeout, "add")
    if add.returncode != 0:
        return PersistResult(
            status="failed", reason="add-failed", paths=rel, error=_err(add)
        )

    committed = False
    if _has_staged_changes(runner, rel, root=root, timeout=timeout):
        message = f"chore(work-queue): materialize external event {event_id}"
        commit = _invoke(
            runner,
            ["git", "commit", "-m", message, "--", *rel],
            root,
            timeout,
            "commit",
        )
        if commit.returncode != 0:
            return PersistResult(
                status="failed", reason="commit-failed", paths=rel, error=_err(commit)
            )
        committed = True

    push = _invoke(runner, ["git", "push"], root, timeout, "push")
    if push.returncode != 0:
        return PersistResult(
            status="failed",
            reason="push-failed",
            committed=committed,
            paths=rel,
            error=_err(push),
        )

    return PersistResult(status="pushed", committed=committed, pushed=True, paths=rel)


def _has_staged_changes(
    runner: RunFn, rel: tuple[str, ...], *, root: Path, timeout: int
) -> bool:
    """Whether the staged content of ``rel`` differs from HEAD.

    Decided from repository state, not from git's prose: a retry over paths that
    an earlier attempt already committed must skip the commit and go straight to
    the push. Reading `git commit`'s message instead misfires whenever the queue
    root holds unrelated untracked content, where git says "nothing added to
    commit but untracked files present" -- the normal state of a live queue.
    """
    diff = _invoke(
        runner,
        ["git", "diff", "--cached", "--quiet", "HEAD", "--", *rel],
        root,
        timeout,
        "diff",
    )
    # 0 = identical to HEAD, 1 = differs. Anything else (e.g. 128 on a repo with
    # no HEAD yet) is not evidence of "already committed": let the commit run.
    return diff.returncode != 0


def _err(proc) -> str:
    return ((proc.stderr or "") + (proc.stdout or "")).strip()
