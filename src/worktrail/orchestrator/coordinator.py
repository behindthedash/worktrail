#!/usr/bin/env python3
"""
Parallel SDD Orchestrator -- coordinator core (frontier + grouping).

Pure, side-effect-free PLANNING logic for the parallel orchestrator. It does NOT
spawn agents, touch git, or read the real filesystem. It operates on a plain list
of task dicts (the shape produced by spec-to-tasks / fix_plan.json):

    {
      "id":     "TASK-001",
      "deps":   ["TASK-000", ...],     # task IDs this depends on
      "files":  ["src/a.ts", ...],     # Files-to-Create/Modify from the task brief
      "status": "pending",             # pending|claimed|implementing|reviewing|
                                       # fixing|cleaning|done|completed|failed
      "kind":   "impl",                # impl | e2e | cleanup
      "reqs":   ["REQ-001", ...]       # requirement IDs (traceability-matrix)
    }

Two responsibilities:

  runnable_frontier(tasks, max_workers)
      Which PENDING tasks can run RIGHT NOW: all deps done, file-disjoint from
      in-flight tasks AND from each other, capped at max_workers. (Extends the
      framework's single-task get_next_pending_task into a parallel frontier.)

  plan_groups(tasks)
      Partition the task DAG into incremental delivery GROUPS (one PR per group):
      a foundational BASE group (roots many tasks build on) plus independent
      FEATURE groups. Feature groups have no dependency edges between them (any
      such edge merges them), so they are mutually parallel; each may stack on
      base. The global e2e + cleanup tasks are held out as a serialized TAIL.

Run `python3 coordinator.py demo` to see both on a toy spec.

Status: v0 prototype. See parallel-spec-orchestrator-design.md sections 4 and 13.2.
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import os
import sys
from typing import Any, Dict, List, Sequence

TAIL_KINDS = {"e2e", "cleanup"}
DONE = {"done", "completed"}
# Tasks already integrated into a group branch from a prior run (never re-merge these).
# "completed" is set when: (a) the cleanup prompt updates the task file on disk, or
# (b) --only pre-marks non-selected tasks so the frontier treats them as done but
# their worktree branches no longer exist and must not be re-merged.
# "done" = worker completed in the CURRENT run — branches exist and ARE deliverable.
ALREADY_INTEGRATED = {"completed"}
IN_FLIGHT = {"claimed", "implementing", "reviewing", "fixing", "cleaning"}


# --------------------------------------------------------------------------- #
# Frontier
# --------------------------------------------------------------------------- #
def _norm_files(files) -> set:
    """Normalised file set for collision detection: `./src/a.ts` and `src/a.ts`
    are the SAME file, so compare on os.path.normpath -- otherwise two tasks that
    declare the same file with different spellings run in parallel and collide at
    integration."""
    return {os.path.normpath(f) for f in (files or [])}


def tail_held_out_task_ids(tasks: List[Dict[str, Any]]) -> List[str]:
    """Tasks held out of the fan-out.

    This includes tail-kind tasks themselves plus pending non-tail tasks whose
    only unmet deps are tail-kind tasks. Those impl tasks cannot become runnable
    during the parallel fan-out because the tail runs later.
    """
    by_id = {t["id"]: t for t in tasks}
    held_out = set()
    for t in tasks:
        if t.get("status") != "pending":
            continue
        if t.get("kind") in TAIL_KINDS:
            held_out.add(t["id"])
            continue
        unmet = [
            dep
            for dep in t.get("deps", [])
            if dep in by_id and by_id[dep].get("status") not in DONE
        ]
        if unmet and all(by_id[dep].get("kind") in TAIL_KINDS for dep in unmet):
            held_out.add(t["id"])
    return sorted(held_out)


def runnable_frontier(tasks: List[Dict[str, Any]], max_workers: int) -> List[Dict[str, Any]]:
    """Pending tasks runnable now: deps satisfied + file-disjoint, capped."""
    done = {t["id"] for t in tasks if t.get("status") in DONE}
    in_flight = [t for t in tasks if t.get("status") in IN_FLIGHT]
    tail_ids = {t["id"] for t in tasks if t.get("kind") in TAIL_KINDS}

    # Files already being written by in-flight workers are locked (normalised).
    locked_files: set = set()
    for t in in_flight:
        locked_files |= _norm_files(t.get("files"))

    frontier: List[Dict[str, Any]] = []
    for t in tasks:
        if t.get("status") != "pending":
            continue
        if t["id"] in tail_ids:  # tail runs last, serialized
            continue
        if not all(d in done for d in t.get("deps", [])):
            continue
        # `external_deps_ok` is precomputed by live.py (contracts/frontier-external-deps-
        # gate.md) since resolving it requires a filesystem read this module must not do.
        # Defaults True so specs with no external-dependencies: entries are unaffected.
        if not t.get("external_deps_ok", True):
            continue
        files = _norm_files(t.get("files"))
        if locked_files & files:  # would collide on a file -> defer
            continue
        frontier.append(t)
        locked_files |= files
        if len(in_flight) + len(frontier) >= max_workers:
            break
    return frontier


def disjoint_batches(tasks: List[Dict[str, Any]], max_workers: int) -> List[List[Dict[str, Any]]]:
    """Greedily pack tasks into batches that may run concurrently: within a batch no
    two tasks share a (normalised) file, and a batch holds at most `max_workers`.

    Order-preserving and pure. Used to resume interrupted mid-flight tasks in
    parallel (those are not `pending`, so `runnable_frontier` never surfaces them)
    while still guaranteeing two tasks never write the same file at once.
    """
    cap = max(1, max_workers)
    batches: List[Dict[str, Any]] = []  # each: {"tasks": [...], "files": set}
    for t in tasks:
        files = _norm_files(t.get("files"))
        for b in batches:
            if len(b["tasks"]) < cap and not (b["files"] & files):
                b["tasks"].append(t)
                b["files"] |= files
                break
        else:
            batches.append({"tasks": [t], "files": set(files)})
    return [b["tasks"] for b in batches]


def simulate(tasks: List[Dict[str, Any]], max_workers: int):
    """Replay frontier ticks (assume each dispatched task finishes that tick).

    Returns (impl_batches, tail_ids). Pure: operates on a deep copy.
    """
    tasks = copy.deepcopy(tasks)
    by_id = {t["id"]: t for t in tasks}
    batches: List[List[str]] = []
    for _ in range(10_000):  # guard against cycles
        frontier = runnable_frontier(tasks, max_workers)
        if not frontier:
            break
        batches.append([t["id"] for t in frontier])
        for t in frontier:
            by_id[t["id"]]["status"] = "done"
    tail = [t["id"] for t in tasks if t.get("kind") in TAIL_KINDS]
    return batches, tail


# --------------------------------------------------------------------------- #
# Levels (topological depth) -- for display / sanity
# --------------------------------------------------------------------------- #
def compute_levels(tasks: List[Dict[str, Any]]) -> Dict[str, int]:
    by_id = {t["id"]: t for t in tasks}
    level: Dict[str, int] = {}

    def lvl(tid: str, seen: frozenset) -> int:
        if tid in seen:
            raise ValueError(f"dependency cycle through {tid}")
        if tid in level:
            return level[tid]
        deps = [d for d in by_id[tid].get("deps", []) if d in by_id]
        level[tid] = 0 if not deps else 1 + max(lvl(d, seen | {tid}) for d in deps)
        return level[tid]

    for t in tasks:
        lvl(t["id"], frozenset())
    return level


# --------------------------------------------------------------------------- #
# Grouping -> one PR per group
# --------------------------------------------------------------------------- #
def _reqs(ids, by_id) -> List[str]:
    out = set()
    for i in ids:
        out.update(by_id[i].get("reqs", []))
    return sorted(out)


def _touches_migration(task: Dict[str, Any], patterns: Sequence[str]) -> bool:
    """True if any of `task`'s declared files match a migration path pattern."""
    files = _norm_files(task.get("files"))
    return any(fnmatch.fnmatch(f, pat) for f in files for pat in patterns)


