"""How parallel is a compiled task DAG, and roughly how long will it take?

Why this exists
---------------
`compile.py` prints the merged dependency table and a note like
"auto-repaired 32 ordering edge(s)", but it never turns those into a signal.
On 2026-09-01 `intake-to-spec-triage` compiled 19 tasks that all declared the
same file, so the same-file repair serialised every one of them: `max_workers`
was irrelevant, each task cost ~50 min through the implement/review/fix loop,
and the ~16 h projection was discovered four hours into the run instead of
before launch. Nothing was *wrong* with the plan -- it was correct and slow --
which is exactly why no existing check fired.

`profile()` reduces a merged task list to two numbers a human can act on
before launching: the critical-path length (the longest dependency chain,
which bounds wall-clock no matter how many workers run) and the parallel
width (how many tasks could ever run at once). `estimate_minutes()`
multiplies the chain length by the historical mean per-task cost read from
prior run journals, so a plan's wall-clock projection carries a real number
rather than an abstract shape.

`shape_problems()` is the enforcement half: design D2's three rules (a
critical path too long relative to its own width, a same-file dependency
chain past a threshold, and an implementation task that skipped a test
counterpart that already exists on disk) returned as human-readable problem
lines. Unlike `profile()`/`estimate_minutes()`, this is not advisory --
`compile.py` raises `PlanShapeError` when it returns anything, and a rejected
plan writes no marker.

"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worktrail.orchestrator.coordinator import TAIL_KINDS, compute_levels

# Defaults for shape_problems()'s two policy-tunable thresholds, used when the
# repo's policy dict does not set `compile_max_critical_path_over_width` /
# `compile_max_same_file_chain`.
DEFAULT_MAX_CRITICAL_PATH_OVER_WIDTH = 2
DEFAULT_MAX_SAME_FILE_CHAIN = 2

# Per-task cost assumed when no prior run journal on this machine carries
# timing data yet. Calibrated from the incident run (41-58 min per task with
# the 3-strike review loop) rounded down, so a cold estimate is not alarmist.
DEFAULT_TASK_MINUTES = 40.0


@dataclass(frozen=True)
class Profile:
    tasks: int  # fan-out tasks considered (tail kinds excluded)
    critical_path: int  # longest dependency chain, in tasks
    width: int  # most tasks that could ever run concurrently
    hot_files: tuple[str, ...]  # files declared by more than half the tasks

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": self.tasks,
            "critical_path": self.critical_path,
            "width": self.width,
            "hot_files": list(self.hot_files),
        }


def profile(tasks: Sequence[dict[str, Any]]) -> Profile:
    """Shape of the fan-out DAG for *merged* tasks (after `apply_to_tasks`).

    Tail kinds are excluded, matching `unordered_file_collisions()`: they run
    after the fan-out and never compete for a worker slot. Width is the size
    of the largest dependency level -- the same levels `compute_levels` gives
    `runnable_frontier` -- which is the upper bound on concurrent workers for
    a scheduler that releases a level as its predecessors finish. It ignores
    file locks, so it can only over-state parallelism; a plan reported as
    serialised here is serialised at runtime too.
    """
    fanout = [t for t in tasks if t.get("kind") not in TAIL_KINDS]
    if not fanout:
        return Profile(0, 0, 0, ())
    ids = {t["id"] for t in fanout}
    scoped = [
        {**t, "deps": [d for d in t.get("deps") or [] if d in ids]} for t in fanout
    ]
    levels = compute_levels(scoped)
    per_level: dict[int, int] = {}
    for lvl in levels.values():
        per_level[lvl] = per_level.get(lvl, 0) + 1
    writers: dict[str, int] = {}
    for t in fanout:
        for f in sorted(set(t.get("files") or [])):
            writers[f] = writers.get(f, 0) + 1
    hot = tuple(sorted(f for f, n in writers.items() if n * 2 > len(fanout)))
    return Profile(
        tasks=len(fanout),
        critical_path=max(levels.values()) + 1,
        width=max(per_level.values()),
        hot_files=hot,
    )


def estimate_minutes(prof: Profile, journals: Iterable[Path] = ()) -> tuple[float, str]:
    """Projected wall-clock in minutes for the critical path, plus its basis.

    The per-task cost is the mean of every prior task's total recorded time
    (all roles: implement, review, fix, ...) across the given run journals --
    the same `duration_s` entries `progress.py` documents live.py stamping.
    Returns `(minutes, basis)` where *basis* says whether the mean came from
    history (and how much) or from `DEFAULT_TASK_MINUTES`, so the printed
    estimate never presents a guess as a measurement.
    """
    per_task_s: dict[tuple[str, str], float] = {}
    for jp in journals:
        try:
            journal = json.loads(Path(jp).read_text())
        except (OSError, ValueError):
            continue
        entries = journal.get("entries") if isinstance(journal, dict) else None
        for e in entries or []:
            if not isinstance(e, dict) or not e.get("duration_s"):
                continue
            key = (str(jp), str(e.get("task")))
            per_task_s[key] = per_task_s.get(key, 0.0) + float(e["duration_s"])
    if per_task_s:
        mean = sum(per_task_s.values()) / len(per_task_s) / 60.0
        basis = f"mean of {len(per_task_s)} prior task(s) on this machine"
    else:
        mean = DEFAULT_TASK_MINUTES
        basis = "no prior run journals with timing; default"
    return prof.critical_path * mean, f"{mean:.0f} min/task, {basis}"


def journals_beside(repo: Path) -> list[Path]:
    """Every run journal `live.py` has written beside this repo's worktrees."""
    return sorted((repo.parent / f"{repo.name}-worktrees").glob("run-*.json"))


