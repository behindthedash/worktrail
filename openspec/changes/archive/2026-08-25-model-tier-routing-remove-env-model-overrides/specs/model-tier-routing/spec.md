## ADDED Requirements

### Requirement: Default model resolution is config-file driven only
Codex and opencode default models SHALL resolve from the operator-maintained
model-defaults config (`worktrail_home()/model-defaults.yaml`, or its file-override env)
falling back to the hardcoded per-agent constant — with NO environment-variable override
layer. No `ORCH_CODEX_MODEL` or `ORCH_OPENCODE_MODEL` variable SHALL influence spawned
model selection. Claude's resolution (config file > hardcoded constant) is unchanged.
Config-driven routing (`routing.tiers`/`routing.roles`/`routing.fallback`) continues to
override these fallback defaults exactly as before this change.

#### Scenario: Ambient codex env var is ignored
- **WHEN** a machine's environment carries `ORCH_CODEX_MODEL=gpt-5.6-sol`, no
  model-defaults entry for codex exists, and a spawn resolves its default codex model
- **THEN** the resolved model SHALL be the hardcoded codex constant, never the ambient
  env var's value

#### Scenario: Ambient opencode env var is ignored
- **WHEN** a machine's environment carries `ORCH_OPENCODE_MODEL=provider/custom` and a
  spawn resolves its default opencode model with no model-defaults entry
- **THEN** the resolved model SHALL be the hardcoded opencode constant, never the ambient
  env var's value

#### Scenario: Config-file entry still wins over the hardcoded constant
- **WHEN** the model-defaults config maps `codex: gpt-5.6-luna` and a spawn resolves its
  default codex model
- **THEN** the resolved model SHALL be `gpt-5.6-luna`

#### Scenario: Routing-config model selection is unaffected
- **WHEN** a task resolves through a `routing.tiers` or `routing.roles` entry carrying an
  explicit `agent_model`
- **THEN** dispatch SHALL use that entry's model regardless of any default-model
  resolution, identical to behavior before this change

#### Scenario: Operators migrate via the config file
- **WHEN** an operator previously relied on `ORCH_CODEX_MODEL` or `ORCH_OPENCODE_MODEL`
- **THEN** setting the equivalent agent key in the model-defaults config SHALL produce the
  previously overriden model, and the removed variables SHALL have no effect if left set
