## Why

`spawnlib.default_model_for_agent()` resolves codex/opencode default models through
three layers: `ORCH_CODEX_MODEL`/`ORCH_OPENCODE_MODEL` env vars > `model-defaults.yaml`
> hardcoded constants. The env-var layer is a second, undocumented override channel that
duplicates what `model-defaults.yaml` already provides operator-side, and it is exactly
the kind of ambient environment leakage that has caused false test failures on machines
with the vars set locally (see the hermeticity guards already required in
`tests/orchestrator/test_resilience_helpers.py`). Model selection should have one
operator-facing source of truth: the config file.

## What Changes

- **BREAKING**: Remove the `ORCH_CODEX_MODEL` and `ORCH_OPENCODE_MODEL` env-var lookups
  from `default_model_for_agent()` in `src/worktrail/orchestrator/spawnlib.py`. Codex and
  opencode defaults now resolve `model-defaults.yaml` > hardcoded constant only.
- Claude's resolution (`defaults.get("claude") > DEFAULT_CLAUDE_MODEL`) is unchanged — it
  never had an env override.
- `routing.tiers`/`routing.roles`/`routing.fallback` config-driven model selection
  (`dispatch.agent_for()`, `resolve_routing()`) is untouched — explicit per-role/tier
  models continue to win over any default.
- Update affected tests: delete/rename the env-override tests
  (`test_opencode_model_override_remains_supported`,
  `test_explicit_env_var_wins_over_file`) and simplify their hermeticity scaffolding;
  add coverage asserting ambient env vars are ignored.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-tier-routing`: Add a requirement pinning default-model resolution to
  config-file-driven only — `default_model_for_agent()` resolves `model-defaults.yaml`
  > hardcoded constant for codex/opencode (claude unchanged), with no environment-variable
  overrides; tier/role/fallback routing behavior is untouched.

## Impact

- `src/worktrail/orchestrator/spawnlib.py`: `default_model_for_agent()` (~line 415) drops
  two `os.environ.get()` calls; its docstring/comment block (~line 370) loses the env-var
  precedence mention.
- All callers (`live.py`, `check_agent_contract.py`, internal spawnlib call sites) inherit
  the new resolution automatically — no signature changes.
- Tests: `tests/orchestrator/test_resilience_helpers.py`, `tests/orchestrator/test_spawnlib.py`
  (hermeticity comments), and optionally `tests/fixtures/classifier_corpus.json` (a corpus
  entry referencing the removed env var stays valid as historical incident text).
- Operators who relied on `ORCH_*_MODEL` must move the value into
  `worktrail_home()/model-defaults.yaml` (or `$WORKTRAIL_MODEL_DEFAULTS_FILE`).
