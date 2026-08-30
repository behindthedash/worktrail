#!/usr/bin/env python3
"""
Mechanical step for worktrail-go's Phase 2 `close-stale` action, OpenSpec case.

`close-stale` fires when a spec's implementation already shipped but its
task-tracking artifact never got flipped to reflect that -- bookkeeping drift,
not real remaining work (`_safe_detect_openspec` in `dashboard.py` is what
detects the `stale-bookkeeping` stage this action responds to). Two live
occurrences of the identical shape (PR #547 2026-08-19, PR #548 2026-08-20)
each hand-rolled the same three steps: flip every remaining unchecked
checkbox in the change's `tasks.md`, run `openspec archive -y`, and let the
caller's normal commit/push/PR/pre-PR-gate flow take it from there.

This module scripts only the first two of those -- the mechanical, identical-
every-time part. It deliberately does NOT commit, push, open a PR, or run
`gh` at all: worktrail-go's own Phase 8 (pre-PR gate, PR template, automerge
labels, CI watch loop) already owns that path and enforces label correctness
via a PreToolUse hook (`worktrail-preflight`) that a bespoke `gh pr create`
call here would bypass. It also does NOT decide which tasks are safe to flip
-- confirming that a task's shipped work is actually on `$BASE` (the "confirm
the spec's pending impl tasks are truly shipped" step SKILL.md's close-stale
row already documents) stays a judgment call for the dispatching agent, since
OpenSpec's `tasks.md` carries no per-task `files:` frontmatter to check
mechanically (unlike the devkit format's `TASK-*.md`) -- see
`taskformats/openspec/schema.py`'s module docstring.

`drain.py`'s `archive_openspec_change` is a different, non-reusable shape for
this: it creates its own worktree/branch, assumes checkboxes are already
flipped, and drives `gh pr create` directly for drain's own unattended
background sweep. Nothing here duplicates it -- this operates on a worktree
`worktrail-go` already created (`#fix-branch-worktree-setup`) and stops before
the PR boundary.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from ..taskformats.openspec.schema import parse_tasks_md, set_task_checked


def flip_and_archive(
    worktree: Path,
    change_id: str,
    task_ids: list[str] | None = None,
    *,
    timeout: int = 300,
) -> dict[str, Any]:
    """Flip the given (or all pending) task checkboxes for `change_id` and
    run `openspec archive -y <change_id> --json` in `worktree`.

    Returns `{"checked": bool, "change_dir": str, "flipped": [...],
    "already_checked": [...], "unknown_task_ids": [...], "archived": bool,
    "archive_output": str, "error": str|None}`.

    `checked=False` means the change directory or its `tasks.md` could not be
    found -- the caller should treat this as unknown, not as "nothing to do".
    `unknown_task_ids` (only populated when `task_ids` is passed explicitly)
    lists ids that don't exist in `tasks.md` at all -- distinct from
    `already_checked`, which means the id exists and was already `[x]`.
    Archiving is attempted only when at least one flip happened or every
    requested id was already checked; a genuine `unknown_task_ids` miss with
    no already-checked fallback stops before running `openspec archive` at
    all, since that would archive a change the caller may not have actually
    finished flipping.
    """
    worktree = Path(worktree)
    result: dict[str, Any] = {
        "checked": False,
        "change_dir": None,
        "flipped": [],
        "already_checked": [],
        "unknown_task_ids": [],
        "archived": False,
        "archive_output": "",
        "error": None,
    }

    change_dir = worktree / "openspec" / "changes" / change_id
    tasks_md = change_dir / "tasks.md"
    if not tasks_md.is_file():
        result["error"] = f"tasks.md not found: {tasks_md}"
        return result

    result["checked"] = True
    result["change_dir"] = str(change_dir)

    parsed = parse_tasks_md(tasks_md.read_text())
    if task_ids is None:
        targets = [t.id for t in parsed.tasks if t.status != "completed"]
    else:
        targets = list(task_ids)
        known_ids = {t.id for t in parsed.tasks}
        result["unknown_task_ids"] = [tid for tid in targets if tid not in known_ids]
        targets = [tid for tid in targets if tid in known_ids]

    for tid in targets:
        task = parsed.by_id(tid)
        if task is not None and task.status == "completed":
            result["already_checked"].append(tid)
            continue
        if set_task_checked(tasks_md, tid, True):
            result["flipped"].append(tid)
        else:
            result["already_checked"].append(tid)

    if not result["flipped"] and not result["already_checked"]:
        result["error"] = (
            "nothing to flip and nothing already checked; refusing to archive"
        )
        return result
    if (
        result["unknown_task_ids"]
        and not result["flipped"]
        and not result["already_checked"]
    ):
        result["error"] = f"unknown task ids: {result['unknown_task_ids']}"
        return result

    proc = subprocess.run(
        ["openspec", "archive", "-y", change_id, "--json"],
        capture_output=True,
        text=True,
        cwd=str(worktree),
        timeout=timeout,
    )
    result["archive_output"] = (proc.stdout or proc.stderr).strip()
    if proc.returncode != 0:
        result["error"] = (
            f"openspec archive -y {change_id} failed: {result['archive_output']}"
        )
        return result

    result["archived"] = True
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worktree", required=True, help="the fix-branch worktree path"
    )
    parser.add_argument("--change-id", required=True)
    parser.add_argument(
        "--task-ids",
        default=None,
        help="comma-separated task ids to flip (e.g. 2.1,3.1); default: all pending",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    task_ids = args.task_ids.split(",") if args.task_ids else None
    res = flip_and_archive(Path(args.worktree), args.change_id, task_ids)

    if args.json:
        print(json.dumps(res))
    elif res["error"]:
        print(f"ERROR: {res['error']}")
    else:
        print(
            f"flipped={res['flipped']} already_checked={res['already_checked']} "
            f"archived={res['archived']}"
        )
    return 0 if res["archived"] else 1


if __name__ == "__main__":
    sys.exit(main())
