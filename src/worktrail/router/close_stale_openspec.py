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

This module scripts all three steps now: after flipping and archiving,
it lands the bookkeeping PR through the shared `land_pr` pipeline (commit,
compile-marker gate, preflight + labels, push, create/update PR, watch CI,
finish run record). This single shared pipeline replaces every dispatcher's
own hand-rolled commit/push/PR logic, ensuring correct label handling and
CI watching across all callers. It also does NOT decide which tasks are safe
to flip -- confirming that a task's shipped work is actually on `$BASE` (the
"confirm the spec's pending impl tasks are truly shipped" step SKILL.md's
close-stale row already documents) stays a judgment call for the dispatching
agent, since OpenSpec's `tasks.md` carries no per-task `files:` frontmatter
to check mechanically (unlike the devkit format's `TASK-*.md`) -- see
`taskformats/openspec/schema.py`'s module docstring.

`drain.py`'s `archive_openspec_change` is a different, non-reusable shape for
this: it creates its own worktree/branch, assumes checkboxes are already
flipped, and drives `gh pr create` directly for drain's own unattended
background sweep. Nothing here duplicates it -- this operates on a worktree
`worktrail-go` already created (`#fix-branch-worktree-setup`) and lands
through the shared pipeline.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..taskformats.openspec.schema import parse_tasks_md, set_task_checked
from . import dashboard
from .land_pr import LandRequest, land_pr


def _delta_precheck(
    worktree: Path,
    change_id: str,
    *,
    allow_delta_drift: bool,
    timeout: int,
) -> tuple[dict[str, Any], str | None]:
    """Read-only pre-check run before any checkbox flip (design.md D1-D3).

    Returns `(precheck, error)`. `precheck` is always fully populated;
    `error` names the first failing class (validate failure, missing
    canonical target, archived-sibling delta drift) or is None. Validate
    failure and missing canonical targets can never be overridden -- archive
    would fail on them anyway. Drift is time-based and may be stale for a
    delta reconciled but not yet committed, so `allow_delta_drift` bypasses
    that class alone and is recorded as `drift_allowed`.
    """
    change_dir = worktree / "openspec" / "changes" / change_id
    precheck: dict[str, Any] = {
        "validate_ok": False,
        "validate_output": "",
        "missing_canonical": [],
        "delta_drift": [],
        "drift_allowed": bool(allow_delta_drift),
    }

    proc = subprocess.run(
        ["openspec", "validate", change_id, "--strict"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(worktree),
        timeout=timeout,
    )
    precheck["validate_output"] = (proc.stdout or proc.stderr).strip()
    precheck["validate_ok"] = proc.returncode == 0

    # Missing canonical targets: MODIFIED/REMOVED headings and RENAMED FROM:
    # names must already exist in openspec/specs/<capability>/spec.md.
    # ADDED is not checked -- a brand-new capability has no canonical file yet.
    specs_root = change_dir / "specs"
    for delta_file in sorted(specs_root.glob("**/spec.md")):
        capability = str(delta_file.relative_to(specs_root).parent)
        canonical_file = worktree / "openspec" / "specs" / capability / "spec.md"
        canonical_names: set[str] = set()
        if canonical_file.is_file():
            canonical_names = {
                m.strip()
                for m in dashboard._OPENSPEC_REQUIREMENT.findall(
                    canonical_file.read_text(errors="ignore")
                )
            }
        text = delta_file.read_text(errors="ignore")
        for kind, body in dashboard._iter_openspec_delta_sections(text):
            if kind in {"MODIFIED", "REMOVED"}:
                names = [
                    m.strip() for m in dashboard._OPENSPEC_REQUIREMENT.findall(body)
                ]
            elif kind == "RENAMED":
                names = [
                    dashboard._rename_requirement_name(value)
                    for direction, value in dashboard._OPENSPEC_RENAME.findall(body)
                    if direction == "FROM"
                ]
            else:
                continue
            for name in names:
                if name not in canonical_names:
                    precheck["missing_canonical"].append(
                        {"capability": capability, "requirement": name, "kind": kind}
                    )

    precheck["delta_drift"] = dashboard._openspec_delta_drift(change_dir, worktree)

    if not precheck["validate_ok"]:
        return precheck, (
            f"openspec validate {change_id} --strict failed: "
            f"{precheck['validate_output']}"
        )
    if precheck["missing_canonical"]:
        first = precheck["missing_canonical"][0]
        return precheck, (
            f"missing canonical target: {first['kind']} requirement "
            f"{first['capability']}/{first['requirement']} not in "
            f"openspec/specs/{first['capability']}/spec.md"
        )
    if precheck["delta_drift"] and not allow_delta_drift:
        first = precheck["delta_drift"][0]
        return precheck, (
            f"archived-sibling delta drift: {first['capability']}/"
            f"{first['requirement']} overtaken by archived change "
            f"{first['archived_change_id']} (pass --allow-delta-drift to proceed)"
        )
    return precheck, None


def flip_and_archive(
    worktree: Path,
    change_id: str,
    task_ids: list[str] | None = None,
    *,
    allow_delta_drift: bool = False,
    timeout: int = 300,
) -> dict[str, Any]:
    """Run the delta pre-check, then flip the given (or every) task checkbox
    for `change_id` and run `openspec archive -y <change_id> --json` in
    `worktree`.

    Returns `{"checked": bool, "change_dir": str, "precheck": {...}|None,
    "flipped": [...], "already_checked": [...], "unknown_task_ids": [...],
    "archived": bool, "archive_output": str, "error": str|None}`.

    `precheck` is populated whenever `checked` is true (see `_delta_precheck`).
    A pre-check refusal returns before any flip, leaving the worktree
    unchanged so the caller can fix the delta and re-run the same command.

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
        "precheck": None,
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

    precheck, precheck_error = _delta_precheck(
        worktree, change_id, allow_delta_drift=allow_delta_drift, timeout=timeout
    )
    result["precheck"] = precheck
    if precheck_error is not None:
        result["error"] = precheck_error
        return result

    parsed = parse_tasks_md(tasks_md.read_text())
    if task_ids is None:
        # Every task id, not just the pending ones: a change whose tasks.md is
        # already 100% `[x]` but was never archived is pure bookkeeping drift --
        # the exact case this module exists to close. A pending-only default
        # leaves both `flipped` and `already_checked` empty for it, so the
        # "nothing to flip" guard below wrongly refuses to archive it.
        targets = [t.id for t in parsed.tasks]
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
        check=False,
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
    parser.add_argument(
        "--allow-delta-drift",
        action="store_true",
        help="proceed despite archived-sibling delta drift (the only pre-check "
        "class that can be overridden; validate failure and missing canonical "
        "targets always refuse)",
    )
    parser.add_argument("--base", required=True, help="base branch for landing")
    parser.add_argument("--run", required=True, help="run record path")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    task_ids = args.task_ids.split(",") if args.task_ids else None
    res = flip_and_archive(
        Path(args.worktree),
        args.change_id,
        task_ids,
        allow_delta_drift=args.allow_delta_drift,
    )

    if res["error"]:
        # flip_and_archive failed; do not attempt to land
        if args.json:
            print(json.dumps(res))
        else:
            print(f"ERROR: {res['error']}")
        return 1

    # Successful flip and archive; land the PR through the shared pipeline
    landing_request = LandRequest(
        repo=args.worktree,
        base_branch=args.base,
        title=f"chore({args.change_id}): close stale bookkeeping",
        summary="Archive completed OpenSpec change",
        route="E",
        risk="low",
        run=args.run,
        commit_message=f"chore({args.change_id}): archive completed change",
    )
    landing_outcome = land_pr(landing_request)

    # Merge landing outcome into result
    res["landing"] = asdict(landing_outcome)

    if args.json:
        print(json.dumps(res))
    else:
        print(
            f"flipped={res['flipped']} already_checked={res['already_checked']} "
            f"archived={res['archived']} landing={landing_outcome.outcome}"
        )

    # Map landing outcome to exit code
    exit_code_map = {
        "landed": 0,
        "refused": 2,
        "code_defect": 3,
        "review_threads_blocking": 3,
        "ceiling": 4,
    }
    return exit_code_map.get(landing_outcome.outcome, 1)


if __name__ == "__main__":
    sys.exit(main())
