## Why

`LiveSpawn.__call__`'s explicit `--model-map`/`--effort` override path wraps the whole spawn in
`spawnlib.explicit_cell_override()` (`src/worktrail/orchestrator/live.py:3182`), and the live
fan-out runs sibling tasks on a `ThreadPoolExecutor` with `max_workers` 2 or 3
(`live.py:4798`, default at `live.py:4844`). The override writes a unique `mkstemp` routing
file -- so two workers never share a *file* -- but then points the process-wide
`os.environ["WORKTRAIL_ROUTING_FILE"]` at it and, on exit, restores the previous value it
captured (`spawnlib.py:1258-1266`). `spawn_agent` re-reads policy at spawn start via
`load_policy(worktrail_home())` (`spawnlib.py:1335`), which resolves the file through that same
process-wide env var.

Two overlapping overrides therefore clobber each other's pointer. Whichever leaves first
restores the operator's default file while the other is still inside its `with` block, so that
worker's `load_policy()` resolves a routing table with no `explicit` tier and the spawn fails
with `NoExecutionTarget` (or lands on the wrong cell); in the window before either exits, a
spawn can also read the sibling's temp file. Neither `spawnlib.py` nor `policy.py` holds a lock
or thread-local for this state, and `tests/orchestrator/test_spawnlib.py:3418` only pins the
written file's contents -- no test runs two overrides concurrently. The archived
`2026-10-03-fix-explicit-routing-override` change is silent on concurrency.

The parallel fan-out exists to keep 2-3 cold starts in flight; an explicit override is the
normal path for a run configured with `--effort` or `--model-map`. The race makes exactly the
runs that use overrides the least reliable.

## What Changes

- **Scope the routing-file override to the calling thread.** The override stops mutating
  `os.environ`. A new thread-scoped `routing_file_override()` context manager in
  `router/policy.py` holds the path, and `resolved_routing_file_path()` -- the single place
  every routing read (`load_policy()`, and so `spawn_agent`'s `tier="explicit"` selection)
  resolves through -- consults it ahead of `WORKTRAIL_ROUTING_FILE` and the default file.
  `explicit_cell_override()` keeps its signature and lifetime; it enters that context manager
  instead of swapping the process environment.
- **Do not serialize spawns.** A process-wide mutex is rejected: the override spans the entire
  `spawn_agent` call, so locking it would serialize every concurrent worker's cold start and
  collapse the fan-out to one. Per-thread state gives correctness without losing parallelism.
- **Pin the concurrent contract with a regression test.** Two threads inside overrides at once
  each resolve their own throwaway file and their own `explicit` cell; a sibling thread not in
  an override (and the process after both exit) still resolves the operator's file; and
  `os.environ` is unchanged throughout.
- **Children no longer inherit the throwaway file.** `spawn_agent` builds the worker env from
  `os.environ`; removing the swap means the worker (and any nested worktrail process it runs)
  sees operator routing, not a one-cell table with no tier except `explicit`. This is the
  correct direction -- the child harness never resolves worktrail routing, and the throwaway
  file is meaningful only to the selecting thread's `select_cell` call.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `model-tier-routing`: an explicit model/effort override's temporary routing file becomes
  thread-scoped instead of process-environment-scoped, so concurrent live workers cannot observe
  or clobber each other's override.

## Impact

- `src/worktrail/router/policy.py` -- new `routing_file_override()` thread-local context
  manager; `resolved_routing_file_path()` consults it first.
- `src/worktrail/orchestrator/spawnlib.py` -- `explicit_cell_override()` enters it instead of
  setting/restoring `os.environ[ROUTING_FILE_ENV]`.
- `tests/orchestrator/test_spawnlib.py` -- concurrency regression coverage; the existing
  file-content assertions read the thread-local instead of the environment.
- `tests/orchestrator/test_live_extras.py`, `tests/orchestrator/test_resilience_helpers.py`,
  `tests/conductor/test_compile.py` -- fixtures that captured the override via
  `os.environ["WORKTRAIL_ROUTING_FILE"]` read `resolved_routing_file_path()` instead.
- `src/worktrail/router/env_profile.py` -- one comment that describes the override as pointing
  the env var at the throwaway file.
