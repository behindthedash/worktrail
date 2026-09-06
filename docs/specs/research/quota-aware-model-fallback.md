# Discovery — quota-aware model fallback within a single agent_cli

Route A (idea-discovery) note. Source brief:
`20260803-184427-build-quota-aware-model-fallback`.

Status: **discovery only — no implementation.** Gate
`no_implementation_without_approval` applies.

## Problem, as framed by the brief

`~/.go/routing.yaml`'s `t4-trivia-opencode` tier is hand-pinned to
`opencode/deepseek-v4-flash-free`, a promotional free-tier model. Once
opencode Zen's "limited-time free period" lapses, the tier needs a manual
edit to `opencode/deepseek-v4-flash` (paid). The brief asked for a
MODEL-level fallback mechanism within one `agent_cli`, distinct from the
existing AGENT-level capacity-gate fallback (`routing.fallback`,
`agent_capacity.py`).

### Who benefits

The workspace operator (one person) running headless orchestrator/`/go`
workers, who wants tier-based routing to degrade gracefully instead of
silently breaking or silently overspending when a pinned model stops being
usable.

### What would make this unsuccessful

Building bespoke schema + composition logic for what turns out to be a
single, one-time, easily-hand-edited event — speculative infrastructure with
no second real trigger to justify it (see Risks).

## What already exists (grounded in the actual code, not assumed)

- `agent_capacity.py`'s capacity cache is **already keyed by
  `provider_key(agent, model)`**, i.e. `(agent, model)`-granular, not just
  `agent`-granular. `classify_failure()` already classifies "quota
  exceeded"/"usage limit"/"billing" text into a `billing` failure class with
  its own retry cooldown. The reactive-detection signal the brief asked for
  already exists as generic plumbing — it is not gated to any particular
  agent or model.
- `spawnlib.spawn_agent()`'s `fallback_agent` chain is **agent-level only**:
  each fallback hop always resolves to `default_model_for_agent(hop)`,
  ignoring any tier/role-resolved model for that hop. There is no way today
  to express "try model B on the SAME `agent_cli` before escalating to a
  different `agent_cli`."
- **Decisive finding, `live.py:1530-1531`:**

  ```python
  fallback = self.fallback_chain if self.fallback_chain else self.fallback_agent
  effective_fallback = fallback if agent == self.agent else None
  ```

  The agent-level fallback chain is **only applied when the resolved agent
  equals the run's own default agent**. The moment a task's agent is chosen
  via a `routing.tiers`/`routing.roles` override — exactly the
  `t4-trivia-opencode` case — `effective_fallback` is forced to `None`. The
  comment explains why: *"a role/tier override has no sensible fallback of
  its own."*

  **This means the real, current behavior is not "no model-level fallback" —
  it is zero fallback of any kind for any tier/role-pinned task.** When
  `t4-trivia-opencode`'s model becomes unavailable for *any* reason (the
  free-tier promo lapsing, a transient outage, a real rate limit), that
  task's spawn has no automatic recovery path today, cross-agent or
  same-agent.
