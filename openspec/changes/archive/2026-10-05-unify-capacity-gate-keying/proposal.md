## Why

Three defects, all reproduced by code read, all in the capacity-cache seam the 2026-10-04
`fix(drain): key capacity gates by routing target` change (#1430) narrowed but did not close.

**1. Two key formats for one failing cell, and only one reader understands both.**

`agent_capacity.provider_key(target, model)` (`src/worktrail/orchestrator/agent_capacity.py:97-98`)
renders the canonical gate key `<target>:<model>`. `spawn_agent`'s spawn path records under it
(`spawnlib.py:1581`, `:1720`), and `specs/model-tier-routing`'s "Capacity gates key on target and
model" requires the selector to "consult the cell key and its bare-target key".

`worktrail-drain` records an account-level block (`auth`/`billing`) under a **bare** key instead:
`record_capacity_gate(..., active_target or active_agent, ...)` (`src/worktrail/drain/drain.py:747-785`)
-- deliberately, because a billing/account block applies to every model of that target. Of the
two readers, only the drain's own private `capacity_gated()`/`_entry_gated()`
(`drain.py:484-543`) honours a bare entry for a `<target>:<model>` query. `agent_capacity.check()`
-- the reader every *other* consumer uses -- looks up the exact `<target>:<model>` key only and
returns silently (available) on a miss. Its consumers are `spawn_agent`'s in-spawn selection
(`spawnlib.py:1350` passes the module itself as `capacity=`), the front-door cell resolution
(`router/skill_dispatch.py:478-490`), `worktrail-agent-capacity check-agent`
(`agent_capacity.py:693` → `gate_for_agent` → `check`), and the dashboard's capacity line
(`router/dashboard.py:3919-3925` → `gate_snapshot`, fed only `<target>:<model>` keys by
`_routing_configured_providers`, `dashboard.py:482-494`).

Reproduced: with `{"providers": {"claude-deepseek": {"status": "unavailable",
"failure_class": "billing", "retry_after": <+1h>, "source": "drain"}}}` on disk,
`agent_capacity.check("claude-deepseek", "deepseek-flash[1m]")` returns without raising -- the
cell looks available -- while `drain.capacity_gated(cache, "claude-deepseek:deepseek-flash[1m]")`
is `True`.

The operator-visible consequence: the drain blocks on a billing block, persists its gate, and
fails over correctly *for its own candidate choice* -- then the one-shot it spawns
(`worktrail-go auto`) resolves its own task and review spawns through `spawn_agent`, re-selects
the very cell that gate names, and burns an attempt on it. The gate is only half-visible.

**2. The diagnostic that names the cell names a different string.**

`NoExecutionTarget` renders each exhausted cell as `"{target} ({harness}:{model})"`
(`src/worktrail/runtime/selection.py:70`). For a target `claude-deepseek` serving the claude
harness, the intake-triage attempt line reads `claude-deepseek (claude:deepseek-flash[1m])` --
neither the key the cache holds (`claude-deepseek:deepseek-flash[1m]`) nor a string
`worktrail-agent-capacity status`/`clear` accepts. An operator handed that line cannot act on it.

**3. The drain's circuit breaker counts what both of its docstrings promise it does not.**

The module docstring (`drain.py:58-63`) and `classify_outcome`'s (`drain.py:2169-2174`) both
state a capacity-blocked iteration "does not count toward `circuit_breaker`" and "should stop the
drain via the existing capacity_gated path once the cache reflects it". The loop contradicts
both: `if outcome.kind in ("failed", "blocked") and not decisions_filed: state.consecutive_failures += 1`
(`drain.py:2780`).

The fix cannot simply drop `"blocked"` from that condition: `kind == "blocked"` covers the
record-less capacity block (`blocked_capacity_auth`/`blocked_capacity_billing`,
`drain.py:2221-2222`, the only states the docstring's promise is about) *and* the run-record
blocked states (`blocked_external_dependency`, `blocked_product_decision`,
`blocked_security_or_safety`, `drain.py:2200-2201`), which are meant to keep counting --
`human-decision-queue`'s "The drain rewards filed decisions and punishes decision-less blocks"
pins that. Only the capacity block is exempt.

Live cost (2026-09-24 drain-logs): two `blocked_capacity_billing` iterations stopped the run
with `circuit_breaker: 2 consecutive failed iterations` -- a capacity outage reported to the
operator as two failures. The counting also truncates failover: with three configured
candidates, two consecutive capacity blocks reach the threshold while the third candidate is
still selectable, so `decide()` returns `circuit_breaker` before that candidate is ever tried
(`drain.py:2135-2140` sits below the gated-stop check, which needs every candidate gated). That
counting is a backstop for the pre-#1430 shape in which the drain's gate was invisible to its
own selector (`record_capacity_gate`'s docstring, `drain.py:759-763`). With #1430's target
keying and with defect 1 fixed, every gate a blocked iteration writes is visible to the
selection that must honour it, so the backstop's only remaining effects are the misleading stop
and the skipped candidate.

## What Changes

- **One resolution decides "is this cell gated".** The bare-target-aware lookup currently private
  to `drain.capacity_gated()` is hoisted into `agent_capacity` and becomes the single resolution
  (`agent_capacity.gate_entry(query, ...)` returning `(entry_key, state)`, plus the gated-entry
  predicate `entry_gated(state, ...)` it is built from), and `check()` reads through it.
  Contract, hoisted verbatim so drain's existing `capacity_gated` tests stay the regression net:
  a `<target>:<model>` query is gated by an active entry under that exact key **or** by an active
  bare `<target>` entry; a bare query is gated by an active bare entry, or by every entry under a
  `<query>:*` key being active; an expired window never gates. `drain.capacity_gated()` becomes a thin
  wrapper, `gate_snapshot()` resolves each configured cell key through the same function, and
  `spawn_agent`'s selection, the front-door resolution, and `check-agent`/`gate_for_agent`
  inherit it unchanged. `check()`'s probe-through path stays keyed to the entry that actually
  gated the cell -- the bare one, when a bare entry is what gated it.
- **The diagnostic names the gate key.** `NoExecutionTarget`'s attempted-cell label carries the
  cell's `<target>:<model>` key verbatim (the harness is still named alongside it), so the line
  an operator reads can be pasted into `worktrail-agent-capacity status`/`clear`.
- **The breaker counts failures, as documented.** Only a capacity block (`blocked_capacity_*`)
  stops advancing `consecutive_failures`; every other `blocked` state keeps counting exactly as
  before, and the decision-filed exemption is untouched. `capacity_gated` remains the sole stop
  for a capacity outage, and both docstrings become true.
- No new operator procedure and no new command: the operator surface is unchanged. What changes
  is that the key `worktrail-agent-capacity status` prints is now the key every reader resolves,
  and the key the diagnostic prints.

## Capabilities

### New Capabilities

- `drain-failure-breaker`: what advances the drain's consecutive-failure circuit breaker -- an
  iteration waiting on provider capacity is a wait, not a failure, and the capacity stop remains
  the sole stop for a capacity outage.

### Modified Capabilities

- `model-tier-routing`: "Capacity gates key on target and model" is scoped to the whole surface
  rather than to `spawn_agent` alone -- exactly one resolution decides gating for every reader, a
  bare target-wide entry gates every model of that target for all of them, an account-level block
  is named as the bare target key it is written as and no reader may require a model-qualified
  entry to honour it, and the exhausted-row diagnostic names each attempted cell by its gate key.

## Impact

- `src/worktrail/orchestrator/agent_capacity.py` -- the hoisted resolution (`gate_entry()` +
  `entry_gated()`), `check()`'s and `gate_snapshot()`'s use of it, `gate_for_agent`'s docstring.
- `src/worktrail/drain/drain.py` -- `capacity_gated()`/`_entry_gated()` delegate to the shared
  resolution; the loop's `consecutive_failures` increment exempts a capacity block; the module
  and `classify_outcome` docstrings.
- `src/worktrail/runtime/selection.py` -- `NoExecutionTarget`'s attempted-cell label.
- Tests: `tests/orchestrator/test_agent_capacity.py`, `tests/orchestrator/test_check_agent_contract.py`,
  `tests/router/test_dashboard.py`, `tests/drain/test_drain.py`, `tests/runtime/test_selection.py`.
- Docs: `skills/worktrail-routing-config/references/gotchas.md` (a bare target entry gates every
  model of that target) and `skills/worktrail-go/SKILL.md`'s capacity-cache command block (the
  key the diagnostic and the attempt line print is the key `clear` accepts) -- the two places an
  operator reads the cache's key shape.
- Non-goals: changing which failures are capacity classes (`CAPACITY_FAILURE_CLASSES` stays
  `auth`/`billing`); changing cooldowns, probing, or the cooldown-derived re-probe cadence;
  making the drain write a model-qualified key for an account-level block (a provider-wide gate
  is the correct shape for it -- the readers move, not the writer); the second `spawnlib`
  capacity writer (`record(cell.target, cell.model, ...)`, already the canonical key);
  `spawnlib`'s per-cell *progress* lines, which render the same `target (harness:model)` shape
  but are not a gate diagnostic an operator is asked to clear; and any change to the
  decision-filed exemption in `human-decision-queue`.
