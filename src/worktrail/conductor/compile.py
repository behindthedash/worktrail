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
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from worktrail.conductor import req_coverage, runplan
from worktrail.orchestrator.coordinator import TAIL_KINDS
from worktrail.conductor.runplan import (
    COMPILE_MARKER_NAME,
    SOURCE_BASELINE,
    SOURCE_COMPILED,
    SOURCE_SEED,
    RunPlan,
    TaskPlan,
)

COMPILE_TIMEOUT_DEFAULT = 900


def marker_path(spec_dir: "str | Path") -> Path:
    """Where a passing compile records its validated content fingerprint."""
    return Path(spec_dir) / COMPILE_MARKER_NAME


def write_marker(spec_dir: "str | Path", fp: str) -> None:
    """Record a passing compile so CI can verify it later without an LLM call.

    Overwritten on every passing compile -- a marker left over from a prior
    fingerprint is exactly the "stale" case the CI check needs to catch, and
    it naturally does: the next fingerprint comparison simply won't match
    until this is re-run and the marker moves to the new content version.
    """
    Path(marker_path(spec_dir)).write_text(fp + "\n", encoding="utf-8")


def default_cache_dir(repo: "str | Path") -> Path:
    """Where compiled plans live: `<repo>-worktrees/runplans/`.

    Deliberately the same `<repo>-worktrees/` root that `live.journal_path_for`
    uses, so all derived run state for a repo sits in one place, outside the
    repo and therefore never committed into the change directory.
    """
    repo = Path(repo).resolve()
    return repo.parent / f"{repo.name}-worktrees" / "runplans"


def _git_repo_root(path: Path) -> Optional[Path]:
    """Return the canonical checkout root that owns ``path``'s Git state.

    Resolves via ``--git-common-dir`` rather than ``--show-toplevel``: a spec
    directory inside a linked *worktree* (which is what SDD skills always run
    `worktrail-compile` against -- see AGENTS.md `conductor/`) has its own
    toplevel distinct from the canonical checkout, so `--show-toplevel` would
    resolve `default_cache_dir` to `<worktree>-worktrees/runplans` while
    `live.py`'s canonical `full-real` run reads `<repo>-worktrees/runplans` --
    two caches that never share an entry, so the (paid, LLM-backed) pre-compile
    result was always thrown away. `--git-common-dir` is shared by every
    worktree of one checkout and resolves to the same canonical root whether
    `path` is inside the main checkout or a linked worktree.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    common_dir = result.stdout.strip()
    if not common_dir:
        return None
    common_path = Path(common_dir)
    return common_path.parent.resolve() if common_path.name == ".git" else None


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
{purpose_instructions}
Final pass, before you output anything: you decided each task's `files` in \
isolation, which is exactly how a shared file gets recorded on only one task. \
Re-read every task's `files` side by side. For each file that appears on at \
least one task, check whether any OTHER task in the list also creates or \
modifies it and is missing it from its own `files`. Add it there too. Two \
tasks correctly declaring the same file is normal and expected; a shared file \
declared by only one of them is the exact miss this pass exists to catch.

Then re-check `deps` the same way: for every file two or more tasks now \
declare in `files`, confirm those tasks are ordered -- one must be a `deps` \
ancestor of the other, directly or transitively. Two tasks can each correctly \
declare the same file and still have nothing ordering them; that is a \
compile-time failure, not a style nit. Where no order exists yet, add a \
`deps` edge between them -- the later task in authored order depending on \
the earlier one, unless the tasks' own descriptions demand the opposite \
order.

Rules:
- Every task id above must appear exactly once. Invent no ids.
- `deps` may only contain ids from the list above.
- The dependency graph must be acyclic.
- If you genuinely cannot determine a task's files, use `[]`. An empty list is \
read as "unknown", and the task is kept serialised behind its neighbours. That \
is the safe answer; a guessed path is not.

Output nothing but this JSON object, in a ```json fenced block:

{{"tasks": [{{"id": "<id>", "files": ["<path>"], "deps": ["<id>"], \
"complexity": "low|medium|high", "review": "light|standard|deep"{purpose_field}}}]}}
"""


