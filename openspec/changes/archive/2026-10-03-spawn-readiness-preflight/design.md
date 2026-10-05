## Context

A spawn reads its configuration through exactly three functions: `resolve_routing()` picks the
table out of policy, `select_cell()` picks a cell from it, and `build_child_env()` turns that
cell into a process environment. Every failure class that makes a worker silently useless lives
in one of those three — an unresolvable profile, a missing auth lane, an unprovisioned home.

`worktrail-routing --check` inspects none of them. It reads the routing file itself, passes the
mapping through `_validate_routing()`, and prints a per-cell table from that mapping. The one
live probe it does perform (an `auth.profile` resolution) is run against the raw mapping, so it
validates a dict the spawn never sees.

That is not a cosmetic gap. On 2026-10-02, `resolve_routing()` dropped `env_profiles` while
copying its inputs into its return value. Every profile-backed cell raised at spawn time; the
`--check` table said `ok`, because the profile it resolved was found in the raw file it had just
parsed. The bug was fixed in `resolve_routing()` (PR #1380), but the shape of the miss is
structural and remains: **a defect in the function that produces the table is invisible to a
check that never calls it.**

The second half of the incident is the response. The drain *did* run `--check` before its first
spawn, and discarded the answer — `check_routing_liveness(...)` is called best-effort under a
"never blocks the drain itself" comment, and the intake-triage pre-pass that then spawned the
worker is itself wrapped in a never-abort `except Exception` that logged
`intake-triage error: ...`. Both disciplines are correct for what they were written for
(transient pre-pass failures must not abort an otherwise-healthy run); neither is correct for a
configuration error, which no retry can clear.

## Goals / Non-Goals

**Goals**

- A cell is reported ready only after its spawn has been constructed from the same resolved
  table a real spawn would use.
- A configuration that cannot launch anything stops an unattended run *before* it spends an
  iteration discovering that, and says which cell and which file to fix.
- Adding a new auth lane to the spawn path has an obvious place to register its readiness
  check, so the check cannot silently fall behind the path it guards.

**Non-Goals**

- Changing `_validate_routing()`'s warnings, or the gates `--check` already records. The
  readiness probe is additive to both.
- Making `--check` a re-run of a spawn: no process is launched, no home is created, no network
  call is made.
- Touching the never-abort discipline around the drain's pre-passes. It is correct for the
  failures it was written for; the config error it was hiding is now caught earlier.
- Re-deriving readiness from the file. Anything that reads the routing file directly rather
  than the resolved table re-opens the exact hole this change closes.

## Decisions

### The probe is fed `resolve_routing()`'s output, never the file

This is the whole change. `readiness_problems(routing, ...)` takes the dict
`resolve_routing(load_policy(...))` returns and nothing else — it has no path parameter and no
file access, so it cannot be handed the raw mapping by accident or by convenience.

The consequence is that the #1380 defect becomes a *test*, not a memory: seed a routing file
that declares `env_profiles`, stub the resolver to drop the key as it once did, and the probe
reports the profile-bearing cells unready. Stated the other way around — if a future resolver
gains a key, this probe needs no change to cover it; if a future consumer reads a key the
resolver does not carry, the probe fails until the resolver does.

Note the trap this creates for callers: `drain.machine_wide_routing()` is *not* this input. It
returns `load_policy(...)["routing"]`, the validated block, which today happens to carry the
same keys. The drain resolves separately for the probe; using the in-scope variable would
re-introduce the blind spot while looking correct.

### Cells are constructed directly, not selected

The probe enumerates `(row, target)` pairs from `routing["tiers"]` and builds a `Cell` for each,
rather than calling `select_cell()`. Selection is capacity-aware and returns only the first
ungated cell; a check that used it would skip exactly the cells a run is about to fall back to.
Readiness is a property of the table, not of the current cache.

It calls `build_child_env(cell, base_env, env_profiles=...)` — the auth-lane builder — and not
`_prepare_child_env()`, which additionally *creates* per-worker state (an isolated opencode data
dir, a codex home). A check must not have side effects, and a home it created would make the next
spawn's result differ from the one it predicted.

### A new module, because the obvious home is an import cycle

`spawnlib` imports `router.routing_cli` at module level (for `resolved_routing_file_path`'s
sibling helpers), and `routing_cli` is one of the two callers. A module-level
`routing_cli → spawn_readiness → spawnlib → routing_cli` edge is therefore a cycle, so
`spawn_readiness` imports `spawnlib` inside its function. That mirrors the precedent set when
`env_profile.py` was split out for the same reason, and it keeps `routing_cli`'s import list
honest about what it needs at import time.

### The checking process's environment is the right environment

`build_child_env` refuses a claude `api` cell whose named variable is unset, and every worker's
environment is built from `{**os.environ, ...}` of the spawning process. So the honest question
is "can *this* process launch this cell", and the answer depends on the environment asking. The
probe therefore takes `base_env` explicitly (defaulting to the process environment) so tests can
inject it, and both callers pass the environment they will actually spawn with.

This does make `--check` environment-sensitive, which it already is elsewhere (it consults
`opencode models`). Reporting `ok` for a cell whose credentials are not in the environment
would be the same class of lie as reporting `ok` for a retired model, and `_check`'s own
docstring already refuses that: a cell whose auth lane cannot resolve "cannot serve, so
reporting `ok` would be actively misleading".

### A readiness failure stops a drain; a capacity gate does not

The drain already runs a pre-first-spawn check, and already declines to act on its verdict. The
distinction that makes acting on it correct is **what selection can do about the failure**:

- A `model_unavailable` gate is a *provider* condition. It is recorded in the capacity cache,
  and `select_cell()` skips the gated cell on the next iteration — the run heals itself, which
  is why `drain-operator-config`'s own scenario for a retired model says the first iteration
  "SHALL select the next ungated cell instead of launching and failing". Blocking on it would
  break that specified behavior.
- A readiness failure is an *operator configuration* error. Nothing is recorded, no cell can be
  routed to, and no retry helps: the spawn that fails will fail identically on iteration 40.
  Fail closed, name the cell and `routing.yaml`, exit 2 — the same treatment
  `drain-operator-config` already gives an unsupported `--agent` or a malformed `drain` block.

This is also why a readiness failure records no gate, matching the rule the env-profile check
already states: a recorded gate is skipped *silently* by the next spawn, converting a loud
configuration error into an invisible fallback to the next rung — the failure class this change
exists to remove.

### The preflight runs ahead of the pre-passes

The pre-first-spawn check currently sits *after* `run_intake_triage_prepass()`, which spawns
evaluator agents. In the 2026-10-02 incident the failing spawn **was** that evaluator — so a
check ordered where the old one sits would have reported the problem only after the thing it
was meant to protect had already run. Readiness is therefore evaluated before any pre-pass that
spawns, not merely before the first loop iteration.

### The codex `api` home check moves to a shared helper

Readiness that covers the claude auth lanes but not the codex one is a trap: a codex `api`
target would pass preflight and die at spawn, and the next incident report would be this one
again with a different harness. The two raises currently inside `_prepare_child_env` (no
`auth.codex_home` declared; the declared home has no `auth.json`) are extracted into a named
helper that both `_prepare_child_env` and the probe call. Only the *validation* moves — the home
is still created at spawn time, by the spawn path.

## Risks

- `--check` becomes environment-sensitive: a claude `api` cell whose `auth.env` variable is not
  exported in the checking shell now reports unready. That is the wanted behavior (it is exactly
  the condition that makes such a spawn produce nothing), but it is a change operators will
  notice, so the docs task states which variables the check consults.
- A drain on a machine with one stale cell now refuses to start where it previously started and
  routed around it. Deliberate, and the message names the cell; an operator who wants that target
  gone removes its tier cells or its target entry. This is the one behavior change in the proposal
  with an operational cost, and it is the point of the change: a run that silently uses a
  different model than the table's first choice is the failure being removed.
- The probe resolves the table a second time in the drain (separately from
  `machine_wide_routing()`). That is deliberate — see the trap note above — but it does mean the
  two must not diverge in *input*: both read the machine-wide file, and `resolve_routing()` is
  pure, so they cannot disagree about its contents.
