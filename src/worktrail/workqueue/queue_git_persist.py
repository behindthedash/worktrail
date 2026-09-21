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


def _relative(path: Path, root: Path) -> str:
    p = Path(path)
    if not p.is_absolute():
        return p.as_posix()
    return p.resolve().relative_to(root.resolve()).as_posix()


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

    rel = tuple(_relative(Path(p), root) for p in paths)
    if not rel:
        return PersistResult(status="skipped", reason="no-paths")

    runner = run or _default_run

    add = runner(["git", "add", "--", *rel], root, timeout)
    if add.returncode != 0:
        return PersistResult(
            status="failed", reason="add-failed", paths=rel, error=_err(add)
        )

    message = f"chore(work-queue): materialize external event {event_id}"
    commit = runner(["git", "commit", "-m", message, "--", *rel], root, timeout)
    committed = commit.returncode == 0
    if not committed and not _nothing_to_commit(commit):
        return PersistResult(
            status="failed", reason="commit-failed", paths=rel, error=_err(commit)
        )

    push = runner(["git", "push"], root, timeout)
    if push.returncode != 0:
        return PersistResult(
            status="failed",
            reason="push-failed",
            committed=committed,
            paths=rel,
            error=_err(push),
        )

    return PersistResult(status="pushed", committed=committed, pushed=True, paths=rel)


def _nothing_to_commit(proc) -> bool:
    """A re-run over already-committed paths is success, not failure."""
    blob = f"{proc.stdout or ''}\n{proc.stderr or ''}".lower()
    return "nothing to commit" in blob or "no changes added to commit" in blob


def _err(proc) -> str:
    return ((proc.stderr or "") + (proc.stdout or "")).strip()
