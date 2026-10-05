#!/usr/bin/env python3
"""Direct-mode eligibility gate for the modify pipeline (Route F/G).

A change that reduces to one small, mechanical fix does not need the
orchestrator's fan-out machinery: the modify pipeline's direct branch
implements the single task inline in the change worktree and lands one PR.
This module owns the eligibility verdict so that branch decision is
code-enforced, not prose-remembered -- the pipeline branches only on
`eligible`.

Read-only and deterministic: parses the change's `tasks.md` through the same
parser compile trusts, applies named caps, and never writes, spawns a worker,
or calls a model. Exit 0 whenever a verdict is computed (eligible OR
ineligible); a nonzero exit always means a usage error, never a verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from worktrail.taskformats.openspec.schema import parse_tasks_md

# The caps. Deliberately constants, not policy keys: direct mode is a
# mechanical-fix fast path, and anything bigger belongs to the orchestrated
# path with its worker isolation and independent review.
MAX_TASKS = 1
MAX_FILES = 3
MAX_ESTIMATED_CHANGED_LINES = 40

# A change touching any of these can never ride direct mode: routing/
# classification changes always take the full path (Route J's cassette rule).
# Directory entries end with `/` and match by prefix; the rest match exactly.
ROUTING_SURFACE = (
    "src/worktrail/router/classify.py",
    "src/worktrail/router/classify_handoff.py",
    "src/worktrail/router/risk_judgment.py",
    "src/worktrail/router/policy.py",
    "src/worktrail/router/routing_cli.py",
    "src/worktrail/router/cassettes/",
    "scripts/cassettes/",
)

# Stable reason tokens; `reason` always begins with one of these.
REASON_ELIGIBLE = "eligible"
REASON_CHANGE_SHAPE = "change_shape"
REASON_TASK_COUNT = "task_count"
REASON_FILES_UNDECLARED = "files_undeclared"
REASON_FILES_OVER_CAP = "files_over_cap"
REASON_ROUTING_SURFACE = "routing_surface"
REASON_DELTA_OVER_CAP = "delta_over_cap"


def _openspec_root(change_dir: Path) -> Path | None:
    """The nearest ancestor of `change_dir` (excluding itself) containing a
    `specs/` directory -- resolves both the `changes/<id>` and
    `changes/archive/<id>` layouts."""
    for parent in change_dir.resolve().parents:
        if (parent / "specs").is_dir():
            return parent
    return None


def _delta_spec_files(change_dir: Path) -> list[Path]:
    return sorted((change_dir / "specs").rglob("*.md"))


def _estimate_changed_lines(change_dir: Path, openspec_root: Path | None) -> int:
    """Non-blank stripped delta lines absent from the corresponding base spec.

    The corresponding base spec is `<openspec-root>/specs/<relpath under the
    change's specs/>`. A missing base spec (or no resolvable openspec root)
    counts every non-blank stripped line as new. Delta-format scaffolding
    (e.g. the `## MODIFIED Requirements` heading) deliberately counts as new
    and is never special-cased."""
    total = 0
    for spec_file in _delta_spec_files(change_dir):
        rel = spec_file.relative_to(change_dir / "specs")
        base_lines: set[str] = set()
        if openspec_root is not None:
            base = openspec_root / "specs" / rel
            if base.is_file():
                base_lines = {
                    line.strip()
                    for line in base.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                }
        for line in spec_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and stripped not in base_lines:
                total += 1
    return total


def _hits_routing_surface(files: list[str]) -> str | None:
    """The first declared file that equals or lies under a ROUTING_SURFACE
    entry, or None."""
    for f in files:
        norm = f.replace("\\", "/")
        for entry in ROUTING_SURFACE:
            if entry.endswith("/"):
                if norm.startswith(entry):
                    return f
            elif norm == entry:
                return f
    return None


def evaluate(change_dir: Path) -> dict:
    """The direct-mode verdict for `change_dir`.

    Conditions are evaluated in a fixed order, first failure deciding the
    reason: change_shape -> task_count -> files_undeclared -> files_over_cap
    -> routing_surface -> delta_over_cap -> eligible."""
    tasks_file = change_dir / "tasks.md"
    spec_files = _delta_spec_files(change_dir)

    if not tasks_file.is_file() or not spec_files:
        missing = "tasks.md" if not tasks_file.is_file() else "specs/**/spec.md"
        return {
            "eligible": False,
            "reason": f"{REASON_CHANGE_SHAPE}: missing {missing}",
            "task_count": 0,
            "files": [],
        }

    parsed = parse_tasks_md(tasks_file.read_text(encoding="utf-8"))
    files = sorted({f for t in parsed.tasks for f in t.files})

    if len(parsed.tasks) != MAX_TASKS:
        return {
            "eligible": False,
            "reason": (
                f"{REASON_TASK_COUNT}: {len(parsed.tasks)} task(s) vs cap {MAX_TASKS}"
            ),
            "task_count": len(parsed.tasks),
            "files": files,
        }

    if not files:
        return {
            "eligible": False,
            "reason": f"{REASON_FILES_UNDECLARED}: task declares no files:",
            "task_count": len(parsed.tasks),
            "files": [],
        }

    if len(files) > MAX_FILES:
        return {
            "eligible": False,
            "reason": (
                f"{REASON_FILES_OVER_CAP}: {len(files)} distinct files vs cap "
                f"{MAX_FILES}"
            ),
            "task_count": len(parsed.tasks),
            "files": files,
        }

    hit = _hits_routing_surface(files)
    if hit is not None:
        return {
            "eligible": False,
            "reason": f"{REASON_ROUTING_SURFACE}: declared file {hit}",
            "task_count": len(parsed.tasks),
            "files": files,
        }

    estimated = _estimate_changed_lines(change_dir, _openspec_root(change_dir))
    if estimated > MAX_ESTIMATED_CHANGED_LINES:
        return {
            "eligible": False,
            "reason": (
                f"{REASON_DELTA_OVER_CAP}: ~{estimated} changed lines vs cap "
                f"{MAX_ESTIMATED_CHANGED_LINES}"
            ),
            "task_count": len(parsed.tasks),
            "files": files,
        }

    return {
        "eligible": True,
        "reason": (
            f"{REASON_ELIGIBLE}: 1 task, {len(files)} file(s), "
            f"~{estimated} changed lines"
        ),
        "task_count": len(parsed.tasks),
        "files": files,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "change_dir",
        metavar="CHANGE-DIR",
        help="the OpenSpec change directory (openspec/changes/<id>)",
    )
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    change_dir = Path(args.change_dir)
    if not change_dir.is_dir():
        print(
            f"worktrail-modify-direct-gate: not a directory: {change_dir}",
            file=sys.stderr,
        )
        return 2

    verdict = evaluate(change_dir)
    if args.json:
        print(json.dumps(verdict))
    else:
        print(
            f"{verdict['reason']} "
            f"(tasks={verdict['task_count']}, files={len(verdict['files'])})"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
