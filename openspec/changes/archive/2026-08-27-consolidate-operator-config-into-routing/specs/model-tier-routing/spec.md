## ADDED Requirements

### Requirement: Per-agent default models resolve from routing only

`spawnlib.default_model_for_agent(agent)` SHALL resolve an agent's default model exclusively
from the resolved routing table's `agents.<agent>.default_model`. There SHALL be no hardcoded
per-agent model constant in the package, no `model-defaults.yaml` file, and no
environment variable carrying a model value. Environment variables that locate a config file
(`WORKTRAIL_HOME`, `WORKTRAIL_ROUTING_FILE`, `WORKTRAIL_AGENT_CAPACITY_CACHE`) are unaffected.

#### Scenario: Routing declares the default
- **WHEN** routing declares `agents: {codex: {default_model: gpt-5.6-sol}}` and a spawn
  resolves its default codex model
- **THEN** the resolved model SHALL be `gpt-5.6-sol`

#### Scenario: No default declared fails loud
- **WHEN** routing declares no `agents.codex.default_model` and a spawn requires a default
  codex model
- **THEN** resolution SHALL raise an error naming the routing file path, the missing
  `agents.codex.default_model` key, and the `worktrail-routing --init` remedy -- and SHALL NOT
  fall back to any package-resident model string

#### Scenario: Ambient model env vars have no effect
- **WHEN** a machine's environment carries `ORCH_CODEX_MODEL`, `WORKTRAIL_MODEL_DEFAULTS_FILE`,
  or `WORKTRAIL_PROVIDER_MODEL_CATALOG_FILE` and a spawn resolves its default model
- **THEN** the resolved model SHALL come from routing alone, and the variables SHALL have no
  effect if left set

#### Scenario: Explicit routing entries still win
- **WHEN** a task resolves through a `routing.tiers` or `routing.roles` entry carrying an
  explicit `agent_model`
- **THEN** dispatch SHALL use that entry's model, and `default_model_for_agent()` SHALL not be
  consulted -- identical to behavior before this change

### Requirement: The tier table supports a nested per-agent form

`routing.tiers` SHALL accept `tiers.<tier>.<agent>: {model, effort}` in addition to the flat
`<tier>-<agent>` key form. `resolve_tier_map()` SHALL produce identical output from either
shape, so `dispatch.agent_for()`'s existing `<tier>-<agent>` lookup is unchanged. Use of the
flat form SHALL emit a deprecation warning through `load_policy()`'s existing `meta["warnings"]`
channel without failing the load.

#### Scenario: Nested form resolves
- **WHEN** routing declares `tiers: {t2-build: {claude: {model: sonnet, effort: medium}}}`
- **THEN** `resolve_tier_map()` SHALL yield the same entry as the flat
  `t2-build-claude: {agent_cli: claude, agent_model: sonnet, effort: medium}`

#### Scenario: Flat form still loads, with a warning
- **WHEN** routing declares only flat `<tier>-<agent>` keys
- **THEN** `resolve_tier_map()` SHALL resolve them exactly as before this change, and
  `load_policy()`'s `meta["warnings"]` SHALL name the deprecated form

#### Scenario: Nested wins on collision
- **WHEN** routing declares both `tiers.t2-build.claude` and `t2-build-claude`
- **THEN** the nested entry SHALL win, and a warning SHALL name the conflicting flat key

### Requirement: Provider/model intent has exactly one machine-wide file

The resolved routing table SHALL be the only machine-wide source of provider/model intent.
No separate provider/model catalog file SHALL be read at runtime, and
`select_execution_target`'s candidate list SHALL be derived from routing
(`agents` + `tiers`) rather than from a synthesized sentinel model.

#### Scenario: Candidates carry real model names
- **WHEN** the drain resolves an execution target
- **THEN** each candidate SHALL carry the model name that will actually be spawned, never a
  placeholder such as `"configured-default"`

#### Scenario: Capacity gating keys on the spawned model
- **WHEN** one model of a provider is capacity-gated and another model of the same provider is
  not
- **THEN** selection SHALL advance to the ungated model of that provider before advancing to a
  later provider, rather than treating the whole provider as gated

## MODIFIED Requirements

### Requirement: Default model resolution is config-file driven only

Codex and opencode default models SHALL resolve from the resolved routing table's
`agents.<agent>.default_model` -- with NO environment-variable override layer and NO
package-resident fallback constant. No `ORCH_CODEX_MODEL` or `ORCH_OPENCODE_MODEL` variable
SHALL influence spawned model selection. Claude's resolution follows the same single path.
Config-driven routing (`routing.tiers`/`routing.roles`/`routing.fallback`) continues to
override these defaults exactly as before.

*(Supersedes the archived `model-tier-routing-remove-env-model-overrides` formulation, which
retained `model-defaults.yaml` and a hardcoded per-agent constant as the fallback tail. The
no-env-override decision is preserved and extended; the fallback tail is removed.)*

#### Scenario: Ambient codex env var is ignored
- **WHEN** a machine's environment carries `ORCH_CODEX_MODEL=gpt-5.6-sol` and a spawn resolves
  its default codex model
- **THEN** the resolved model SHALL come from `routing.agents.codex.default_model`, never the
  ambient env var's value

#### Scenario: Ambient opencode env var is ignored
- **WHEN** a machine's environment carries `ORCH_OPENCODE_MODEL=provider/custom` and a spawn
  resolves its default opencode model
- **THEN** the resolved model SHALL come from `routing.agents.opencode.default_model`, never
  the ambient env var's value

#### Scenario: Routing-config model selection is unaffected
- **WHEN** a task resolves through a `routing.tiers` or `routing.roles` entry carrying an
  explicit `agent_model`
- **THEN** dispatch SHALL use that entry's model regardless of any default-model resolution,
  identical to behavior before this change

#### Scenario: Config-file entry still wins over the hardcoded constant
- **WHEN** this requirement's earlier (archived) formulation described a hardcoded
  per-agent fallback constant behind the config file
- **THEN** that fallback constant no longer exists in this package -- `routing.agents.
  <agent>.default_model` is now the sole source, and its absence raises loud (see the
  sibling "No default declared fails loud" scenario) rather than falling back to any
  package-resident model string

#### Scenario: Operators migrate via the config file
- **WHEN** an operator previously relied on `ORCH_CODEX_MODEL`/`ORCH_OPENCODE_MODEL`, or
  maintained the now-removed `model-defaults.yaml`
- **THEN** setting the equivalent agent's `routing.agents.<agent>.default_model` SHALL
  produce the previously overridden/configured model, and neither the removed env vars
  nor a leftover `model-defaults.yaml` on disk SHALL have any effect