def group_contains_migration_task(
    group: Dict[str, Any], tasks: List[Dict[str, Any]], patterns: Sequence[str]
) -> bool:
    """True if any task in `group` (a `plan_groups()` entry) touches a
    migration path pattern -- i.e. this is (part of) why `plan_groups` folded
    it into BASE in the first place. Used to detect when BASE's migration-folding
    safety net (see `plan_groups`'s "Why migration tasks are forced into BASE"
    docstring) is itself quarantined, since that folding only cascades quarantine
    to groups with a *declared* dependency edge on BASE -- not to a group whose
    code merely consumes the new schema with no `deps`/shared-file edge.
    """
    if not patterns:
        return False
    by_id = {t["id"]: t for t in tasks}
    return any(
        _touches_migration(by_id[tid], patterns) for tid in group["tasks"] if tid in by_id
    )


def plan_groups(
    tasks: List[Dict[str, Any]], migration_patterns: Sequence[str] = ()
) -> List[Dict[str, Any]]:
    """Partition the DAG into a base group + independent feature groups.

    Algorithm:
      1. Hold out tail tasks (e2e/cleanup).
      2. BASE = impl roots (no intra-impl deps) with >= 2 transitive dependents
         -- the foundations many tasks build on -- **plus** any task whose
         declared files match `migration_patterns` (see below).
      3. Union-find the remaining FEATURE tasks over dependency edges **and
         shared-file edges**: two feature tasks merge if either one depends on
         the other, or they write the same file. Result: feature groups are
         mutually dependency-independent AND file-disjoint, so their PRs merge
         into base without touching each other's files.
      4. A group stacks on BASE if one of its tasks depends on a base task, or
         if it writes a file base also writes.

    Why shared-file edges matter (they are not a refinement of taste): a group
    is the PR unit. Two dependency-independent tasks that write the same file
    land in *different* groups, so two concurrent PRs both edit that file and
    collide at merge. Dependency-independence alone was never sufficient to make
    group PRs conflict-free; `disjoint_batches`/`runnable_frontier` prevented the
    concurrent *write* within a run but said nothing about the PR partition.

    Measured on the 81 spec dirs under ~/projects with declared file scope
    (2026-07-26): adding these edges eliminated all 35 cross-group file
    collisions and cost 8 of 213 groups. No spec newly collapsed to a single
    group -- file overlap inside a spec turns out to be sparse and mostly already
    aligned with the dependency structure, so no hub-file threshold is needed.

    Why migration tasks are forced into BASE: a schema migration and the code
    that reads/writes the tables it creates rarely share a `files` entry (the
    migration file and the consuming service/router file are different paths),
    so neither the dependency graph nor the shared-file union-find reliably
    catches the coupling -- it depends entirely on `deps` inference (compile.py's
    LLM pass, or hand-authored task frontmatter) correctly noticing an implicit
    "this task's code touches that migration's table" relationship, which is not
    dependable. A migration left in its own singleton/feature group can then be
    quarantined (e.g. on an unrelated flaky test) independently of consumer code
    that already merged and now depends on a table that doesn't exist on any
    already-migrated database -- observed on datalena's embed-widget-auth-hardening
    run (PRs #2138 base, #2139 feature-2, both merged; the migration task's own
    feature-1 group quarantined on an unrelated stale-head assertion and was never
    merged until the manual recovery in #2144, leaving dev broken in between).
    Folding migration tasks into BASE does not fix the missing-deps-edge root
    cause, but it removes the specific failure mode: BASE is the group everything
    else stacks on and merges first, so a migration quarantined inside BASE blocks
    the whole run rather than silently letting an independent, non-stacking
    sibling group merge around it. `migration_patterns` is empty by default (no
    behavior change for callers that don't pass it) -- each repo declares its own
    patterns via `docs/specs/worktrail-go-policy.yaml`'s `migration_path_patterns`, since
    migration tooling/paths are repo-specific (Alembic, Drizzle, Rails, etc.), not
    a worktrail convention.

    TODO: requirement-cluster labels; detect foundational cut-vertices beyond
    pure roots.
    """
    impl = [t for t in tasks if t.get("kind", "impl") not in TAIL_KINDS]
    by_id = {t["id"]: t for t in impl}
    ids = set(by_id)

    # forward edges: dep -> direct dependents (within impl)
    dependents: Dict[str, set] = {tid: set() for tid in ids}
    for t in impl:
        for d in t.get("deps", []):
            if d in dependents:
                dependents[d].add(t["id"])

    def transitive_dependents(tid: str) -> set:
        seen, stack = set(), list(dependents[tid])
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(dependents.get(x, ()))
        return seen

    base = {
        t["id"]
        for t in impl
        if not [d for d in t.get("deps", []) if d in ids]  # root within impl
        and len(transitive_dependents(t["id"])) >= 2  # fans out
    }
    if migration_patterns:
        base |= {t["id"] for t in impl if _touches_migration(t, migration_patterns)}

    # Absorb pure single-writer continuations of a BASE task INTO base, rather
    # than leaving them to become their own dependent group.
    #
    # A BASE task can qualify purely because it fans out (>=2 transitive
    # dependents), even when its *immediate* dependent chain is a serial
    # same-file rewrite with no independent branching of its own (e.g. 1.1 ->
    # 1.2 -> 1.3, all three touching one file, with a separate task 3.2 as the
    # actual fan-out consumer of the whole chain). Splitting that chain into
    # its own stacked group buys no parallelism -- it is still strictly
    # serialized behind base by the same-file edge -- but does create a race:
    # the pipeline scheduler's "base merges before dependents start" gate
    # leaves the chain's own group idle and blocked while base's PR fights a
    # merge conflict against advancing `main`. If base's resolve strikes
    # exhaust before the chain's group ever gets to integrate, the chain's
    # fully-done, reviewed, tested work is orphaned and the whole dependent
    # group cascades to quarantine with it -- confirmed live on worktrail's
    # own repo: run go-20260813-194636, group "base" (task 1.1 alone) PR #379
    # exhausted its conflict-resolve strikes while sibling tasks 1.2/1.3
    # (group "feature-1", blocked on base's done-event) finished concurrently
    # on their own task branches, requiring a manual rebuild of the group
    # branch from task 1.3's commit chain to recover.
    #
    # Only a *pure* continuation is absorbed: the dependent must have no other
    # unmet in-impl dep (a real join point, like a task consuming the whole
    # chain, is deliberately excluded -- it keeps genuine fan-out value as its
    # own group) and its own declared files must be a non-empty SUBSET of the
    # accumulated base file set (an unrelated or partially-overlapping file
    # set is new scope, not a continuation, and stays governed by the
    # existing shared-file stacking logic instead). If more than one
    # dependent of the same predecessor qualifies -- a genuine same-file fork,
    # not a chain -- none of them are absorbed: there is no safe ordering
    # between concurrent siblings to prefer, so grouping falls back to its
    # pre-existing (safe) behavior for that shape.
    base_files: set = set()
    for b in base:
        base_files |= _norm_files(by_id[b].get("files"))
    frontier = list(base)
    while frontier:
        tid = frontier.pop()
        candidates = []
        for dep_id in sorted(dependents.get(tid, ())):
            if dep_id in base:
                continue
            dep_task = by_id[dep_id]
            other_deps = [d for d in dep_task.get("deps", []) if d in ids and d != tid]
            if other_deps:
                continue
            dep_files = _norm_files(dep_task.get("files"))
            if dep_files and dep_files <= base_files:
                candidates.append(dep_id)
        if len(candidates) != 1:
            continue
        absorbed_id = candidates[0]
        base.add(absorbed_id)
        base_files |= _norm_files(by_id[absorbed_id].get("files"))
        frontier.append(absorbed_id)

    feat = ids - base
    parent = {tid: tid for tid in feat}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for t in impl:
        if t["id"] not in feat:
            continue
        for d in t.get("deps", []):
            if d in feat:
                union(t["id"], d)

    # Shared-file edges. Grouped by file rather than compared pairwise: a file
    # written by k tasks contributes k-1 unions, not k*(k-1)/2 comparisons.
    feat_files = {tid: _norm_files(by_id[tid].get("files")) for tid in feat}
    writers: Dict[str, List[str]] = {}
    for tid in sorted(feat):
        for f in feat_files[tid]:
            writers.setdefault(f, []).append(tid)
    for co_writers in writers.values():
        for other in co_writers[1:]:
            union(co_writers[0], other)

    comps: Dict[str, set] = {}
    for tid in feat:
        comps.setdefault(find(tid), set()).add(tid)

    groups: List[Dict[str, Any]] = []
    if base:
        groups.append(
            {"name": "base", "tasks": sorted(base), "depends_on": [], "reqs": _reqs(base, by_id)}
        )
    base_files: set = set()
    for b in base:
        base_files |= _norm_files(by_id[b].get("files"))
    for idx, members in enumerate(sorted(comps.values(), key=lambda m: sorted(m)[0]), start=1):
        member_files: set = set()
        for m in members:
            member_files |= _norm_files(by_id[m].get("files"))
        # Sharing a file with base is enough on its own. Base merges first, so
        # stacking sequences the two; without this the group's PR and base's PR
        # are concurrent and both edit that file. Merging the group *into* base
        # would be wrong -- base is the shared foundation, not a catch-all.
        # (Verified real, not hypothetical: datalena spec 027's feature-2 writes
        # `package.json`, which base also writes, while depending on nothing in
        # base.)
        stacks_on_base = bool(base) and (
            any(d in base for m in members for d in by_id[m].get("deps", []))
            or bool(base_files & member_files)
        )
        groups.append(
            {
                "name": f"feature-{idx}",
                "tasks": sorted(members),
                "depends_on": ["base"] if stacks_on_base else [],
                "reqs": _reqs(members, by_id),
            }
        )
    return groups


