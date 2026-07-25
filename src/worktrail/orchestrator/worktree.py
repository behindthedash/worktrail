#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- worktree lifecycle.

Programmatic git-worktree management for the coordinator. Sibling-directory
layout (locked decision, design doc 13.4):
    base       = ../<repo>-worktrees/
    task path  = <base>/<spec>-<task>
    task branch= <spec>/<task>          e.g. 025-feature/task-003
    group br.  = <spec>/<group>         e.g. 025-feature/feature-1

Conventions adopted from the superpowers `using-git-worktrees` skill (credit):
  - **Sandbox-denial fallback**: `git worktree add` can fail with a permission
    error under a sandbox; detect it and surface a clear fallback instead of a
    raw crash. (Verified relevant: this environment is sandboxed.)
  - Project-local worktree dirs must be gitignored -- we sidestep that by using a
    SIBLING dir outside the repo, so nothing leaks into git status.

Why we implement this directly instead of depending on that plugin: the
superpowers skill is *interactive agent guidance* (detect existing isolation,
prefer the harness's native worktree tool, ask consent, set up ONE workspace).
Our coordinator is headless code that creates MANY worktrees for per-task
fan-out -- the opposite use case. We borrow its hard-won conventions, not the
plugin.

`dry_run=True` (the demo default) prints the git commands without executing --
safe to run anywhere, including this sandbox.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# stderr fragments that indicate a sandbox/permission block rather than a real
# git error (mirrors the superpowers "sandbox fallback" note).
_SANDBOX_HINTS = ("permission denied", "operation not permitted",
                  "read-only file system")


class WorktreeError(RuntimeError):
    pass


class SandboxDenied(WorktreeError):
    pass


# --------------------------------------------------------------------------- #
# Naming (pure)
# --------------------------------------------------------------------------- #
def task_branch(spec_id: str, task_id: str) -> str:
    return f"{spec_id}/{task_id.lower()}"


def group_branch(spec_id: str, group: str) -> str:
    return f"{spec_id}/{group}"


def default_worktree_base(repo_root: Path) -> Path:
    repo_root = Path(repo_root).resolve()
    return repo_root.parent / f"{repo_root.name}-worktrees"


def worktree_path(base: Path, spec_id: str, task_id: str) -> Path:
    return Path(base) / f"{spec_id}-{task_id.lower()}"


# --------------------------------------------------------------------------- #
# Manager
# --------------------------------------------------------------------------- #
@dataclass
class WorktreeManager:
    repo_root: Path
    spec_id: str
    base_commit: str = "HEAD"          # fan-out point: spec+tasks already committed
    worktree_base: Optional[Path] = None
    dry_run: bool = False
    # Optional injected effect runner (cmd list -> object with .returncode /
    # .stdout / .stderr). When set, the live verify path and tests reuse this
    # manager without shelling out directly; default falls back to subprocess.
    runner: Optional[Callable[[List[str]], object]] = None
    log: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).resolve()
        self.worktree_base = (
            Path(self.worktree_base) if self.worktree_base
            else default_worktree_base(self.repo_root)
        )

    def _git(self, *args: str) -> str:
        cmd = ["git", "-C", str(self.repo_root), *args]
        printable = " ".join(cmd)
        self.log.append(printable)
        if self.dry_run:
            print(f"DRY-RUN $ {printable}")
            return ""
        proc = self.runner(cmd) if self.runner else subprocess.run(
            cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            if any(h in err.lower() for h in _SANDBOX_HINTS):
                raise SandboxDenied(
                    f"git worktree blocked (sandbox?): {err}\n"
                    "Fallback: run the task in-place, or allow the worktree path "
                    "outside the sandbox."
                )
            raise WorktreeError(f"`{printable}` failed: {err}")
        return (proc.stdout or "").strip()

    def add(self, task_id: str) -> Path:
        """Create an isolated worktree+branch for a task off base_commit."""
        path = worktree_path(self.worktree_base, self.spec_id, task_id)
        self._git("worktree", "add", "-b",
                  task_branch(self.spec_id, task_id), str(path), self.base_commit)
        return path

    def remove(self, task_id: str, force: bool = True) -> None:
        path = worktree_path(self.worktree_base, self.spec_id, task_id)
        args = ["worktree", "remove", str(path)]
        if force:
            args.append("--force")
        self._git(*args)

    def delete_branch(self, task_id: str, force: bool = True) -> None:
        """Delete a task's local branch (after its group merged green)."""
        self._git("branch", "-D" if force else "-d",
                  task_branch(self.spec_id, task_id))

    def prune(self) -> None:
        self._git("worktree", "prune")

    def list_worktrees(self) -> str:
        return self._git("worktree", "list")


# --------------------------------------------------------------------------- #
# Demo (dry-run)
# --------------------------------------------------------------------------- #
def _demo() -> None:
    spec = "025-feature"
    frontier = ["TASK-002", "TASK-003", "TASK-006"]   # one parallel batch
    wm = WorktreeManager(repo_root="/repos/app", spec_id=spec, dry_run=True)

    print("=" * 64)
    print("WORKTREE FAN-OUT PLAN  (dry-run; illustrative repo_root=/repos/app)")
    print("=" * 64)
    print(f"  sibling base : {wm.worktree_base}")
    print(f"  base commit  : {wm.base_commit}")
    print(f"  parallel batch: {', '.join(frontier)}\n")

    print("--- dispatch: one worktree+branch per task -------------------")
    for tid in frontier:
        p = wm.add(tid)
        print(f"      -> {tid}: worktree {p}  on branch {task_branch(spec, tid)}")

    print("\n--- teardown after integration -------------------------------")
    for tid in frontier:
        wm.remove(tid)
    wm.prune()

    print("\nNote: under a sandbox, `git worktree add` may be denied; the manager")
    print("raises SandboxDenied with a fallback hint rather than crashing.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Orchestrator worktree lifecycle")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="Dry-run worktree fan-out/teardown plan")
    args = p.parse_args(argv)
    if args.cmd == "demo":
        _demo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