- `model-tier-routing` (`openspec/changes/model-tier-routing`, PRs #116-#120)
  is still `in-progress` (13/15 tasks) — the remaining two tasks (5.1
  follow-up proposal for task-purpose classification, 5.2 resolve D3
  agent-preference-within-tier) are a different, adjacent open question, not
  this one.
- No end date exists anywhere for opencode Zen's free period — not in
  `~/.go/routing.yaml`'s own comment, not in any handoff brief. "Limited-time"
  is deliberately vague upstream. **A calendar-scheduled manual reminder is
  therefore not viable as a substitute fix** — there is no date to schedule
  against, which makes *some* reactive (failure-triggered) recovery
  necessary regardless of which approach is chosen.
- Prior art, checked for duplication: `20260717-210000-orchestrator-agent-
  fallback-on-quota-exhaustion` (done, 2026-07-19) is the brief that shipped
  today's agent-level fallback chain — this discovery builds on it, doesn't
  duplicate it. `20260722-181423-opencode-free-tier-concurrency-cap-decision`
  (queued, parked to 2099) is a different, unrelated question (concurrency
  cap vs. fallback). No existing spec or shipped code addresses tier/role
  fallback specifically.

## Candidate approaches

### Option A — do nothing beyond documentation (status quo)

Rely on the task failing outright, the operator noticing, and a manual
`routing.yaml` edit.

- **Con — decisive:** the "no fallback of any kind" finding above means this
  is not a graceful degradation today, it's a silent hard failure of every
  `t4-trivia-opencode` task the moment the free tier dies, with no scheduled
  reminder possible (no known end date). Not actually safe to leave as-is.

### Option B — extend the existing agent-level fallback chain to cover tier/role-resolved spawns (recommended)

Relax `live.py:1531`'s `effective_fallback = fallback if agent ==
self.agent else None` so a tier/role-pinned spawn also gets the run's
configured `fallback_agent`/`fallback_chain`, **except** for `JUDGMENT_ROLES`
(review/resolve/ci-fix/assembly-resolve), which must keep the current
independent-reviewer guarantee (13.3, DEC-003) untouched — falling back to
an arbitrary agent for a review verdict would erode that guarantee, so this
carve-out is not optional.

- **Pro:** reuses 100% existing, already-tested plumbing
  (`agent_capacity`'s `(agent, model)`-keyed gate, the fallback-chain walk in
  `spawn_agent`). No new schema. Fixes the *general* reliability gap this
  discovery found — any tier/role-pinned model becoming unavailable for any
  reason, not just the one opencode-Zen scenario the brief named.
- **Con:** a "cheap tier" task that falls back to `claude`/`codex` loses the
  cost intent that picked the cheap tier in the first place. Materially
  better than the task simply failing, but not free — worth surfacing in the
  run journal/dashboard (already partially covered by `_serving_agent_guess`
  at `live.py:1538`) so cost drift is visible, not silent.

### Option C — new model-level fallback field on tier/role entries (the brief's original ask)

A `fallback_model`/`fallback_models` key per `routing.tiers`/`routing.roles`
entry, consulted before any agent-level chain, reusing `agent_capacity`'s
existing `(agent, model)` keying to try another model on the *same*
`agent_cli` first.

- **Pro:** precisely targets "stay on the cheap agent, just change model" —
  preserves cost intent that Option B gives up.
- **Con:** meaningfully larger surface: new schema field + validation
  (`policy._validate_agent_entry()`), a new hop type in `spawn_agent()`'s
  `configured` list construction, and a composition rule for how it
  interacts with Option B's chain once that exists. Solves a problem with
  exactly one concrete data point (opencode Zen's promo) so far.

## Risks and unknowns

1. **No known end date for the trigger event** — reactive (failure-based)
   detection is required either way; there is nothing to schedule against.
2. **JUDGMENT_ROLES carve-out is load-bearing, not incidental.** Any change
   to `live.py:1531` must preserve `effective_fallback = None` for
   review/resolve/ci-fix/assembly-resolve — those roles never reach this
   branch via `LiveSpawn.__call__` for resolve/ci-fix/assembly-resolve
   (per `dispatch.JUDGMENT_ROLES` comment), but `review` does, and losing its
   independent-agent guarantee would be a silent correctness regression, not
   a visible one.
3. **Cost visibility.** Option B trades a hard failure for a possibly-silent
   cost increase (cheap tier silently running on an expensive fallback
   agent). Needs a dashboard/run-record signal, not just a code path.
4. **Option C's `fallback_model` × Option B's `fallback_agent` composition**
   is explicitly out of scope until Option B ships and a second real trigger
   (beyond the single opencode-Zen promo) demonstrates Option C is worth its
   larger surface — building both at once risks the two mechanisms fighting
   over which "next hop" to record success/failure against in
   `agent_capacity`'s cache, a concern the original brief itself flagged.

## Decision

**Proceed — Option B, scoped as a Route F/G-sized fix, not Option C.**

The concrete, verified finding (tier/role-pinned tasks have zero fallback
today, not "no model-level fallback") is worth fixing regardless of the
opencode-Zen promo specifically — it's a general reliability gap in the
tier/role system shipped by `model-tier-routing`. Option B closes it with
existing plumbing and a small, well-bounded change (one gating expression in
`live.py`, one explicit `JUDGMENT_ROLES` exclusion, one dashboard/run-record
cost-visibility line).

Option C (the brief's original literal ask — a new model-level fallback
schema field) is deferred: it solves a real but currently narrower problem
(preserving cost intent on fallback) with a single concrete trigger so far.
Captured as a follow-up handoff, to be picked up only if Option B's
cross-agent fallback proves too costly in practice for cheap-tier work.

Next: file a Route F/G brief for Option B (behavior gap in an already-shipped
mechanism — the existing gate silently drops fallback coverage it clearly
intends to provide, per its own docstring's precedence rules) rather than
continuing this Route A run into implementation.

---

## Addendum (2026-09-06) — both options overtaken by the target selector

Re-read against current `main` while diagnosing a per-model subscription cap
(Claude's Fable allowance metering separately from the rest of the plan).
Two of this discovery's load-bearing findings no longer describe the code:

- **Option B's premise is gone.** `effective_fallback = fallback if agent ==
  self.agent else None` no longer exists — `grep -n effective_fallback
  src/worktrail/orchestrator/live.py` returns nothing. The agent-level
  fallback chain it gated was replaced wholesale by the target selector:
  `select_cell()` (`src/worktrail/runtime/selection.py:339-411`) walks a tier
  row across `routing.targets` in file order and serves the first cell that
  isn't capacity-gated. Tier/role-pinned spawns therefore no longer have
  "zero fallback of any kind" — the row itself *is* the fallback chain
  (`spawnlib.py:953`).
- **Option C's goal is reachable with zero new schema.** The deferred ask was
  a `fallback_model` field so a spawn could "stay on the cheap agent, just
  change model," preserving cost intent. Declaring two targets on the same
  harness and pool does exactly that today: a tier row holds one cell per
  *target*, so a second target is what gives the row a second rung on the
  same CLI, and `provider_key(target, model)`
  (`src/worktrail/orchestrator/agent_capacity.py:83`) makes each rung an
  independently gated cell. `gate_for_agent()` (`:227-231`) already collects
  *all* targets matching a harness, so the front door's adapter path handles
  it too.

Verified 2026-09-06 against the live operator config by simulating gates
through `select_cell`: with both rungs healthy the first serves; with rung 1
gated the same-harness rung 2 serves; with both gated selection leaves the
harness for the next target in the row. No code path needed changing.

**Status: superseded — no implementation to schedule.** The remaining real
gap in this area is unrelated to fallback shape: `_EXPLICIT_RESET_RE`
(`agent_capacity.py:352-357`) parses only Codex's `try again at <date>`
wording, so a Claude cap's stated reset is lost and the gate falls back to
`DEFAULT_COOLDOWNS['billing'] = 3600` — a week-long cap re-opens hourly and
re-burns a spawn each time. Tracked in brief
`20260905-222317-claude-capacity-reset-parsing-and`; the call site that
consumes the parsed value is owned by the active change
`agent-capacity-gate-liveness-reprobe`. The intra-harness ladder above
limits the blast radius of that bug (each retry falls through to the next
rung) but does not fix it.
