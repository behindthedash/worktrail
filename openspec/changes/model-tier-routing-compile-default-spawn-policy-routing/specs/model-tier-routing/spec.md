## ADDED Requirements

### Requirement: The default compile spawn routes through repo policy with full-real's precedence
When no spawn callable is injected, the scope-check compile SHALL resolve its worker
provider, model, and fallback chain with the same precedence as the orchestrator's
full-real runs: an explicit invocation value first, then the repository policy's
`agent_cli`/`agent_model`, then the configured routing fallback chain — instead of
unconditionally spawning the hardcoded claude default. A repository with no policy
configuration SHALL resolve exactly as before this change (claude with the config-file
default model and no fallback hops).

#### Scenario: Explicit invocation wins over policy
- **WHEN** a compile is invoked with an explicit agent override (e.g. `--agent opencode --model openrouter/stealth/ox-alpha`) and the repo's policy pins a different `agent_cli`
- **THEN** the spawned worker SHALL use the explicitly invoked agent and model, never the policy values

#### Scenario: Policy agent_cli/agent_model applies when nothing is explicit
- **WHEN** no explicit invocation override is given and the repo's policy sets `agent_cli: opencode` with `agent_model: openrouter/stealth/ox-alpha`
- **THEN** the spawned worker SHALL run on opencode with that model instead of the hardcoded claude default

#### Scenario: Configured fallback chain threads into the spawn
- **WHEN** the repo's policy (or its machine-wide routing file) configures `routing.fallback` (or the flat `fallback_agent_cli`) and no explicit chain overrides it
- **THEN** the spawned worker SHALL carry that ordered chain so a capacity-gated primary degrades to the next configured hop instead of failing on the primary alone

#### Scenario: Primary outage degrades to the next healthy provider
- **WHEN** the resolved primary provider is capacity/billing gated at spawn time and a later hop in the resolved chain is ungated
- **THEN** the scope-check SHALL complete on that later hop rather than stalling on or dying to the gated primary

#### Scenario: Every provider unavailable degrades to the baseline plan
- **WHEN** every provider in the resolved chain is gated and the compile therefore cannot spawn any worker
- **THEN** the compile SHALL degrade to the artifact's own baseline dependency/file-scope plan (its existing give-up path) and SHALL NOT fail the run

#### Scenario: Unconfigured repo is byte-identical to pre-change behavior
- **WHEN** a repo has no policy file, no machine-wide routing file, and no explicit invocation override
- **THEN** the compile SHALL spawn claude with the config-file default model and no fallback hops, identical to behavior before this change

### Requirement: No environment-variable channel is added for compile model selection
The compile spawn's model selection SHALL remain config-driven only — repository/machine
routing configuration and the operator's model-defaults config — consistent with
`model-tier-routing-remove-env-model-overrides`. This change SHALL NOT introduce any new
environment-variable override for the compile's agent or model.

#### Scenario: Ambient model env vars do not influence the compile spawn
- **WHEN** a machine's environment carries model-override style variables (e.g. `ORCH_OPENCODE_MODEL`, `ORCH_CODEX_MODEL`) and a compile resolves its worker model
- **THEN** the resolved model SHALL come only from the policy `agent_model` (when set) or the operator model-defaults config falling back to the per-agent constant — never from an ambient variable

#### Scenario: Model-defaults config still governs unrouted agents
- **WHEN** the policy sets `agent_cli: codex` with no `agent_model`, and the operator model-defaults config maps `codex: gpt-5.6-luna`
- **THEN** the compile spawn SHALL run codex on `gpt-5.6-luna`