def _purpose_prompt_additions(purpose_tiers: Dict[str, str]) -> tuple[str, str]:
    """Prompt fragments requesting a per-task `purpose` classification.

    Both empty when the repo has no `routing.purpose_tiers` configured: a repo
    that never declared a purpose vocabulary must not be asked to classify
    against one, per task 2.2. When non-empty, the requested value is
    constrained to exactly the resolved table's keys (or omitted).
    """
    if not purpose_tiers:
        return "", ""
    keys = ", ".join(sorted(purpose_tiers))
    instructions = (
        f"- `purpose`: this task's purpose -- exactly one of: {keys}. Omit this "
        "key entirely if none of them fits; never invent a value outside this "
        "list.\n"
    )
    field = f', "purpose": "<one of: {keys}> (omit if none fits)"'
    return instructions, field


def _resolve_purpose_tiers(repo: Path) -> Dict[str, str]:
    """Resolve the target repo's `routing.purposes` (renamed from
    `purpose_tiers` by the target-selector routing schema, task 1.3) -- the
    `{purpose: tier}` vocabulary a repo declares for task-purpose
    classification. `load_policy()` already validates this table (a plain
    string-to-string map dropping malformed entries), so this is a read, not a
    second validation pass. Empty when the repo has none configured; that is
    what tells the PROMPT builder (task 2.2) whether to request `purpose` at
    all.
    """
    from worktrail.router import policy as router_policy

    policy = router_policy.load_policy(repo)
    routing = policy.get("routing") or {}
    return routing.get("purposes") or {}


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


def _validate(
    payload: Dict[str, Any], ids: set, purpose_tiers: Optional[Dict[str, str]] = None
) -> tuple[Optional[List[TaskPlan]], List[str], List[str]]:
    """Turn a raw model payload into TaskPlans, or explain why it cannot be trusted.

    Rejection is all-or-nothing on task-set correctness (see
    `runplan.apply_to_tasks`): the file scopes were inferred alongside the
    edges, so a payload that got the task set wrong has not earned trust in the
    parts that happen to parse. `purpose` is the one field that does not follow
    that rule -- a value outside the injected vocabulary is dropped and
    reported as a `warnings` entry (not a `problems` one), the same handling
    `_validate_agent_entry()` gives a malformed `agent_model`: the rest of the
    row is still trusted.

    Returns `(planned, problems, warnings)`. `problems` non-empty means the
    whole payload is rejected; `warnings` is purely informational and never
    affects the return value of `planned`.
    """
    valid_purposes = set(purpose_tiers or {})
    problems: List[str] = []
    warnings: List[str] = []
    rows = payload.get("tasks")
    if not isinstance(rows, list):
        return None, ["payload has no `tasks` list"], warnings

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

        purpose = str(row.get("purpose") or "").strip()
        if purpose and purpose not in valid_purposes:
            warnings.append(f"{tid}: purpose {purpose!r} outside the configured vocabulary; dropped")
            purpose = ""

        seen[tid] = TaskPlan(
            id=tid,
            files=tuple(sorted(set(files))),
            deps=tuple(d for d in runplan._norm_str_list(row.get("deps")) if d in ids and d != tid),
            complexity=str(row.get("complexity") or ""),
            review=str(row.get("review") or ""),
            purpose=purpose,
        )

    if problems:
        return None, problems, warnings
    if set(seen) != ids:
        return None, [f"missing task ids: {sorted(ids - set(seen))}"], warnings
    return [seen[i] for i in sorted(seen)], [], warnings


