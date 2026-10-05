## Context

The capacity cache (`~/.worktrail/agent-capacity.json`, `src/worktrail/orchestrator/agent_capacity.py`)
is one machine-local advisory store written by three producers and read by a half-dozen
consumers on both sides of the drain/spawn seam:

- **Writers.** `spawn_agent` records the served cell's outcome under
  `provider_key(target, model)` = `<target>:<model>` (`spawnlib.py:1581`, `:1720`);
  `worktrail-routing --check` records a retired opencode model under the same
  `<target>:<model>` shape (`routing_cli.py:272-283`); the drain records an account-level block
  under the bare key `active_target or active_agent` (`record_capacity_gate`, `drain.py:747-785`).
- **Readers.** `spawn_agent`'s in-spawn selection and every other `select_cell` caller pass
  `agent_capacity` itself as `capacity=`, so `agent_capacity.check()` is the reader;
  the front-door resolution wraps `check()` (`skill_dispatch.py:478-490`);
  `check-agent`/`gate_for_agent` calls `check()`; the dashboard's capacity line resolves a
  `<target>:<model>` set through `gate_snapshot()`; and the drain has its own private
  `capacity_gated()`/`_entry_gated()`.

So there are two key shapes and two readers, and exactly one of the four combinations works:
the drain's reader honours both shapes, and `check()` honours only the model-qualified one. A
gate written by the drain is therefore invisible to every spawn-path reader, and `gate_snapshot`'s
configured-key set makes the dashboard blind to it in the same way.

See `proposal.md` for the full motivation. This document records the decisions behind the shape
of the fix: which resolution wins, which direction the writers keep, how the probe survives it,
why the diagnostic changes, and why the drain's breaker loses its blocked-counting backstop.

## Goals / Non-Goals

**Goals:**

- One function decides whether a cell is gated, and every reader resolves through it, so a gate
  written by any producer is honoured by every consumer.
- The drain keeps writing the key that matches the semantics of what it records: an account-level
  (`auth`/`billing`) block applies to every model of the target, so it stays a bare target key.
- The string an operator is handed when a cell is skipped is the key they can act on with the
  existing `worktrail-agent-capacity status`/`clear` surface.
- The drain's circuit breaker counts what its docstrings already promise: a capacity block is a
  wait, not a failure, and the capacity stop is the only stop for it.

**Non-Goals:**

- Changing which failures are capacity failures. `CAPACITY_FAILURE_CLASSES` stays
  `auth`/`billing`; a `transport`/`startup`/`sandbox` failure remains a plain `failed` iteration
  the breaker is supposed to catch.
- Making the drain write a model-qualified key. The writer's shape is correct for what it
  records; the readers move.
- Adding a store migration, a new command, or a new cache field. The file's shape is unchanged,
  and no existing entry is rewritten.
- Changing cooldowns, the probe cadence, or `NEVER_PROBE_CLASSES`.
- Touching `human-decision-queue`'s decision-filed exemption, which is a different axis of the
  same counter.

## Decisions

### D1. Hoist the drain's resolution into `agent_capacity`; do not write a second one

The drain's `capacity_gated()` is already the superset: it is the only implementation that
answers both "does an exact entry gate this cell" and "does a bare entry for this target gate
this cell", and `tests/drain/test_drain.py` pins seven cases of it (expired `retry_after`,
`reset_at` fallback, timestamp-less gates, partial-model gates, garbage caches). Hoisting it
keeps that suite as the regression net for the shared implementation instead of leaving two
implementations to drift.

The shape that makes it usable from both call sites is a resolution that returns *which* entry
gated, not just whether one did:

- `entry_gated(state, now=None) -> bool` -- the predicate (gated status `unavailable`/`gated`/
  `blocked`, and a `retry_after`/`reset_at` window that is absent or still in the future),
  hoisted verbatim from `drain._entry_gated`.
