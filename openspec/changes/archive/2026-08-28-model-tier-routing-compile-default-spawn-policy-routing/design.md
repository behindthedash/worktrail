## Context

`compile_run_plan`'s model path is `runner = spawn or _default_spawn`
(src/worktrail/conductor/compile.py:463). `_default_spawn` (compile.py:352) calls
`spawnlib.spawn_agent(prompt, cwd, timeout=timeout, log=log)` — every other parameter
takes its default, and spawnlib's default is `agent="claude"` with
`fallback_agent=None`. The machinery this change needs already exists one layer down:

- `spawn_agent(..., agent=, model=, fallback_agent=)` walks an ordered chain through the
  persisted capacity gates (`agent_capacity.check`) and degrades to the first ungated
  hop; a single gated agent re-raises `ProviderUnavailable`.
- Repo policy routing (`worktrail-go-policy.yaml`, plus the machine-wide routing file)
  resolves through `router/policy.py`: `load_policy(repo)` → `resolve_routing(policy,
  route, risk)` returns `{agent_cli, agent_model, fallback[], roles, purpose_tiers}`;
  flat keys (`agent_cli`/`agent_model`/`fallback_agent_cli`) are the last-resort layer.
- Provider precedence (explicit invocation > repository policy > machine-wide env >
  detected host > claude) has a single implementation: `invocation_context.resolve()` /
  `_resolve_agent` — live.py's `_detect_default_agent` deliberately routes through it so
  it "can no longer drift from the front door's resolver".
- Full-real threads its chain as plain data: CLI flags → `LiveSpawn(fallback_chain=[str])`,
  names only; per-hop models resolve inside `spawn_agent` via `default_model_for_agent`.

The sibling change `model-tier-routing-remove-env-model-overrides` (merged, archived
2026-08-25) removed env-var *model* overrides from `default_model_for_agent()`:
resolution there is config-file driven only. Its Decisions bind this change where they
overlap.

The live incident (2026-08-25): a scope-check stalled ~2h on a claude outage until
manually routed through the `spawn=` seam with opencode +
`openrouter/stealth/ox-alpha`.

## Goals / Non-Goals

**Goals:**

- Default compile spawns resolve provider/model/fallback-chain from repo policy with
  full-real's precedence: explicit invocation > policy `agent_cli`/`agent_model` >
  routing fallback chain.
- Outage degradation for free: capacity gates walk the resolved chain instead of dying
  on claude alone; all-hops-gated lands in compile's existing give-up baseline path.
- Both production entry points benefit with zero caller changes: the `worktrail-compile`
  CLI and `live.apply_run_plan`'s un-injected path share `_default_spawn`.
- Keep the `spawn=` injection seam byte-identical (tests pin it by patching
  `_default_spawn`; injected callers keep full control of their own policy).

**Non-Goals:**

- No per-task tier routing for compiles: `routing.tiers`/`routing.roles`/
  `routing.defaults[route][risk]` are task-shaped machinery (complexity/domain/role/route),
  and a compile has no task yet. Only the flat policy primary + configured chain apply.
- No effort threading for the compile spawn (no reasoning-effort requirement on a
  scope-check).
- No new environment variables of any kind — sibling reconciliation (below).
- No change to `compile_run_plan`'s signature, cache behavior, or validation.

## Decisions

