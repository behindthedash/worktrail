## Context

`explicit_cell_override(target, model, effort=None)` exists so a model or effort override can
reuse a declared target's harness/pool while serving a different cell. It writes a one-target,
one-tier (`explicit`) routing document to a `mkstemp` file and makes that file the routing
source for the duration of the `with` block. Both callers -- `conductor.compile`'s
`--agent`/`--model` and `LiveSpawn.__call__`'s `--model-map`/`--effort` -- hold the block across
the entire `spawn_agent()` call, because `spawn_agent` reads policy at its own start
(`spawnlib.py:1335`).

The mechanism it used to make the file the routing source is a process-wide assignment:

```python
previous = os.environ.get(ROUTING_FILE_ENV)
os.environ[ROUTING_FILE_ENV] = str(explicit_file)
try:
    yield
finally:
    ...restore previous...
```

`live.py` fans tasks out through `ThreadPoolExecutor(max_workers=max(1, max_workers))`
(`live.py:4798`; run default 2, other entry points 3 at `live.py:4844`), so two
`LiveSpawn.__call__` invocations can overlap inside that block. `os.environ` is shared by every
thread, and the captured `previous` is whatever was set when *this* override entered -- normally
the operator's file or unset. Interleaving A-enter, B-enter, A-exit, B-spawn leaves
`WORKTRAIL_ROUTING_FILE` pointing at A's file (or the operator's) while B resolves, and B's
`tier="explicit"` row does not exist there. The files themselves are unique per call, so the
hazard is entirely the shared pointer.

`resolved_routing_file_path()` is the single resolution point: `load_policy()` ->
`_resolve_routing()` -> `resolved_routing_file_path()`, and error attribution uses it too. If it
returns the correct file for a thread, every downstream read in that thread is correct.

## Goals / Non-Goals

**Goals:**

- Two explicit overrides active at once each resolve their own throwaway routing file and their
  own `explicit` cell, regardless of exit order.
- Concurrent fan-out stays concurrent -- the fix must not serialize worker spawns.
- `os.environ` is never mutated by an override, so no other thread, child process, or
  long-lived caller can observe a throwaway routing file.
- Preserve `explicit_cell_override`'s call signature and its "restore what was there" contract
  for nesting and for the single-threaded `conductor.compile` path.

**Non-Goals:**

- Changing what the throwaway file contains (the whole-target reproduction is a separate,
  already-specified guarantee).
- Making the override survive into child processes. Worker harnesses do not resolve worktrail
  routing; a nested `worktrail-*` process should see operator policy, and the throwaway table
  has no useful tiers for it.
- Re-resolving routing at `LiveSpawn` construction time or threading a resolved policy through
  `spawn_agent` (a separate design question about repo-local vs machine-wide resolution; this
  change keeps the existing resolution call exactly where it is).
- Reworking `agent_capacity` or any other shared cache the spawn path touches.

## Decisions

### Thread-scoped override state, consulted by `resolved_routing_file_path()`

`router/policy.py` gains a module-level `threading.local()` and a
`routing_file_override(path)` context manager that saves the current thread's value, sets the
new path, and restores the saved value in `finally` (stack discipline, so nesting composes).
`resolved_routing_file_path()` checks that thread-local first, then `env_setting(ROUTING_FILE_ENV)`
(which still honors the legacy `GO_` name), then `default_routing_file()`.

`explicit_cell_override()` keeps its signature and its `with` body but replaces the `os.environ`
swap with `with routing_file_override(explicit_file): yield`. `spawn_agent` is untouched: its
`load_policy(worktrail_home())` call resolves through `resolved_routing_file_path()` and now
sees the calling thread's override.

`threading.local()`, not `contextvars.ContextVar`: the concurrency unit here is a
`ThreadPoolExecutor` worker thread, where per-thread state is the direct model. `ContextVar`
would work but implies async/task propagation semantics this path does not use, and setting it
in one worker thread has the same effect only because each thread has its own context --
strictly more machinery for the same guarantee.

### A process-wide lock is rejected

A mutex around the override block would be correct but spans the whole spawn (the `with` block
encloses `spawn_claude_p`/`spawn_agent`), so it would serialize every concurrent cold start --
the exact throughput the persistent-pool fan-out was introduced to gain (`live.py:4798`'s
docstring records 2 of 3 slots idle behind one task before it). A lock that covers only the
env set/restore does not help either: the borrow must last until the spawned thread has loaded
policy, and `explicit_cell_override` has no signal for when that read completes without new
plumbing. Per-thread state removes the shared resource instead of scheduling access to it.

### Passing an explicit routing file through the spawn API is rejected for this change

Making `spawn_agent(..., routing_file=...)` (and `spawn_claude_p`) accept the path is the most
explicit possible design, but it widens two public signatures that
`tests/orchestrator/test_live_spawn_call_signature.py` audits with AST conformance, rewrites
every internal `load_policy` caller to accept the path, and buys the same guarantee the
thread-local gives. The thread-local also keeps existing mocks and callers working: anything
that reads `resolved_routing_file_path()` -- including real `spawn_agent` -- observes the
override without a new parameter.

### Consequence: child processes see operator routing

`_prepare_child_env` starts from `{**os.environ, ...}` (`spawnlib.py:1387`). With no env swap,
the worker no longer inherits the throwaway path. That is intentional and safer: the throwaway
document declares only `tiers.explicit` and one target, so any nested worktrail process that
resolved it would fail or mis-select. The worker harness itself does not read
`WORKTRAIL_ROUTING_FILE`. Tests that captured the override by reading `os.environ` are updated
to read `resolved_routing_file_path()`, which is what the real spawn path does.

## Risks / Trade-offs

- [A caller relies on `os.environ["WORKTRAIL_ROUTING_FILE"]` being set during an override] ->
  no in-repo production consumer does (only the tests written against the old mechanism, which
  are updated). External tooling reading the operator's env during an override will now see the
  operator's file, which is the pre-existing value and the safer one.
- [A thread pool reuses a thread and the override leaks] -> the `finally` restore runs on every
  exit path (normal, exception, cancellation of the `with` body); the regression test asserts
  the post-exit state.
- [Nested overrides in one thread] -> the context manager saves and restores the enclosing
  thread's value; a nested override resolves its target from the enclosing thread's routing
  view, the same fail-closed semantics the env swap had.
- [A routing *write* (`worktrail-routing --init`) issued inside an active override in the same
  thread] -> it would target the throwaway file. No such call occurs during a spawn, and the
  scoping is confined to the `with` block.
- [Repo-local policy shadows the machine-wide file] -> unchanged by this fix: `_resolve_routing`
  still prefers a repo-local `routing:` block before consulting
  `resolved_routing_file_path()`, exactly as the env-based override did.

## Migration Plan

1. Land the policy helper, the `spawnlib` switch, and the test updates in one commit. No state
   migrates: the override file is per-call and cleaned up as before.
2. Rollback is a revert; the thread-local is only populated inside the context manager, so the
   reverted env-based mechanism resumes with no leftover state.
