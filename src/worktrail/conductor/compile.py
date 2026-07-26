"""Compile a change into a cached RunPlan -- the conductor's one expensive read.

Design `conductor-lanes.md` §4.4: the conductor is "the only context that ever
reads the full change", and compile is where that happens. Every other actor in
a run (the fan-out workers, integrate, verify) sees only its own slice.

Three ways a plan gets produced, cheapest first:

1. **Cache hit.** Keyed by the change's content fingerprint, so a re-run, a
   resume, or a second `compile` of an unchanged change costs nothing. This is
   the property design P3 asks to be verified: *same change compiled twice =>
   zero LLM calls on the second run.*
2. **Seed.** If the authoring format already declares file scope for every task,
   the plan is a pure projection of what was parsed and no model is invoked.
   Every devkit spec takes this path, which is decision D1's "task frontmatter
   becomes a seed RunPlan so compile has ground truth instead of re-inferring".
3. **Compile.** One `spawn_agent` call over the change directory. This is the
   OpenSpec path, where `tasks.md` carries no per-task metadata at all.

A compile that fails, times out, or returns something that does not validate
degrades to the baseline plan rather than raising. A run that falls back is
slower (the format's conservative sequential deps stand) but never wrong, and
`runplan.apply_to_tasks` records the reason in the run journal.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from worktrail.conductor import runplan
from worktrail.orchestrator.coordinator import TAIL_KINDS
from worktrail.conductor.runplan import (
    SOURCE_BASELINE,
    SOURCE_COMPILED,
    SOURCE_SEED,
    RunPlan,
    TaskPlan,
)

COMPILE_TIMEOUT_DEFAULT = 900


def default_cache_dir(repo: "str | Path") -> Path:
    """Where compiled plans live: `<repo>-worktrees/runplans/`.

    Deliberately the same `<repo>-worktrees/` root that `live.journal_path_for`
    uses, so all derived run state for a repo sits in one place, outside the
    repo and therefore never committed into the change directory.
    """
    repo = Path(repo).resolve()
    return repo.parent / f"{repo.name}-worktrees" / "runplans"


# --------------------------------------------------------------------------- #
# Plans that need no model
# --------------------------------------------------------------------------- #
def _plan_from_tasks(spec_id: str, fp: str, tasks: Sequence[Dict[str, Any]], source: str) -> RunPlan:
    return RunPlan(
        spec_id=spec_id,
        fingerprint=fp,
        source=source,
        tasks=tuple(
            TaskPlan(
                id=t["id"],
                files=tuple(runplan._norm_str_list(t.get("files"))),
                deps=tuple(runplan._norm_str_list(t.get("deps"))),
                kind=str(t.get("kind") or ""),
                complexity=str(t.get("complexity") or ""),
                review=str(t.get("review") or ""),
            )
            for t in tasks
        ),
    )


def needs_compile(tasks: Sequence[Dict[str, Any]]) -> List[str]:
    """Task ids with no declared file scope -- the ones a model would have to infer.

    Tail tasks are excluded. `runnable_frontier` holds them out of the fan-out on
    `kind` alone, so they never participate in the file-collision check and
    inferring a scope for them would buy nothing.
    """
    return [
        t["id"]
        for t in tasks
        if t.get("kind") not in TAIL_KINDS and not runplan._norm_str_list(t.get("files"))
    ]


# --------------------------------------------------------------------------- #
# The model pass
# --------------------------------------------------------------------------- #
PROMPT = """\
You are compiling an execution plan for an already-approved change. Do not \
critique the change, propose alternatives, or write any code. Read, then emit \
one JSON object.

Change directory: `{spec_rel}`
Repository root: the directory you are running in.

Read `{spec_rel}/proposal.md`, `{spec_rel}/design.md` (if present), and \
`{spec_rel}/specs/**` for intent. Then explore the actual repository to find \
the real files each task will touch. File paths must be paths that exist in \
this repo, or that this change will create; do not guess at a layout you have \
not looked at.

Tasks, in authored order:
{task_list}

For every task above, decide:

- `files`: repo-relative paths the task will **create or modify**. Files it only \
reads do not belong here. This list is what lets two tasks run in parallel, so \
under-reporting causes two workers to fight over the same file. Prefer listing \
one extra plausible file over omitting a likely one.
- `deps`: ids of tasks from the list above that must be **finished** before this \
one can start, because this task consumes their output. Shared ownership of a \
file is NOT a dependency -- that is what `files` already expresses. Only list a \
real ordering constraint.

Rules:
- Every task id above must appear exactly once. Invent no ids.
- `deps` may only contain ids from the list above.
- The dependency graph must be acyclic.
- If you genuinely cannot determine a task's files, use `[]`. An empty list is \
read as "unknown", and the task is kept serialised behind its neighbours. That \
is the safe answer; a guessed path is not.