**D1 — Reuse the existing resolvers; hand-roll nothing.**
Agent resolution goes through `invocation_context.resolve(agent=<explicit>,
policy_agent=policy.get("agent_cli")).agent_cli` with live.py `_detect_default_agent`'s
same warn-and-fall-back-to-claude posture on `ValueError`. Primary model comes from the
policy entry when set, else `spawnlib.default_model_for_agent(agent)`. The chain comes
from `resolve_routing(policy, route="", risk="")["fallback"]` — with an empty
route/risk no defaults-table match is possible, so this yields exactly the flat-key
primary plus the configured chain (`routing.fallback`, else the single-entry
`fallback_agent_cli` chain), which is precisely the precedence this change specifies.
Alternatives considered: duplicating the precedence ladder in compile.py (the exact
drift class PR #338/#348 and the `_detect_default_agent` docstring warn about); reading
only flat policy keys and skipping `resolve_routing` (re-implements its
configured-vs-flat fallback logic).

**D2 — Resolution lives inside `_default_spawn`; the seam stays put.**
`_default_spawn(prompt, cwd, timeout, log)` keeps its positional signature and gains
keyword-only `agent=None, model=None, fallback_chain=None` appended for the explicit
invocation channel; `cwd` is already the resolved repo root at both call paths, which is
what `load_policy(cwd)` needs. `compile_run_plan` and its `spawn=` contract do not
change. Alternatives considered: resolving in `compile_run_plan` and widening its
signature (touches the pinned seam surface for no behavioral gain); module-level config
(rejected: ambient state, test hermeticity).

**D3 — Explicit invocation = three new CLI flags on `worktrail-compile`.**
`--agent`, `--model`, `--fallback-chain` mirror full-real's flag semantics (chain as an
ordered comma-separated name list, names only). Omitted → None → policy decides. This
is what makes "explicit invocation" the top precedence rather than a dead letter.
Alternative considered: treating host-detection env (`OPENCODE_PARENT`, `CODEX_CI`) as
the explicit layer — rejected; those are pre-existing *provider identity* signals that
`invocation_context.resolve()` already orders correctly beneath explicit-and-policy, not
invocation values.

**D4 — Chain is names-only, matching full-real parity.**
`routing.fallback` entries may carry `agent_model`/`effort`, but the threaded shape is
`list[str]` (exactly `LiveSpawn.fallback_chain`); per-hop models resolve inside
`spawn_agent` via `default_model_for_agent(hop)` (config-file driven). Per-hop model
overrides from the chain would require extending spawnlib's `fallback_agent` shape —
out of scope, and identical to how full-real behaves today.

**D5 — Sibling reconciliation adopted verbatim.**
From `archive/2026-08-25-model-tier-routing-remove-env-model-overrides`: (D1) hard
no-new-env-channels stance — the compile spawn adds no env lookup, and model selection
is config-driven only (policy `agent_model` > `routing.agents.<agent>.default_model`,
raising `OperatorConfigError` if neither is declared);
(D2) uniform resolution shape via `default_model_for_agent`; config-driven
`routing.tiers`/`roles`/`fallback` selection is the mechanism, consulted — never
re-derived. Concretely: this change introduces zero `os.environ` reads; the only env
consultation left in the chain is `invocation_context.resolve()`'s pre-existing
provider-identity detection and spawnlib's own file-path override vars, both untouched
by the sibling and not model overrides.

## Risks / Trade-offs

- [Policy load adds file I/O to every compiling run] → Mitigation: `load_policy` /
  `resolve_routing` are defensive by contract (never raise; malformed config degrades to
  warnings + unconfigured), and the compile already pays comparable I/O on the same
  path (`_resolve_purpose_tiers` reads the same policy).
- [Repos whose policy pins a non-claude `agent_cli` silently move their compiles off
  claude] → Mitigation: that is the specified behavior; the compile log line records
  what was resolved, and `--agent claude` restores today's behavior explicitly.
- [All-hops-gated now surfaces as a baseline-plan note instead of a loud stall] →
  Mitigation: unchanged from today's failure posture for any spawn exception
  (`give_up` logs the reason into the RunPlan notes); new tests pin the degradation.
- [Test hermeticity against operator machines with routing configured] → Mitigation:
  tests pass `environ=`/tmp-dir policy fixtures explicitly, the same pattern the
  sibling's D4 established for model-defaults tests.

## Migration Plan

Single PR, no data or config migration: unconfigured repos behave byte-identically;
configured repos get the documented routing behavior. Rollback = revert; no state to
unwind.

## Open Questions

None.