def _resolve_compile_tier(repo: "str | Path") -> "tuple[str | None, str | None]":
    """The tier (and, if declared, a preferred target) compile() spawns
    against: `routing.roles.compile.{tier,prefer}` if the repo declares one,
    else `routing.default_tier` with no preference (task 5.2 AC: resolve
    `_default_spawn` through `select_cell(routing, roles.get('compile',
    {}).get('tier') or default_tier)`). Role/tier *naming* is resolved from
    `repo`'s own policy (repo-local `routing:` block if present, else the
    machine-wide file) -- but the tier's actual cell contents are looked up
    by `spawnlib.spawn_agent()` itself, always from the machine-wide routing
    file regardless of caller (the same asymmetry `dispatch.tier_for()` /
    `LiveSpawn.__call__` already rely on: which tier a role prefers can be
    repo-scoped, but which models exist to serve it is a machine property)."""
    from worktrail.router.policy import load_policy, resolve_routing

    resolved_routing = resolve_routing(load_policy(Path(repo)))
    role = (resolved_routing.get("roles") or {}).get("compile") or {}
    tier = role.get("tier") or resolved_routing.get("default_tier")
    return tier, role.get("prefer")


def _default_spawn(
    prompt: str,
    cwd: Path,
    timeout: int,
    log,
    *,
    agent: Optional[str] = None,
    model: Optional[str] = None,
    fallback_agent: Optional["str | Sequence[str]"] = None,
) -> str:
    """Spawn the compile worker.

    No `agent`/`model` (the common case, `--agent`/`--model` omitted):
    resolve a tier via `_resolve_compile_tier()` and let `spawn_agent()`
    select a cell from it. `agent` alone (`--agent <target>`) prefers that
    declared target within the resolved tier row, keeping its own configured
    model. Both `agent` and `model` together are an explicit-cell override
    (task 5.2 AC: "flag values name a target and model") -- `agent` must
    already be a target the operator has declared (its harness/pool are
    reused unchanged; this never invents a target/harness pairing the
    operator hasn't), and `model` replaces its configured model for this one
    spawn via a throwaway one-cell routing file, never the operator's real
    routing.yaml. `model` given without `agent` has no target to attach it
    to and is rejected rather than guessed.

    `fallback_agent` (`--fallback-chain`) is accepted for backward CLI
    compatibility only and otherwise unused: `spawn_agent()`'s tier-row
    selection already re-selects automatically within the row on an infra
    failure (task 3.4), so there is no longer a separate fallback chain to
    thread through.
    """
    from worktrail.orchestrator import spawnlib

    tier, prefer = _resolve_compile_tier(cwd)
    prefer = agent or prefer

    if model is None:
        log(f"run plan: spawn policy resolved tier={tier} prefer={prefer or '-'}")
        return spawnlib.spawn_agent(
            prompt, cwd, tier=tier, prefer=prefer, timeout=timeout, log=log
        ).text

    if not prefer:
        raise ValueError(
            "--model requires --agent naming the routing.targets entry its value overrides"
        )
    return _spawn_with_explicit_cell(prompt, cwd, timeout, log, target=prefer, model=model)


def _spawn_with_explicit_cell(
    prompt: str, cwd: Path, timeout: int, log, *, target: str, model: str
) -> str:
    """The `--agent`/`--model` explicit-cell override: reuse `target`'s
    declared harness/pool from the operator's real machine-wide routing, but
    serve `model` instead of that target's configured tier cell, for this one
    spawn only. Delegates to `spawnlib.explicit_cell_override()` -- the same
    mechanism `LiveSpawn.__call__`'s `--model-map`/`--effort` override uses --
    which raises `OperatorConfigError` (a `ValueError`) when `target` isn't
    already declared."""
    from worktrail.orchestrator import spawnlib

    log(f"run plan: spawn policy resolved explicit cell target={target} model={model}")
    with spawnlib.explicit_cell_override(target, model):
        return spawnlib.spawn_agent(prompt, cwd, tier="explicit", timeout=timeout, log=log).text


