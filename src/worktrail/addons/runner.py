"""Shared add-on runner: install/configure/run each configured add-on, then
stage and commit whatever it produced.

`integrate.py`'s group-PR path and `router/preflight.py`'s one-off path both
finish a task by handing its worktree off to a PR. This module is the one
place that knows how to run a configured `AddOn` against that worktree and
commit its output, so neither call site duplicates the add/diff/commit
sequence `_write_group_task_status` already established for spec-status
writes (`orchestrator/integrate.py:274-323`).
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worktrail.addons.base import AddOnResult
from worktrail.addons.resolve import addon_for

# Bounded so a slow/hung add-on (e.g. a third-party CLI network call) cannot
# stall a task's pre-PR flow indefinitely. Mirrors `SMOKE_TIMEOUT_DEFAULT`
# (`orchestrator/integrate.py:639`); an `AddOn` threads this through its own
# `subprocess.run(..., timeout=ctx.timeout)` calls rather than the runner
# wrapping the whole install/configure/run sequence in a hard kill.
ADDON_TIMEOUT_DEFAULT = int(os.environ.get("ORCH_ADDON_TIMEOUT", "600"))


@dataclass
class AddOnContext:
    """What an `AddOn`'s `install`/`configure`/`run` receive as `ctx`."""

    worktree: Path
    repo: Path
    config: dict[str, Any]
    timeout: int


@dataclass
class AddOnRunLog:
    """What `run_addons` did for one configured add-on.

    Returned to the caller (`integrate_one`, `worktrail-preflight run`) so it
    can decide whether a `required` failure should block the PR. `ok` is
    False only for a non-fatal failure (see `AddOnFailure`) that was logged
    and skipped rather than raised.
    """

    name: str
    changed: bool
    committed: bool
    detail: str
    ok: bool = True


class AddOnFailure(Exception):
    """A `required` add-on's install/configure/run step failed.

    Per design D4, `run_addons` raises this instead of swallowing the
    failure so `integrate_one`/`worktrail-preflight run` can quarantine/fail
    the same way an unresolvable add-on name (D5) or a failed drift/smoke
    gate already does. A non-`required` add-on's failure is logged and
    skipped instead -- see `run_addons`.
    """

    def __init__(self, name: str, detail: str):
        super().__init__(f"add-on {name!r} failed (required): {detail}")
        self.name = name
        self.detail = detail


def _git(worktree: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(worktree), *args], capture_output=True, text=True, check=check
    )


def _stage_and_commit(worktree: Path, name: str, result: AddOnResult) -> bool:
    """Stage `result.paths` and commit iff they produce a real diff.

    Mirrors `_write_group_task_status`'s add -> `diff --cached --quiet` ->
    commit sequence (`integrate.py:274-323`): the real `git diff` is the
    source of truth for "did this change anything", not the add-on's
    self-reported `AddOnResult.changed`, so an add-on reporting a change
    that turns out identical to HEAD still produces no empty commit.
    """
    if not result.paths:
        return False
    _git(worktree, "add", "--", *[str(p) for p in result.paths], check=False)
    if _git(worktree, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return False
    _git(worktree, "commit", "-q", "-m", f"chore({name}): {result.detail}")
    return True


def run_addons(worktree: Path, repo: Path, policy: dict) -> list[AddOnRunLog]:
    """Run every enabled `policy["add_ons"]` entry against `worktree`.

    A repo with no `add_ons:` key (or `{}`) iterates zero entries -- the
    zero-behavior-change guarantee for an unconfigured repo. An entry with
    `enabled: false` is skipped; absent otherwise defaults to enabled.
    """
    worktree = Path(worktree)
    repo = Path(repo)
    logs: list[AddOnRunLog] = []
    for name, raw_config in (policy.get("add_ons") or {}).items():
        config = raw_config or {}
        if not config.get("enabled", True):
            continue
        addon = addon_for(name)
        ctx = AddOnContext(
            worktree=worktree, repo=repo, config=config, timeout=ADDON_TIMEOUT_DEFAULT
        )
        try:
            addon.install(ctx)
            addon.configure(ctx)
            result = addon.run(ctx)
        except Exception as e:
            # Covers subprocess.TimeoutExpired (add-on's own subprocess.run call
            # hung past its timeout), OSError (spawn failure), and any exception
            # an add-on's install/configure/run raises. Per design D4 this is
            # non-fatal by default -- aspens sync is maintenance, not correctness,
            # so a flaky third-party CLI must not block an unrelated task's PR.
            detail = f"{type(e).__name__}: {e}"
            if config.get("required"):
                raise AddOnFailure(name, detail) from e
            print(f"  WARN [addon:{name}] non-fatal failure, skipping: {detail}")
            logs.append(
                AddOnRunLog(name=name, changed=False, committed=False, detail=detail, ok=False)
            )
            continue
        committed = _stage_and_commit(worktree, name, result)
        logs.append(
            AddOnRunLog(name=name, changed=result.changed, committed=committed, detail=result.detail)
        )
    return logs
