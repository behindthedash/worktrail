#!/usr/bin/env python3
"""PreToolUse guard for spawned task workers: deny writes outside the worker's
own worktree.

Every orchestrator worker is launched with `cwd` set to its task worktree and a
brief that says `Worktree (operate ONLY here): <path>` -- but that line is
advisory. On 2026-09-05 (run full-1788634519, task 2.1) a `claude` implement
worker wrote its draft into the canonical `~/projects/worktrail` checkout
instead, dirtying the branch every other task and the orchestrator itself
depend on. The operator's own `worktree-write-guard` hook did not fire because
workers run with `--setting-sources project,local`, which deliberately drops
user-level settings (a user Stop hook used to eat the report-back turn).

`spawnlib` therefore injects THIS hook into every `claude` worker through
`--settings` (an additional settings source that survives `--setting-sources`),
so the guard travels with the package rather than depending on what the
operator has configured. Hooks fire, and their `deny` is honored, under
`--permission-mode bypassPermissions` (live-verified 2026-09-05).

Rules (fail-open on anything unexpected -- a broken guard must never stall a
run):
- `Write`/`Edit`/`NotebookEdit`: the target path must resolve inside the
  worker's `cwd`.
- `Bash`: the command must not name the canonical checkout the worktree is
  linked from (its `git rev-parse --git-common-dir` parent) when that differs
  from `cwd` -- the only realistic way a shell command edits the wrong tree is
  by spelling out that absolute path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

FILE_TOOLS = {"Write", "Edit", "NotebookEdit"}
GUARDED_TOOLS = FILE_TOOLS | {"Bash"}
HOOK_MATCHER = "|".join(sorted(GUARDED_TOOLS))


def _deny(reason: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }


def _canonical_root(cwd: str) -> str | None:
    """The canonical checkout a linked worktree belongs to, or None when
    `cwd` is not a linked worktree (or git is unavailable)."""
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    common = r.stdout.strip()
    if not os.path.isabs(common):
        common = os.path.join(cwd, common)
    root = os.path.realpath(os.path.dirname(common))
    return None if root == os.path.realpath(cwd) else root


def decide(payload: dict) -> dict | None:
    """Return a deny decision dict, or None to allow."""
    tool = payload.get("tool_name")
    if tool not in GUARDED_TOOLS:
        return None
    cwd = payload.get("cwd")
    if not cwd:
        return None
    root = os.path.realpath(cwd)
    tool_input = payload.get("tool_input") or {}
    if tool in FILE_TOOLS:
        target = tool_input.get(
            "notebook_path" if tool == "NotebookEdit" else "file_path"
        )
        if not target:
            return None
        resolved = os.path.realpath(
            target if os.path.isabs(target) else os.path.join(root, target)
        )
        if resolved == root or resolved.startswith(root + os.sep):
            return None
        return _deny(
            f"Worker worktree guard: {tool} to {target} is outside this task's "
            f"worktree ({root}). Operate ONLY inside the worktree named in your "
            "brief; the canonical checkout and other worktrees belong to other tasks."
        )
    command = tool_input.get("command") or ""
    canonical = _canonical_root(root)
    if canonical and canonical in command:
        return _deny(
            f"Worker worktree guard: this command names the canonical checkout "
            f"{canonical}, which this task must not touch. Operate ONLY inside "
            f"this task's worktree ({root})."
        )
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        decision = decide(payload)
    except Exception:  # noqa: BLE001 -- fail open: a broken guard must never stall a worker
        return 0
    if decision is not None:
        print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