def declared_files_by_group(
    groups: List[Dict[str, Any]], tasks: List[Dict[str, Any]]
) -> Dict[str, List[str]]:
    """`{group name: every file its tasks declare}`, normalised.

    Feeds the verify-stage deny-list, which needs to tell an out-of-scope edit
    apart from a deliverable. Spec 080 is the case that forced this: its whole
    purpose is modifying `.github/workflows/**`, a blanket-denied prefix, so its
    own deliverable read as a violation.
    """
    by_id = {t["id"]: t for t in tasks}
    out: Dict[str, List[str]] = {}
    for g in groups:
        files: set = set()
        for tid in g.get("tasks", []):
            t = by_id.get(tid)
            if t:
                files |= _norm_files(t.get("files"))
        out[g["name"]] = sorted(files)
    return out


FAILED_STATUSES = {"failed", "escalated"}


def deliverable_subset(
    group_task_ids: List[str],
    tasks: List[Dict[str, Any]],
    status: Dict[str, str],
) -> tuple:
    """Split a group into the tasks we can still ship vs. the ones to quarantine.

    A task is NOT deliverable if:
    - it failed/escalated, OR
    - it is already integrated (status in ALREADY_INTEGRATED, i.e. "completed" —
      set when (a) the cleanup prompt marks the task file on disk, or (b) the
      --only pre-mark excludes it because its worktree branch no longer exists), OR
    - any of its in-group dependencies failed/escalated.

    "done" tasks (worker completed in the current run, branch freshly committed)
    ARE deliverable — their branches must be merged into the group PR.

    Crucially, an independent, dependency-satisfied SIBLING of a failed task IS
    still deliverable. Dropping an ALREADY_INTEGRATED dependency does NOT
    cascade-drop its pending dependents — cascade only follows failed edges.

    Returns (deliverable_ids_sorted, dropped_ids_sorted).
    """
    group_set = set(group_task_ids)
    deps_by_id = {
        t["id"]: [d for d in t.get("deps", []) if d in group_set]
        for t in tasks
        if t["id"] in group_set
    }
    failed_set = {tid for tid in group_set if status.get(tid) in FAILED_STATUSES}
    # Tasks already on a group branch from a prior run — do not re-merge.
    integrated_set = {tid for tid in group_set if status.get(tid) in ALREADY_INTEGRATED}

    # Compute cascade-drops: tasks that depend on failed tasks (but not integrated tasks).
    # This closure includes failed tasks themselves plus any task that depends on them.
    failed_related_dropped = set(failed_set)
    changed = True
    while changed:
        changed = False
        for tid in group_set - failed_related_dropped:
            if any(d in failed_related_dropped for d in deps_by_id.get(tid, [])):
                failed_related_dropped.add(tid)
                changed = True

    # Non-deliverable = failed-related drops + already-integrated tasks
    non_deliverable = failed_related_dropped | integrated_set
    deliverable = group_set - non_deliverable
    return sorted(deliverable), sorted(non_deliverable)