Output nothing but this JSON object, in a ```json fenced block:

{{"tasks": [{{"id": "<id>", "files": ["<path>"], "deps": ["<id>"], \
"complexity": "low|medium|high", "review": "light|standard|deep"}}]}}
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    """Pull the result object out of an agent's final message.

    Prefers the last fenced ```json block, then falls back to the last balanced
    top-level object. Last, not first: models routinely narrate a draft before
    committing to a final answer, and the trailing object is the one they meant.
    """
    for m in reversed(list(re.finditer(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL))):
        try:
            obj = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj

    depth, start = 0, None
    spans: List[tuple] = []
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0 and start is not None:
                spans.append((start, i + 1))
    for s, e in reversed(spans):
        try:
            obj = json.loads(text[s:e])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _validate(payload: Dict[str, Any], ids: set) -> tuple[Optional[List[TaskPlan]], List[str]]:
    """Turn a raw model payload into TaskPlans, or explain why it cannot be trusted.

    Rejection is all-or-nothing on purpose (see `runplan.apply_to_tasks`): the
    file scopes were inferred alongside the edges, so a payload that got the task
    set wrong has not earned trust in the parts that happen to parse.
    """
    problems: List[str] = []
    rows = payload.get("tasks")
    if not isinstance(rows, list):
        return None, ["payload has no `tasks` list"]

    seen: Dict[str, TaskPlan] = {}
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            problems.append(f"unusable row: {row!r:.80}")
            continue
        tid = str(row["id"]).strip()
        if tid not in ids:
            problems.append(f"unknown task id {tid!r}")
            continue
        if tid in seen:
            problems.append(f"duplicate task id {tid!r}")
            continue

        files: List[str] = []
        for f in runplan._norm_str_list(row.get("files")):
            # Strip a leading `./` only. Not `lstrip("./")` -- that takes a
            # character SET, so it eats the whole `../../` of a traversal and
            # silently turns it into a plausible-looking repo-relative path.
            p = f.replace("\\", "/")
            while p.startswith("./"):
                p = p[2:]
            if not p or p.startswith("/") or ".." in Path(p).parts:
                problems.append(f"{tid}: file path outside the repo: {f!r}")
                continue
            files.append(p)

        seen[tid] = TaskPlan(
            id=tid,
            files=tuple(sorted(set(files))),
            deps=tuple(d for d in runplan._norm_str_list(row.get("deps")) if d in ids and d != tid),
            complexity=str(row.get("complexity") or ""),
            review=str(row.get("review") or ""),
        )

    if problems:
        return None, problems
    if set(seen) != ids:
        return None, [f"missing task ids: {sorted(ids - set(seen))}"]
    return [seen[i] for i in sorted(seen)], []


def _default_spawn(prompt: str, cwd: Path, timeout: int, log) -> str:
    from worktrail.orchestrator import spawnlib

    return spawnlib.spawn_agent(prompt, cwd, timeout=timeout, log=log).text


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def compile_run_plan(
    spec_dir: "str | Path",
    tasks: Sequence[Dict[str, Any]],
    *,
    spec_id: str,
    repo: "str | Path",
    cache_dir: "str | Path | None" = None,
    allow_llm: bool = True,
    force: bool = False,
    timeout: int = COMPILE_TIMEOUT_DEFAULT,
    spawn: Optional[Callable[..., str]] = None,
    log: Callable[[str], None] = lambda *_: None,
) -> RunPlan:
    """Return the RunPlan for this change, compiling only if it has to.

    `spawn(prompt, cwd, timeout, log) -> str` is injectable so the cache
    behaviour can be tested without a model, and so a caller can route the
    compile through its own agent policy.
    """
    spec_dir = Path(spec_dir)
    repo = Path(repo).resolve()
    cache_dir = Path(cache_dir) if cache_dir is not None else default_cache_dir(repo)
    fp = runplan.fingerprint(spec_dir, tasks)

    if not force:
        cached = runplan.load_cached(cache_dir, spec_id, fp)
        if cached is not None:
            log(f"run plan: cache hit {fp[:12]} ({cached.source})")
            return cached

    gaps = needs_compile(tasks)
    if not gaps:
        plan = _plan_from_tasks(spec_id, fp, tasks, SOURCE_SEED)
        runplan.store(cache_dir, plan)
        log(f"run plan: seeded from the artifact, no model needed ({fp[:12]})")
        return plan

    baseline = _plan_from_tasks(spec_id, fp, tasks, SOURCE_BASELINE)
    if not allow_llm:
        log(f"run plan: {len(gaps)} task(s) lack file scope and compiling is disabled")
        return baseline

    try:
        spec_rel = spec_dir.resolve().relative_to(repo)
    except ValueError:
        spec_rel = spec_dir
    task_list = "\n".join(f"- {t['id']}: {t.get('title') or ''}".rstrip() for t in tasks)
    prompt = PROMPT.format(spec_rel=spec_rel, task_list=task_list)

    def give_up(note: str) -> RunPlan:
        """Degrade to the baseline, carrying the reason into the run journal.

        Not cached: the next attempt should get a fresh try at the model rather
        than inheriting one bad response for the life of this content version.
        """
        log(f"run plan: {note}")
        return RunPlan(
            spec_id=spec_id,
            fingerprint=fp,
            source=SOURCE_BASELINE,
            tasks=baseline.tasks,
            notes=(note,),
        )

    log(f"run plan: compiling {len(tasks)} task(s), {len(gaps)} without file scope")
    runner = spawn or _default_spawn
    try:
        text = runner(prompt, repo, timeout, log)
    except Exception as exc:  # noqa: BLE001 -- a failed compile must not fail the run
        return give_up(f"compile failed ({type(exc).__name__}: {exc}); using the artifact's own deps")

    payload = _extract_json(text or "")
    if payload is None:
        return give_up("compile returned no JSON object; using the artifact's own deps")

    planned, problems = _validate(payload, {t["id"] for t in tasks})
    if planned is None:
        return give_up("compile output rejected: " + "; ".join(problems[:4]))

    # `kind` is never taken from the model: the artifact declares it (devkit
    # frontmatter, OpenSpec `[tag]`), and it is what holds e2e/cleanup out of the
    # fan-out. Carry the parsed value through so a seed and a compile agree.
    kinds = {t["id"]: str(t.get("kind") or "") for t in tasks}
    plan = RunPlan(
        spec_id=spec_id,
        fingerprint=fp,
        source=SOURCE_COMPILED,
        # `from_dict`, not `TaskPlan(**...)`: `to_dict` emits lists, and the bare
        # constructor would store them as-is. `frozen=True` stops rebinding a
        # field, not mutating what the field holds.
        tasks=tuple(
            TaskPlan.from_dict({**tp.to_dict(), "kind": kinds.get(tp.id, "")}) for tp in planned
        ),
    )
    runplan.store(cache_dir, plan)
    log(f"run plan: compiled and cached {fp[:12]}")
    return plan


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Compile a spec/change into a cached RunPlan (file scope + dependency edges)."
    )
    ap.add_argument("spec", help="path to the spec or OpenSpec change directory")
    ap.add_argument("--cache-dir", default=None, help="override the plan cache location")
    ap.add_argument("--force", action="store_true", help="recompile even on a cache hit")
    ap.add_argument("--no-llm", action="store_true", help="seed from the artifact only; never spawn")
    ap.add_argument("--timeout", type=int, default=COMPILE_TIMEOUT_DEFAULT)
    ap.add_argument("--json", action="store_true", help="print the plan as JSON")
    a = ap.parse_args(argv)

    from worktrail.taskformats import resolve

    spec_dir = Path(a.spec).resolve()
    if not spec_dir.is_dir():
        print(f"no such spec directory: {spec_dir}", file=sys.stderr)
        return 1

    spec_id, tasks = resolve.load_spec(str(spec_dir))
    repo = spec_dir
    while repo != repo.parent and not (repo / ".git").exists():
        repo = repo.parent
    if repo == repo.parent:
        # Without this the walk lands on `/` and the default cache dir becomes
        # `/-worktrees/runplans`. Fail instead of writing somewhere absurd.
        print(f"not inside a git repository: {spec_dir}", file=sys.stderr)
        return 1

    plan = compile_run_plan(
        spec_dir,
        tasks,
        spec_id=spec_id,
        repo=repo,
        cache_dir=a.cache_dir,
        allow_llm=not a.no_llm,
        force=a.force,
        timeout=a.timeout,
        log=lambda m: print(m, file=sys.stderr),
    )

    if a.json:
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
        return 0

    merged, notes = runplan.apply_to_tasks(tasks, plan)
    print(f"{plan.spec_id}  source={plan.source}  fingerprint={plan.fingerprint[:12]}")
    print(f"  cache: {runplan.cache_path(a.cache_dir or default_cache_dir(repo), spec_id, plan.fingerprint)}")
    for n in notes:
        print(f"  note: {n}")
    for t in merged:
        print(f"  {t['id']:<10} deps={','.join(t.get('deps') or []) or '-':<24} files={len(t.get('files') or [])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