- `gate_entry(query, *, data=None, path=None, now=None) -> tuple[str, dict] | None` -- the
  resolution, returning `(entry_key, state)` for the first entry that gates `query`.

`check(target, model, ...)` calls `gate_entry(provider_key(target, model), ...)` and raises
`ProviderUnavailable` with the resolved entry; `gate_snapshot` resolves each configured cell key
through it; `drain.capacity_gated(cache, agent, now)` becomes
`agent_capacity.gate_entry(agent, data=cache, now=now) is not None` (plus the existing
base-`str` handling so an operand-less query string still works).

### D2. Cell queries: exact first, then the bare target. Bare queries: unchanged

For a `<target>:<model>` query the resolution order is: an active bare `<target>` entry gates
(provider-wide evidence wins, matching `capacity_gated`'s documented rule that "a per-model entry
can be stale ... and a provider-wide gate is never weaker evidence than that"), else an active
exact entry gates, else the cell is available. For a bare query it stays: an active bare entry
gates, else every `<target>:*` entry being active gates, else available.

The bare-query branch is load-bearing and easy to lose in a rewrite: it is what
`select_available_agent()` falls back to when routing is unset (`drain.py:650-653`) and what
drain's templated `--agent-cmd` path uses (`drain.py:2642`). The bare-target fallback added for
cell queries must not change it -- only the exact-key branch gains the extra lookup.

### D3. The writer keeps its shape

A `billing`/`auth` block is an account condition: it holds for every model the target could
serve, including models the drain has never seen. Recording it as `<target>:<model>` would make
it invisible to a later selection that resolves a different model for the same target, which is
the failover the drain exists to perform. So `record_capacity_gate()` keeps writing the bare
target key (and the bare harness name when no routing applies), and the readers take on the
obligation of honouring it. This is the direction the spec already states ("or bare `<target>`
for a target-wide gate") -- the code, not the spec, was wrong.

### D4. The probe stays keyed to the entry that gated

`check()`'s cooldown re-probe (`_probeable`, `PROBE_INTERVAL_S`, design D1/D2 of the re-probe
change) stamps `probe_at` on the entry it is gating on and lets exactly one caller through. When
a cell query is gated by a *bare* entry, the entry to probe and stamp is that bare entry -- not
`provider_key(target, model)`, which may hold nothing at all. Returning `(entry_key, state)`
rather than a bare boolean is what makes this expressible; the probe path keeps its existing
lock-and-recheck structure, against the resolved key.

### D5. The diagnostic names the gate key and keeps the harness

`NoExecutionTarget`'s label becomes the key plus the harness (`claude-deepseek:deepseek-flash[1m]
[claude]`-shaped), not the current `target (harness:model)`. Two reasons: the key is the
identifier the operator can act on -- `worktrail-agent-capacity status` prints it and
`clear <key>` accepts it -- and the current form's `harness:model` half *looks* like a key
(`claude:deepseek-flash[1m]`) while being one the cache has never held, which is exactly the
wrong invitation. The harness stays because it is what tells an operator which CLI the cell would
have launched, which the target name alone does not always reveal.

The scope is the exhausted-row diagnostic alone. `spawnlib`'s per-cell progress lines carry the
same `target (harness:model)` shape (`spawnlib.py:1630`, `:1672`, `:1774`), but they report a
*served* cell failing on a named class, not a gate an operator is being asked to clear, so they
are deliberately left alone -- a log line is not a cell key. Widening this to every
`(harness:model)` rendering would make a progress line pretend to be a cache key, which is the
same confusion in the other direction.

### D6. The breaker exempts only a capacity block

`outcome.kind == "blocked"` is two different things: the record-less capacity block
(`state` = `blocked_capacity_auth`/`blocked_capacity_billing`) and the run-record blocked states
(`blocked_external_dependency`, `blocked_product_decision`, `blocked_security_or_safety`). The
docstrings' promise is about the first only. The loop therefore keys the exemption on the state
prefix that already identifies a capacity block (`drain.py:2723` uses the same
`state.startswith("blocked_capacity_")` test to decide whether to persist a gate), leaving every
other kind's accounting byte-identical, including the decision-filed exemption
`human-decision-queue` pins.

The rejected alternative -- drop `"blocked"` from the condition wholesale -- would silently stop
counting `blocked_external_dependency` and `blocked_security_or_safety`, i.e. remove the only
bound on a loop that keeps hitting a wall the automation cannot pass, and would contradict
`human-decision-queue`'s decision-less-block scenario.

### D7. The backstop is removed, not documented

The two options were: make the code match the docstrings (a capacity block does not count), or
make the docstrings describe the counting backstop. The first is chosen, because the backstop is
now dead weight with a live cost.

The backstop existed for the pre-#1430 shape in which the drain's gate was invisible to its own
selector: the same exhausted cell was re-selected, blocked again, and only the failure counter
stopped the loop (`record_capacity_gate`'s docstring records exactly that live reproduction). Two
things now make the gate visible to the selection that must honour it: #1430 keyed the drain's
recording by target, and this change makes the spawn-path reader honour the bare key too. The
remaining cost of keeping the count is the one the operator actually saw: two capacity blocks
reported as `circuit_breaker: 2 consecutive failed iterations`.

The safety argument that the counter is no longer needed: a capacity block is `auth` or `billing`
only, whose cooldowns are 86400s and 3600s (or a provider-stated reset, which is honoured
verbatim and marked `provider`-derived so the probe will not shorten it). Both outlast a drain
iteration by orders of magnitude, so the gate the blocked iteration persists is still active when
the next iteration's selection runs, `select_available_agent()` returns `None`, and `decide()`
takes the `capacity_gated` stop. The counter never had to fire to bound a capacity outage; it
only ever reported one as a failure.

## Risks / Trade-offs

- [A single bare entry now suppresses every model of a target for every reader] -> that is the
  intended meaning of a target-wide gate and the reason the drain records one; the failure classes
  that produce it are account-level by construction (`auth`/`billing`). A *billing* gate remains
  cooldown-derived and probe-eligible, so a recovered account self-heals on the probe cadence
  without an operator; an `auth` gate still requires an explicit clear, as its own spec says.
- [A capacity outage is now bounded only by the gated stop] -> the stop is reliable for the reason
  D7 gives (cooldowns outlast an iteration), and the residual is intentional: if an operator
  clears a gate by hand mid-run, the drain resumes attempting that target, which is what clearing
  a gate means.
- [The label change alters an operator-visible string other text may quote] -> no test asserted
  the old form and no skill text quotes it; the new form is the one the operator-facing
  `clear <key>` command accepts, so the change moves the diagnostic toward the documented surface,
  not away from it.
- [Two callers pass `capacity=` as a callable rather than the module, so they would not inherit
  the fix] -> the drain's and the front-door's wrappers both delegate to `check()`/`gate_entry()`
  in this change, and the enforcement is a test per consumer, not a code-read assumption.
- [A reader could mistake an `available` entry for evidence against a bare gate] -> the resolution
  order is explicit (D2) and the "model-qualified gate does not un-gate its target-wide sibling"
  scenario pins the stronger direction.

## Migration Plan

1. Land the three parts together: the reader unification, the diagnostic, and the breaker
   accounting. They are one contract -- `model-tier-routing`'s requirement covers the first two
   and `drain-failure-breaker` covers the third -- and splitting them would leave a window in
   which the drain stops counting a block whose gate is still invisible to the spawn path.
2. No store migration. The cache's shape does not change; existing entries (bare and
   model-qualified alike) simply become visible to the readers that used to miss them, which is
   the defect being fixed. An existing bare drain gate starts suppressing its target's other
   models for the spawn-path selectors immediately.
3. Rollback is reverting the commit: the resolution returns to `check()`'s exact-key lookup, the
   label returns to its previous form, and the counter counts `blocked` again. Nothing persisted
   is left in a shape the older code cannot read.
