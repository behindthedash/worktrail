"""Post-hoc: did a task's declared RunPlan file scope match what it actually
delivered?

`compile.py`'s model pass decides each task's `files` in relative isolation
(design note in `compile.PROMPT`), so a shared file can end up recorded on
only one of two tasks that both touch it. `coordinator.runnable_frontier` and
`disjoint_batches` (`../orchestrator/coordinator.py`) correctly serialise or
group tasks on whatever `files` the plan declared -- they have nothing to
catch an omission the plan itself never recorded.

This module closes that visibility gap after the fact, not during the run: it
diffs each task's branch (still on disk, not yet torn down) against what the
plan predicted, and reports the mismatch. It is deliberately **not** wired
into the live dispatch loop -- a forensic/tuning signal for the compile
prompt must never gate or slow a run down. Run it by hand, or from a
post-run check, any time before the task branches are cleaned up.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from worktrail.conductor import runplan
from worktrail.orchestrator import worktree


@dataclass(frozen=True)
class FileMismatch:
    """One task whose branch disagreed with its declared plan scope."""

    task_id: str
    declared: tuple
    actual: tuple
    undeclared: tuple  # actual - declared: a real collision the plan missed
    unused: tuple  # declared - actual: predicted but never touched


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


def _ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", ref).returncode == 0


def _touched_files(repo: Path, base_ref: str, branch_ref: str) -> list[str] | None:
    """Files `branch_ref` changed relative to its merge-base with `base_ref`.

    None means the diff could not be computed (unreachable ref, no common
    ancestor) -- distinct from an empty list, which means a real, empty diff.
    """
    merge_base = _git(repo, "merge-base", base_ref, branch_ref)
    if merge_base.returncode != 0:
        return None
    base_sha = merge_base.stdout.strip()
    diff = _git(repo, "diff", "--name-only", f"{base_sha}..{branch_ref}")
    if diff.returncode != 0:
        return None
    return [f for f in diff.stdout.splitlines() if f]


def audit_plan(
    repo: str | Path, spec_id: str, plan: runplan.RunPlan, base_ref: str
) -> list[FileMismatch]:
    """Compare each task's declared `plan` file scope against what its branch
    actually changed.

    Best-effort and read-only: a task whose branch no longer exists (already
    integrated and torn down, or never dispatched) is silently skipped. This
    is a forensic tool, not a live gate -- a missing ref is not a mismatch,
    it is just nothing left to check.
    """
    repo = Path(repo)
    mismatches: list[FileMismatch] = []
    for tp in plan.tasks:
        if not tp.files:
            continue  # nothing declared to compare against
        branch = worktree.task_branch(spec_id, tp.id)
        if not _ref_exists(repo, branch):
            continue
        touched = _touched_files(repo, base_ref, branch)
        if touched is None:
            continue
        declared = set(tp.files)
        actual = set(touched)
        undeclared = tuple(sorted(actual - declared))
        unused = tuple(sorted(declared - actual))
        if undeclared or unused:
            mismatches.append(
                FileMismatch(
                    task_id=tp.id,
                    declared=tuple(sorted(declared)),
                    actual=tuple(sorted(actual)),
                    undeclared=undeclared,
                    unused=unused,
                )
            )
    return mismatches


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Post-hoc: diff each task's delivered files against its compiled RunPlan "
            "file scope. A log-only signal for compile-prompt tuning -- never blocks "
            "a run, and a task whose branch is already gone is skipped, not flagged."
        )
    )
    ap.add_argument(
        "spec",
        help="path to the spec or OpenSpec change directory (same argument worktrail-compile takes)",
    )
    ap.add_argument(
        "base_ref",
        help="ref the task branches were forked from and will merge into (e.g. the spec's base branch)",
    )
    ap.add_argument(
        "--cache-dir", default=None, help="override the plan cache location"
    )
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    from worktrail.conductor import compile as conductor_compile
    from worktrail.taskformats import resolve

    spec_dir = Path(a.spec).resolve()
    if not spec_dir.is_dir():
        print(f"no such spec directory: {spec_dir}", file=sys.stderr)
        return 1

    spec_id, tasks = resolve.load_spec(str(spec_dir))
    repo = conductor_compile._git_repo_root(spec_dir)
    if repo is None:
        print(f"not inside a git repository: {spec_dir}", file=sys.stderr)
        return 1

    cache_dir = a.cache_dir or conductor_compile.default_cache_dir(repo)
    fp = runplan.fingerprint(spec_dir, tasks)
    plan = runplan.load_cached(cache_dir, spec_id, fp)
    if plan is None:
        print(
            f"no cached plan for {spec_id}@{fp[:12]} in {cache_dir} -- run worktrail-compile first",
            file=sys.stderr,
        )
        return 1

    mismatches = audit_plan(repo, spec_id, plan, a.base_ref)

    if a.json:
        print(json.dumps([asdict(m) for m in mismatches], indent=2, sort_keys=True))
        return 0

    if not mismatches:
        print(
            "no mismatches: every checked task's branch matched its declared file scope"
        )
        return 0
    for m in mismatches:
        print(f"{m.task_id}:")
        if m.undeclared:
            print(f"  under-declared (touched, not planned): {', '.join(m.undeclared)}")
        if m.unused:
            print(f"  unused (planned, never touched): {', '.join(m.unused)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