def pr_plan(tasks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Human-readable PR delivery plan derived from plan_groups()."""
    groups = plan_groups(tasks)
    tail = [t["id"] for t in tasks if t.get("kind") in TAIL_KINDS]
    # Feature groups never depend on each other (any such edge would have merged
    # them), so every non-base group can run in parallel once base has merged.
    parallel = [
        g["name"] for g in groups if g["name"] != "base" and set(g["depends_on"]) <= {"base"}
    ]
    return {"groups": groups, "tail": tail, "parallel_after_base": parallel}


# --------------------------------------------------------------------------- #
# Demo
# --------------------------------------------------------------------------- #
def _toy_tasks() -> List[Dict[str, Any]]:
    return [
        {
            "id": "TASK-001",
            "deps": [],
            "files": ["db/schema.sql"],
            "kind": "impl",
            "reqs": ["REQ-001"],
        },  # foundation
        {
            "id": "TASK-002",
            "deps": ["TASK-001"],
            "files": ["src/orders/service.ts"],
            "kind": "impl",
            "reqs": ["REQ-002"],
        },
        {
            "id": "TASK-003",
            "deps": ["TASK-001"],
            "files": ["src/payments/service.ts"],
            "kind": "impl",
            "reqs": ["REQ-003"],
        },
        {
            "id": "TASK-004",
            "deps": ["TASK-002"],
            "files": ["src/orders/api.ts"],
            "kind": "impl",
            "reqs": ["REQ-002"],
        },
        {
            "id": "TASK-005",
            "deps": ["TASK-003"],
            "files": ["src/payments/api.ts"],
            "kind": "impl",
            "reqs": ["REQ-003"],
        },
        {
            "id": "TASK-006",
            "deps": ["TASK-001"],
            "files": ["src/notify/service.ts"],
            "kind": "impl",
            "reqs": ["REQ-004"],
        },
        {
            "id": "TASK-007",
            "deps": ["TASK-002", "TASK-003", "TASK-004", "TASK-005", "TASK-006"],
            "files": ["test/e2e.spec.ts"],
            "kind": "e2e",
            "reqs": [],
        },
        {"id": "TASK-008", "deps": ["TASK-007"], "files": [], "kind": "cleanup", "reqs": []},
    ]


def _demo() -> None:
    tasks = _toy_tasks()
    for t in tasks:
        t.setdefault("status", "pending")

    print("=" * 64)
    print("TASKS & DEPENDENCY LEVELS")
    print("=" * 64)
    levels = compute_levels(tasks)
    for t in tasks:
        deps = ", ".join(t.get("deps", [])) or "-"
        print(f"  L{levels[t['id']]}  {t['id']:9} [{t.get('kind'):7}] deps: {deps}")

    print()
    print("=" * 64)
    print("FRONTIER SIMULATION  (max_workers=3)")
    print("  each tick = a batch of agents running in parallel worktrees")
    print("=" * 64)
    batches, tail = simulate(tasks, max_workers=3)
    for i, batch in enumerate(batches, start=1):
        tag = "parallel" if len(batch) > 1 else "single  "
        print(f"  tick {i} [{tag}] -> {', '.join(batch)}")
    print(f"  tail  (serialized, after all impl) -> {', '.join(tail)}")
    print("  note: a tick also defers any task whose files collide with an")
    print("        in-flight task (file-disjoint guarantee); toy set is disjoint.")

    print()
    print("=" * 64)
    print("PR DELIVERY PLAN  (one PR per group)")
    print("=" * 64)
    plan = pr_plan(tasks)
    for g in plan["groups"]:
        stack = f"  (stacks on: {', '.join(g['depends_on'])})" if g["depends_on"] else ""
        print(f"  [{g['name']:9}] tasks: {', '.join(g['tasks'])}{stack}")
        print(f"              reqs : {', '.join(g['reqs']) or '-'}")
    print(
        f"  parallel feature PRs after base merges: "
        f"{', '.join(plan['parallel_after_base']) or '-'}"
    )
    print(
        f"  final: e2e + cleanup ({', '.join(plan['tail'])}) on integration "
        f"branch -> final merge"
    )
    print()
    print("Interpretation: base merges first; feature PRs are mutually")
    print("independent (parallel) and stack on base; e2e+cleanup run once at the end.")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load(path: str) -> List[Dict[str, Any]]:
    with open(path) as f:
        data = json.load(f)
    tasks = data.get("tasks", data) if isinstance(data, dict) else data
    for t in tasks:
        t.setdefault("status", "pending")
    return tasks


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Parallel orchestrator coordinator core")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("demo", help="Run on a built-in toy spec")
    fp = sub.add_parser("frontier", help="Print runnable frontier for a tasks JSON")
    fp.add_argument("--file", required=True)
    fp.add_argument("--max-workers", type=int, default=4)
    gp = sub.add_parser("groups", help="Print PR group plan for a tasks JSON")
    gp.add_argument("--file", required=True)

    args = p.parse_args(argv)
    if args.cmd == "demo":
        _demo()
    elif args.cmd == "frontier":
        for t in runnable_frontier(_load(args.file), args.max_workers):
            print(t["id"])
    elif args.cmd == "groups":
        print(json.dumps(pr_plan(_load(args.file)), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