def _parse_fallback_chain(value: Optional[str]) -> Optional[List[str]]:
    """Parse a comma-separated fallback chain into ordered agent names."""
    if value is None:
        return None
    chain = [hop.strip() for hop in value.split(",")]
    return [hop for hop in chain if hop]


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
    allow_force_over_active_worktrees: bool = False,
    timeout: int = COMPILE_TIMEOUT_DEFAULT,
    spawn: Optional[Callable[..., str]] = None,
    log: Callable[[str], None] = lambda *_: None,
) -> RunPlan:
    """Return the RunPlan for this change, compiling only if it has to.

    `spawn(prompt, cwd, timeout, log) -> str` is injectable so the cache
    behaviour can be tested without a model, and so a caller can route the
    compile through its own agent policy.

    `force` re-invokes the model even on a cache hit -- deliberately, so a
    human can retry a compile that produced a bad plan (the CLI's own
    scope/ordering-gap errors suggest exactly this). The model call is not
    deterministic, so two forced recompiles of unchanged content can produce
    two different (each individually valid) dependency graphs. That is safe
    before any task worktree exists for this spec; once one does, its files
    were fanned out under whatever plan is currently cached, and silently
    replacing that plan out from under it is how a resumed run ends up
    disagreeing with the work already in progress. `force` therefore refuses
    to overwrite an existing cache entry while task worktrees exist for this
    spec, unless the caller passes `allow_force_over_active_worktrees=True`.
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
    else:
        cached = runplan.load_cached(cache_dir, spec_id, fp)
        if cached is not None and not allow_force_over_active_worktrees:
            from worktrail.orchestrator import worktree as _worktree

            if _worktree.has_task_worktrees(repo, spec_id):
                log(
                    f"run plan: --force refused ({fp[:12]}) -- task worktree(s) already "
                    f"exist for {spec_id} and were fanned out under the currently cached "
                    "plan; a non-deterministic recompile could disagree with that "
                    "in-progress work. Pass allow_force_over_active_worktrees to override."
                )
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

    purpose_tiers = _resolve_purpose_tiers(repo)

    try:
        spec_rel = spec_dir.resolve().relative_to(repo)
    except ValueError:
        spec_rel = spec_dir
    task_list = "\n".join(f"- {t['id']}: {t.get('title') or ''}".rstrip() for t in tasks)
    purpose_instructions, purpose_field = _purpose_prompt_additions(purpose_tiers)
    prompt = PROMPT.format(
        spec_rel=spec_rel,
        task_list=task_list,
        purpose_instructions=purpose_instructions,
        purpose_field=purpose_field,
    )

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

    log(
        f"run plan: compiling {len(tasks)} task(s), {len(gaps)} without file scope"
        + (f", {len(purpose_tiers)} purpose tier(s) configured" if purpose_tiers else "")
    )
    runner = spawn or _default_spawn
    try:
        text = runner(prompt, repo, timeout, log)
    except Exception as exc:  # noqa: BLE001 -- a failed compile must not fail the run
        return give_up(f"compile failed ({type(exc).__name__}: {exc}); using the artifact's own deps")

    payload = _extract_json(text or "")
    if payload is None:
        return give_up("compile returned no JSON object; using the artifact's own deps")

    planned, problems, purpose_warnings = _validate(payload, {t["id"] for t in tasks}, purpose_tiers)
    for w in purpose_warnings:
        log(f"run plan: {w}")
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
    ap.add_argument("--agent", default=None, help="override the compile spawn agent")
    ap.add_argument("--model", default=None, help="override the compile spawn model")
    ap.add_argument(
        "--fallback-chain",
        default=None,
        help="comma-separated fallback agent names, in order",
    )
    ap.add_argument("--cache-dir", default=None, help="override the plan cache location")
    ap.add_argument("--force", action="store_true", help="recompile even on a cache hit")
    ap.add_argument(
        "--allow-force-over-active-worktrees",
        action="store_true",
        help=(
            "with --force, also recompile when task worktree(s) already exist for this "
            "spec (normally refused: a non-deterministic recompile could disagree with "
            "the plan those worktrees were fanned out under)"
        ),
    )
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
    repo = _git_repo_root(spec_dir)
    if repo is None:
        # Without this the walk lands on `/` and the default cache dir becomes
        # `/-worktrees/runplans`. Fail instead of writing somewhere absurd.
        print(f"not inside a git repository: {spec_dir}", file=sys.stderr)
        return 1

    fallback_chain = _parse_fallback_chain(a.fallback_chain)

    plan = compile_run_plan(
        spec_dir,
        tasks,
        spec_id=spec_id,
        repo=repo,
        cache_dir=a.cache_dir,
        allow_llm=not a.no_llm,
        force=a.force,
        allow_force_over_active_worktrees=a.allow_force_over_active_worktrees,
        timeout=a.timeout,
        spawn=lambda prompt, cwd, timeout, log: _default_spawn(
            prompt,
            cwd,
            timeout,
            log,
            agent=a.agent,
            model=a.model,
            fallback_agent=fallback_chain,
        ),
        log=lambda m: print(m, file=sys.stderr),
    )

    merged, notes = runplan.apply_to_tasks(tasks, plan)
    gaps = needs_compile(merged)
    collisions = runplan.unordered_file_collisions(merged)
    uncovered = req_coverage.find_uncovered_requirements(spec_dir, repo)

    if not (gaps or collisions or uncovered):
        write_marker(spec_dir, plan.fingerprint)

    if a.json:
        print(json.dumps(plan.to_dict(), indent=2, sort_keys=True))
        if gaps:
            _print_scope_gap_error(gaps)
        if collisions:
            _print_ordering_gap_error(collisions)
        if uncovered:
            _print_req_coverage_gap_error(uncovered)
        return 1 if (gaps or collisions or uncovered) else 0

    print(f"{plan.spec_id}  source={plan.source}  fingerprint={plan.fingerprint[:12]}")
    print(f"  cache: {runplan.cache_path(a.cache_dir or default_cache_dir(repo), spec_id, plan.fingerprint)}")
    for n in notes:
        print(f"  note: {n}")
    for t in merged:
        print(f"  {t['id']:<10} deps={','.join(t.get('deps') or []) or '-':<24} files={len(t.get('files') or [])}")

    if gaps:
        _print_scope_gap_error(gaps)
    if collisions:
        _print_ordering_gap_error(collisions)
    if uncovered:
        _print_req_coverage_gap_error(uncovered)
    return 1 if (gaps or collisions or uncovered) else 0


def _print_scope_gap_error(gaps: List[str]) -> None:
    """Shared by both `main()` output modes -- stderr only, so `--json` stdout
    stays a clean, parseable plan even when the exit code reports failure."""
    print(
        f"ERROR: {len(gaps)} implementation task(s) still have no file scope after "
        f"compiling: {', '.join(gaps)}",
        file=sys.stderr,
    )
    print(
        "  a live run will refuse to fan these out (validate_task_metadata). Give "
        "them a tail kind (docs/e2e/cleanup) if they genuinely need none, add explicit "
        "`files:` scope to the artifact, or retry compile (--force) with more context "
        "in proposal.md/design.md so the model can determine it.",
        file=sys.stderr,
    )


def _print_ordering_gap_error(collisions: List[tuple]) -> None:
    """Shared by both `main()` output modes, same shape as `_print_scope_gap_error`."""
    print(
        f"ERROR: {len(collisions)} file(s) are declared by two or more tasks with no "
        "dependency order between them:",
        file=sys.stderr,
    )
    for f, a, b in collisions:
        print(f"  {f}: {a} <-> {b}", file=sys.stderr)
    print(
        "  a live run's per-tick file lock happens to serialise these anyway, but "
        "nothing in the plan itself asserts it. Add an explicit `deps` edge between "
        "them (either order), or retry compile (--force) with more context so the "
        "model can place one.",
        file=sys.stderr,
    )


def _print_req_coverage_gap_error(uncovered: List[str]) -> None:
    """Shared by both `main()` output modes, same shape as `_print_scope_gap_error`."""
    print(
        f"ERROR: {len(uncovered)} requirement(s) declared by this change have no "
        f"task coverage: {', '.join(uncovered)}",
        file=sys.stderr,
    )
    print(
        "  each was newly added or modified under `specs/**/spec.md` but never "
        "mentioned in `tasks.md`. Add a task that names it (or references it "
        "explicitly), or drop the requirement if it is not actually part of this "
        "change.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
