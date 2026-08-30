#!/usr/bin/env python3
"""
CI's structural half of the Route C scope-check gate.

`pipeline-details.md`'s Route C step 3 already runs `worktrail-compile` against
an OpenSpec change directory before the docs-only spec PR is pushed -- but that
is an agent-followed doc instruction, not a structural guarantee (PR #147). An
agent that skips step 3, or a human who hand-edits `tasks.md` after it ran,
still produces a mergeable PR with an under-scoped plan -- reproduced directly
in datalena PR #2128 -> #2129 conflict -> #2130 recovery, 2026-08-05/06.

A CI job cannot re-run the real check: `worktrail-compile`'s OpenSpec path has
no static per-task file scope, so a genuine scope-gap check requires an LLM
call (`spawn_agent`), and this repo/org has no agent-CLI credentials wired
into CI. Instead, this module re-derives the same deterministic content
fingerprint `compile.py` already uses (`runplan.fingerprint`, no model call)
and compares it against the marker (`compile.COMPILE_MARKER_NAME`) that a
passing local/agent compile wrote into the change directory. This catches
"compile was never run for this content" and "tasks.md changed after compile
passed" -- not a fresh scope-gap discovery a live compile might surface for
content nobody has compiled yet.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from worktrail.conductor import compile as conductor_compile
from worktrail.conductor import runplan

TASKS_GLOB_SUFFIX = ("openspec", "changes")


def _run_git(repo: Path, args: Sequence[str], timeout: int = 15) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def changed_change_dirs(
    repo: str | Path, base_ref: str, head_ref: str = "HEAD"
) -> list[Path]:
    """OpenSpec change directories touched by `tasks.md` changes between two refs.

    Best-effort: an unresolvable `base_ref` (shallow clone missing history,
    unknown ref) yields an empty list rather than raising -- the caller decides
    whether "nothing to check" is a pass or a configuration problem.
    """
    repo = Path(repo)
    # A single `*` (not `**`) -- git pathspec glob matching for `**` needs
    # `:(glob)` magic, and every real change directory is exactly one level
    # deep (`openspec/changes/<id>/tasks.md`) anyway.
    out = _run_git(
        repo,
        [
            "diff",
            "--name-only",
            f"{base_ref}...{head_ref}",
            "--",
            "openspec/changes/*/tasks.md",
        ],
    )
    if out is None:
        return []
    dirs: list[Path] = []
    seen = set()
    for line in out.splitlines():
        line = line.strip()
        if not line or not line.endswith("/tasks.md"):
            continue
        change_dir = repo / Path(line).parent
        parts = change_dir.relative_to(repo).parts
        if (
            len(parts) < 3
            or parts[0] != TASKS_GLOB_SUFFIX[0]
            or parts[1] != TASKS_GLOB_SUFFIX[1]
        ):
            continue
        # `openspec archive` moves a change one level deeper, under
        # openspec/changes/archive/<name>/ -- matches dashboard.py's own
        # `d.name != "archive"` convention for the same directory. An
        # archived change's tasks.md is historical, not a still-live plan;
        # resolve._split() assumes the fixed openspec/changes/<id> depth and
        # mis-splits a deeper archived path (worktrail PR #206).
        if parts[2] == "archive":
            continue
        if change_dir in seen:
            continue
        seen.add(change_dir)
        if (change_dir / "tasks.md").is_file():
            dirs.append(change_dir)
    return dirs


def check_marker(change_dir: str | Path) -> dict[str, Any]:
    """Does `change_dir` carry a compile marker matching its current content?

    Returns `{"change": str, "status": "ok"|"missing"|"stale",
    "expected_fingerprint": str, "marker_fingerprint": str|None}`.
    """
    from worktrail.taskformats import resolve

    change_dir = Path(change_dir)
    spec_id, tasks = resolve.load_spec(str(change_dir))
    expected = runplan.fingerprint(change_dir, tasks)

    marker = conductor_compile.marker_path(change_dir)
    if not marker.is_file():
        return {
            "change": spec_id,
            "status": "missing",
            "expected_fingerprint": expected,
            "marker_fingerprint": None,
        }

    recorded = marker.read_text(encoding="utf-8").strip()
    status = "ok" if recorded == expected else "stale"
    return {
        "change": spec_id,
        "status": status,
        "expected_fingerprint": expected,
        "marker_fingerprint": recorded,
    }


def check(
    repo: str | Path,
    base_ref: str,
    head_ref: str = "HEAD",
    change_dirs: Sequence[str | Path] | None = None,
) -> dict[str, Any]:
    """Check every OpenSpec change touched between `base_ref` and `head_ref`.

    `change_dirs`, when given, replaces git-diff discovery entirely (tests,
    or a caller that already knows the touched changes) -- `base_ref`/`head_ref`
    are then unused for discovery but still echoed back for context.
    """
    dirs = (
        [Path(d) for d in change_dirs]
        if change_dirs is not None
        else changed_change_dirs(repo, base_ref, head_ref)
    )
    results = [check_marker(d) for d in dirs]
    failing = [r for r in results if r["status"] != "ok"]
    return {
        "repo": str(repo),
        "base_ref": base_ref,
        "checked": results,
        "passed": not failing,
    }


def _report_errors(result: dict[str, Any]) -> None:
    """Stderr only -- shared by both output modes, same convention as
    `compile.py`'s own `_print_scope_gap_error`, so `--json` stdout stays a
    clean, parseable payload even when the exit code reports failure."""
    for r in result["checked"]:
        if r["status"] == "missing":
            print(
                f"ERROR: {r['change']}: no compile marker found -- run "
                f"`worktrail-compile openspec/changes/{r['change']}` and commit "
                f"`.compile-ok` before pushing.",
                file=sys.stderr,
            )
        elif r["status"] == "stale":
            print(
                f"ERROR: {r['change']}: compile marker is stale (tasks.md changed since "
                f"the last passing compile) -- re-run `worktrail-compile "
                f"openspec/changes/{r['change']}` and commit the updated `.compile-ok`.",
                file=sys.stderr,
            )


def _report_summary(result: dict[str, Any]) -> None:
    if not result["checked"]:
        print("no openspec/changes/*/tasks.md changes in this diff -- nothing to check")
    elif result["passed"]:
        print(f"OK: {len(result['checked'])} change(s) carry a fresh compile marker")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--base-ref", required=True, help="e.g. origin/main")
    ap.add_argument("--head-ref", default="HEAD")
    ap.add_argument(
        "--change-dir",
        action="append",
        default=None,
        help="check this change directory instead of discovering from git diff "
        "(repeatable)",
    )
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    result = check(a.repo, a.base_ref, a.head_ref, change_dirs=a.change_dir)

    if a.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        _report_summary(result)
    _report_errors(result)

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
