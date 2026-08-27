#!/usr/bin/env python3
"""
Mechanical pre-check for the openspec-propose headless spawn (`#openspec-propose`
in subagent-prompts.md).

That spawn routinely takes several minutes to author a change's full
proposal/delta-specs/design/tasks set, and 2026-08-15 PR #450 required
`run_in_background` after a foreground default-timeout kill discarded partial
work mid-generation. `run_in_background` only removes the *foreground-timeout*
trigger -- an OOM kill, a host disconnect, or a spawn that legitimately
outlives even a generous background execution still kills the child with
partial artifacts already written to disk and nothing tracking what's done.

The killed child's file writes are not lost -- `Write`/`Edit` tool calls flush
to disk immediately, so `openspec/changes/<change-id>/` on the worktree
retains whatever the child finished before it died. The actual gap is that
re-dispatching `#openspec-propose` blindly re-invokes `/opsx:propose` for the
same change-id, which hits OpenSpec's own change-name-collision guardrail
(`openspec new change` refuses an existing name -- verified against
@fission-ai/openspec's `validateChangeName`) instead of continuing the work.
There was no way to tell "this change-id already has partial artifacts, resume
with `/opsx:update` instead" from "this change-id is untouched, `/opsx:propose`
is safe to run" without this check.

Pure filesystem check, no live I/O boundary -- unlike `check_resumable_state.py`,
there is no `gh` lookup here, so `checked=True`
whenever the worktree path itself is reachable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

REQUIRED_ARTIFACTS = ("proposal.md", "design.md", "tasks.md")


def check(worktree: Path, change_id: str) -> Dict[str, Any]:
    """Does `<worktree>/openspec/changes/<change_id>/` already carry partial
    artifacts from an interrupted openspec-propose spawn?

    Returns `{"checked": bool, "exists": bool, "present": [...],
    "missing": [...], "has_specs": bool, "resumable": bool, "warning": str|None}`.

    `checked=False` means the worktree path itself could not be reached --
    the caller should treat this as unknown, not as "safe to run fresh"; the
    normal case is the worktree was just created by `#fix-branch-worktree-setup`
    / `#spec-worktree-setup` and is always resolvable.

    `resumable=True` means at least one artifact (`proposal.md`, `design.md`,
    `tasks.md`, or any file under `specs/`) already exists -- the caller should
    dispatch `/opsx:update <change-id>` (documented at `#openspec-explore`,
    "revise the existing artifacts, keeps them coherent") instead of
    `/opsx:propose`. `exists=True` with nothing written (e.g. an empty change
    directory from `git worktree add` alone, no authoring yet) is not
    resumable -- there is nothing to resume from, and `/opsx:propose` runs
    normally into the empty directory.
    """
    worktree = Path(worktree)
    result: Dict[str, Any] = {
        "checked": False,
        "exists": False,
        "present": [],
        "missing": list(REQUIRED_ARTIFACTS),
        "has_specs": False,
        "resumable": False,
        "warning": None,
    }
    if not worktree.is_dir():
        result["warning"] = f"worktree path does not exist: {worktree}"
        return result

    result["checked"] = True
    change_dir = worktree / "openspec" / "changes" / change_id
    if not change_dir.is_dir():
        return result

    result["exists"] = True
    present = [name for name in REQUIRED_ARTIFACTS if (change_dir / name).is_file()]
    result["present"] = present
    result["missing"] = [name for name in REQUIRED_ARTIFACTS if name not in present]

    specs_dir = change_dir / "specs"
    result["has_specs"] = specs_dir.is_dir() and any(specs_dir.rglob("*.md"))

    result["resumable"] = bool(present) or result["has_specs"]
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--worktree", required=True,
        help="the change worktree path (the --cwd passed to worktrail-skill-dispatch)",
    )
    parser.add_argument("--change-id", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    res = check(Path(args.worktree), args.change_id)
    if args.json:
        print(json.dumps(res))
    elif not res["checked"]:
        print(f"unknown: {res['warning']}")
    elif res["resumable"]:
        print(
            f"RESUMABLE: present={res['present']} has_specs={res['has_specs']} "
            f"missing={res['missing']} -- dispatch /opsx:update, not /opsx:propose"
        )
    else:
        print("not resumable: no partial artifacts found; safe to run a fresh openspec-propose")
    return 0


if __name__ == "__main__":
    sys.exit(main())