def summary_line(prof: Profile) -> str:
    return (
        f"parallelism: {prof.tasks} task(s), critical path {prof.critical_path}, "
        f"width {prof.width}"
    )


def _longest_chain(subset: Iterable[str], ancestors: dict[str, set[str]]) -> list[str]:
    """One longest totally-or-partially ordered chain within *subset*.

    `ancestors[tid]` is every id (in the full merged graph, not just
    *subset*) that `tid` transitively depends on. A chain here is any
    sequence of `subset` ids where each is an ancestor of the next -- not
    necessarily connected by a direct dependency edge, since intermediate
    tasks outside *subset* (e.g. other files' writers) can sit between two
    same-file writers without breaking their ordering.
    """
    ids = set(subset)
    order = sorted(ids, key=lambda tid: len(ancestors[tid] & ids))
    best_len: dict[str, int] = {}
    best_prev: dict[str, str | None] = {}
    for tid in order:
        preds = [p for p in ids if p != tid and p in ancestors[tid]]
        if preds:
            best_pred = max(preds, key=lambda p: best_len[p])
            best_len[tid] = best_len[best_pred] + 1
            best_prev[tid] = best_pred
        else:
            best_len[tid] = 1
            best_prev[tid] = None
    end = max(order, key=lambda tid: best_len[tid])
    chain: list[str] = []
    cur: str | None = end
    while cur is not None:
        chain.append(cur)
        cur = best_prev[cur]
    chain.reverse()
    return chain


def shape_problems(
    merged: Sequence[dict[str, Any]], repo: str | Path, policy: dict[str, Any]
) -> list[str]:
    """Design D2's three plan-shape rules, as human-readable problem lines.

    Called on the *merged* task list (post `runplan.apply_to_tasks`), the
    same precondition `unordered_file_collisions` documents: two tasks
    declaring the same file are already ordered by a dependency edge (direct
    or transitive), so restricting to one file's writers always yields a
    totally-ordered chain, not merely a partial one.

    Tail kinds are excluded (they run after the fan-out), and so are tasks
    already `status: "completed"` -- all three rules judge the work still
    ahead of the run, so a chain or a same-file pile-up that has already been
    executed must not fail a re-compile.

    An empty result means the plan is fine; any non-empty result is meant to
    fail the compile (`compile.py` raises `PlanShapeError`), not just warn.
    """
    repo = Path(repo)
    fanout = [
        t
        for t in merged
        if t.get("kind") not in TAIL_KINDS and t.get("status") != "completed"
    ]
    if not fanout:
        return []

    max_ratio = policy.get(
        "compile_max_critical_path_over_width", DEFAULT_MAX_CRITICAL_PATH_OVER_WIDTH
    )
    max_same_file = policy.get(
        "compile_max_same_file_chain", DEFAULT_MAX_SAME_FILE_CHAIN
    )

    ids = {t["id"] for t in fanout}
    by_id = {
        t["id"]: {**t, "deps": [d for d in t.get("deps") or [] if d in ids]}
        for t in fanout
    }

    ancestors: dict[str, set[str]] = {}

    def ancestors_of(tid: str, path: frozenset) -> set[str]:
        if tid in ancestors:
            return ancestors[tid]
        if tid in path:  # a cycle here is apply_to_tasks's failure to report, not ours
            return set()
        found: set[str] = set()
        for d in by_id[tid]["deps"]:
            found.add(d)
            found |= ancestors_of(d, path | {tid})
        ancestors[tid] = found
        return found

    for tid in by_id:
        ancestors_of(tid, frozenset())

    levels = compute_levels(list(by_id.values()))
    per_level: dict[int, int] = {}
    for lvl in levels.values():
        per_level[lvl] = per_level.get(lvl, 0) + 1
    critical_path = max(levels.values()) + 1
    width = max(per_level.values())

    problems: list[str] = []

    threshold = max(width, max_ratio)
    if critical_path > threshold:
        chain = _longest_chain(by_id, ancestors)
        problems.append(
            f"serial: critical path {critical_path} exceeds max(width {width}, "
            f"{max_ratio}): {' -> '.join(chain)}"
        )

    writers: dict[str, set[str]] = {}
    for tid, t in by_id.items():
        for f in t.get("files") or []:
            writers.setdefault(f, set()).add(tid)
    for f in sorted(writers):
        subset = writers[f]
        if len(subset) <= max_same_file:
            continue
        chain = _longest_chain(subset, ancestors)
        if len(chain) > max_same_file:
            problems.append(
                f"same-file chain: {' -> '.join(chain)} all declare {f} "
                f"({len(chain)} > {max_same_file})"
            )

    for t in fanout:
        if t.get("kind") == "docs":
            continue
        files = t.get("files") or []
        src_files = sorted(f for f in files if f.startswith("src/"))
        if not src_files or any(f.startswith("tests/") for f in files):
            continue
        for f in src_files:
            stem = Path(f).stem
            matches = sorted(repo.glob(f"tests/**/test_{stem}*.py"))
            if not matches:
                continue
            try:
                test_name = matches[0].relative_to(repo)
            except ValueError:
                test_name = matches[0]
            problems.append(
                f"missing test scope: {t['id']} touches {f} with no tests/ path, "
                f"but {test_name} already exists"
            )
            break

    return problems
