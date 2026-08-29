## Why

Routing today is one file but not one mechanism. `~/.worktrail/routing.yaml` carries three
hand-ordered agent lists (`fallback`, `drain.agent`+`drain.fallback_agents`, and the implicit
order in `agents`), a per-agent default-model table that duplicates the `t2-build` tier row,
and a `roles.review` entry pinned to a literal CLI+model. Review of the code paths on
2026-08-27 (worktrail `d080550`) verified these consequences:

- **Tiers have no fallback.** `tiers` is a provider×tier matrix, but `dispatch.agent_for`
  reads only the run-default provider's column; a fallback hop uses
  `agents.<hop>.default_model` (`spawnlib.py:790-792`, `:942`), so a `t1-deep` task that
  degrades from `claude:opus` lands on `codex:gpt-5.6-terra` (the T2 default) while
  `gpt-5.6-sol` sits unused in the same row.
- **The pinned reviewer has no recovery.** `live.py:2415-2416` disables the fallback chain
  for a judgment role pinned to a non-default agent. When drain has already switched the
  run to codex/opencode because claude is gated, every `review` spawn raises
  `ProviderUnavailable` and `_safe_drive` marks the task failed. The fallback chain is
  disabled in exactly the situation it exists for.
- **A retired model is invisible.** `opencode/x-preview-f-free` (three of four tier cells plus
  `agents.opencode.default_model`) no longer exists. Probed live: `opencode run` exits 1 with
  `{"type":"error","error":{"name":"UnknownError","data":{"message":"Unexpected server
  error..."}}}`; `classify_failure` maps that to `transport` (30 s cooldown), the spawn
  retries the same dead cell, records nothing durable, and drain's circuit breaker stops the
  night. Nothing in the package lists a harness's models, although `opencode models` is a
  cheap listing command. The `--init` starter template ships `opencode/deepseek-v4-flash-free`,
  which is also absent from `opencode models`.
- **"Subscription first, API by opt-in" is not expressible.** `api_opt_in` guards only the
  `openrouter`/`api` literals; `opencode/claude-opus-5` in `t1-deep` is paid Zen spend with no
  opt-in because `opencode` is a first-class CLI. The config cannot distinguish a harness
  (the CLI spawned) from the account pool it bills (subscription, free tier, API key), so a
  weekly-limit gate on the claude subscription also blocks a would-be claude API lane.
- **`drain:` is redundant and partly dead.** Its agent list equals `fallback:`; the nightly
  cron passes `--agent/--fallback-agent` explicitly so the block is bypassed
  (`drain.py:2151`); drain's top-level `worktrail-go auto` session gets no `--model` at all.

## What Changes

- **BREAKING (config):** replace `agents`, `fallback`, `drain.agent`, `drain.fallback_agents`,
  and literal-CLI `roles.*.agent_cli/agent_model` with an ordered `targets` table
  (`{harness, pool, auth}`), tier rows whose cells are keyed by target name, `roles` that name
  a tier (plus optional `prefer`/`independent`), and a top-level `default_tier`. Legacy keys
  fail loud naming `worktrail-routing --migrate`, which rewrites the file deterministically.
- **One selector.** `runtime/selection.select_cell(routing, tier, prefer, exclude_harness,
  capacity)` walks a tier row across targets in preference order, skips `api`-pool targets
  without `api_opt_in`, skips capacity-gated `(target, model)` cells, and raises only when the
  row is exhausted. Every spawn path calls it: orchestrator task/review spawns, `spawn_agent`'s
  in-spawn hop, drain candidate selection and drain's front-door session, the conductor
  compile spawn, and `skill_dispatch`.
- **Harness ≠ target.** Two targets may share a harness (`claude-sub` and `claude-api`). The
  claude API lane spawns `claude --bare` (verified: `--bare` makes Anthropic auth "strictly
  ANTHROPIC_API_KEY or apiKeyHelper; OAuth and keychain are never read") with the key in the
  child env; the subscription lane strips it. OpenRouter/Zen/Google lanes use the `opencode`
  harness (verified: `opencode models` lists `openrouter/...` ids; `opencode auth list` holds
  OpenRouter and Zen credentials). Capacity gates key on `target:model`.
- **Tier-preserving fallback.** A hop stays in the task's tier row; the review role degrades
  along its own row (soft `independent` preference for a harness other than the
  implementer's) instead of failing.
- **Model liveness.** New failure class `model_unavailable` (24 h cooldown, dashboard line);
  `worktrail-routing --check` validates every opencode cell against `opencode models`, warns on
  non-free ids in a `free`-pool target and on effort values outside the harness's vocabulary;
  drain runs it as a preflight next to `validate_agent_runtime`. After the primary cell's
  retries are exhausted on an infra failure, `spawn_agent` hops to the next cell in the same
  call instead of returning an empty result.
- **Starter template** ships only subscription targets plus a commented free-pool example, and
  tells the operator to run `--check`.

## Capabilities

### New Capabilities

(none — all changes modify `model-tier-routing` and `drain-operator-config`)

### Modified Capabilities

- `model-tier-routing`: targets/tier-row schema, single selector, roles-by-tier, target-keyed
  capacity, retired-model failure class, `--check`/`--migrate`, template validity, effort
  vocabulary validation. Removes the per-agent default-model requirement and the literal
  `claude:opus` review pin.
- `drain-operator-config`: drain derives its candidate order from `targets`; the `drain:` block
  keeps only `max_workers`; the front-door session resolves through `default_tier`.

## Impact

- `src/worktrail/router/policy.py` (schema, validation, `--migrate` translation),
  `src/worktrail/runtime/selection.py` (selector), `src/worktrail/orchestrator/spawnlib.py`
  (target-aware `build_cmd`/`spawn_agent`, child env, infra-failure hop, capacity keys),
  `src/worktrail/orchestrator/agent_capacity.py` (`model_unavailable`, target keys),
  `src/worktrail/orchestrator/dispatch.py` + `live.py` (tier resolution, remove pinned-reviewer
  no-fallback branch, remove `default_model_for_agent`), `src/worktrail/drain/drain.py`,
  `src/worktrail/conductor/compile.py`, `src/worktrail/router/skill_dispatch.py`,
  `src/worktrail/router/routing_cli.py` (`--check`, `--migrate`, template),
  `src/worktrail/router/dashboard.py`, `src/worktrail/runtime/routing_source.py`.
- Supersedes the fallback-resolution half of open change
  `model-tier-routing-compile-default-spawn-policy-routing` (its `--agent/--model/
  --fallback-chain` flags stay; its resolver becomes a `select_cell` call) and item 2 of queued
  brief `20260826-143940` (skill_dispatch has no fallback) — both become callers of the selector.
- Operator follow-up outside this repo: `~/bin/worktrail-drain-nightly.sh` (devops) drops its
  hardcoded `--agent/--fallback-agent` once this ships so routing.yaml is the only order.
- Codex API-key lane: `codex login --with-api-key` exists, but a per-spawn selector between
  ChatGPT auth and the key is unverified; the design marks it a verification task, not an
  assumption.
