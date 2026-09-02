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
width (how many tasks could ever run at once). `format_warning()` turns a
fully-serialised plan into one actionable line recommending consolidation of
the tasks that share the hot file. `estimate_minutes()` multiplies the chain
length by the historical mean per-task cost read from prior run journals, so
the warning carries a wall-clock number rather than an abstract shape.

This is advisory: it never changes a plan and never fails a compile.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from worktrail.orchestrator.coordinator import TAIL_KINDS, compute_levels

# A serial chain this long is where one-at-a-time execution stops being a
# footnote and starts being the whole run's wall-clock. Shorter chains are
# normal (a 3-task change usually *is* sequential) and not worth a warning.
SERIAL_WARN_MIN_TASKS = 6

# "Effectively serial": the critical path covers at least this fraction of the
# fan-out tasks. Strict width==1 is not enough -- the incident change itself
# profiles as 16 tasks, critical path 13, width 2 (one stray pair ran side by
# side), and it still took one task at a time for hours.
SERIAL_WARN_CHAIN_FRACTION = 0.75

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

    @property
    def serialized(self) -> bool:
        """Effectively serial: long enough to matter, and the critical path is
        (nearly) the whole task list. Width 1 is always a subset of this."""
        return (
            self.tasks >= SERIAL_WARN_MIN_TASKS
            and self.critical_path >= self.tasks * SERIAL_WARN_CHAIN_FRACTION
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tasks": self.tasks,
            "critical_path": self.critical_path,
            "width": self.width,
            "serialized": self.serialized,
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


def format_warning(prof: Profile, minutes: float, basis: str) -> str | None:
    """One actionable line when the plan is effectively serial, else None."""
    if not prof.serialized:
        return None
    hours = minutes / 60.0
    hot = (
        f" Most tasks declare {', '.join(prof.hot_files)}; consolidate them into "
        "fewer, coarser tasks in tasks.md so the same-file ordering stops "
        "serialising the run."
        if prof.hot_files
        else " Consolidate the chain into fewer, coarser tasks in tasks.md."
    )
    return (
        f"WARN: task DAG is effectively serial ({prof.tasks} tasks, critical path "
        f"{prof.critical_path}, width {prof.width}): max-workers cannot help, "
        f"projected wall-clock ~{hours:.1f} h ({basis}).{hot}"
    )
