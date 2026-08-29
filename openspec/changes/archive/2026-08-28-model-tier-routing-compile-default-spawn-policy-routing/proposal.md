## Why

`compile.py:_default_spawn` (src/worktrail/conductor/compile.py:352) calls
`spawnlib.spawn_agent` with all defaults, which hardcodes `agent="claude"` with no
fallback chain and no policy-routing consultation. A claude capacity/billing outage
therefore blocks every OpenSpec scope-check compile even when codex/opencode providers
are healthy — verified live 2026-08-25, when a scope-check stalled ~2h until it was
routed manually through `compile_run_plan`'s documented spawn injection seam (`spawn=`)
with `--agent opencode --model openrouter/stealth/ox-alpha`.

## What Changes

- Make the default compile spawn resolve its agent/model/fallback-chain from repo policy
  routing with the same precedence as live.py full-real: explicit invocation >
  repository policy `agent_cli`/`agent_model` > routing fallback chain.
- `_default_spawn` passes the resolved `agent`, `model`, and ordered `fallback_agent`
  chain to `spawnlib.spawn_agent`, so a scope-check degrades to the next healthy
  provider instead of dying on claude alone (capacity-gate walk already built into
  `spawn_agent`; exhausting every hop still degrades to the baseline plan via
  compile's existing give-up path — never fails the run).
- Add explicit invocation flags (`--agent`, `--model`, `--fallback-chain`) to the
  `worktrail-compile` CLI so an operator override wins over policy, mirroring
  full-real's flag surface.
- Keep the existing `spawn=` injection seam working unchanged (tests pin it).
- Reconciliation with merged sibling change
  `model-tier-routing-remove-env-model-overrides`
  (archive/2026-08-25-model-tier-routing-remove-env-model-overrides): adopt its
  decisions where they overlap — no new env-var override channels are introduced;
  model selection stays config-file driven (`routing.tiers`/`roles`/`fallback` +
  `routing.agents.<agent>.default_model` are the mechanism), never re-derived as env
  vars.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-tier-routing`: Add a requirement that the conductor's default compile spawn
  resolves its provider/model/fallback-chain from repo policy routing with full-real's
  precedence (explicit invocation > repo policy `agent_cli`/`agent_model` > routing
  fallback chain), degrading across providers on capacity gates; no environment-variable
  override channel is added.

## Impact

- `src/worktrail/conductor/compile.py`: `_default_spawn` (~line 352) resolves routing
  before spawning; `main()` gains the three flags; `compile_run_plan`'s signature and
  `spawn=` contract unchanged.
- `src/worktrail/router/invocation_context.py` + `src/worktrail/router/policy.py`:
  consumed read-only (`resolve()`, `load_policy()`, `resolve_routing()`); no changes.
- `src/worktrail/orchestrator/spawnlib.py`: consumed as-is (`spawn_agent`,
  `default_model_for_agent`); no changes.
- All production compile call sites (`worktrail-compile` CLI,
  `live.apply_run_plan`) inherit the routed default automatically — the in-run path
  benefits without any caller change.
- Tests: `tests/conductor/test_compile.py` keeps patching `_default_spawn` (the pinned
  seam); new coverage for policy-routed resolution, explicit-flag precedence, and
  fallback-chain threading.
