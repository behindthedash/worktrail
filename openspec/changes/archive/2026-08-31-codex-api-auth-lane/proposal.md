## Why

The `model-tier-routing` spec still says "A codex `api` target SHALL be rejected by the
loader until its per-spawn auth selection is live-verified (design D6)" — written when no
verified mechanism existed. Task 3.6 of `routing-target-selector` (PR #786) then
live-verified the mechanism against codex-cli 0.149.1: `-c preferred_auth_method=apikey`
and `OPENAI_API_KEY` are both inert (a persisted ChatGPT login always wins), but
`CODEX_HOME` isolation genuinely switches auth — a separate home with
`codex login --with-api-key` run against it is an independent API-key credential store.
The machinery already exists: `prepare_codex_child_environment(codex_home_override,
inherit_auth=...)` (`router/skill_dispatch.py`) selects/creates a home and optionally
inherits ChatGPT auth, and `spawn_agent`'s `_prepare_child_env` calls it for every codex
cell — today always with the defaults, so a codex `api` cell would silently spawn on the
operator's ChatGPT subscription: the wrong pool, with no warning.

## What Changes

- A codex `api` target declares `auth: {codex_home: <path>}` — a home the operator
  provisioned once with `codex login --with-api-key`. A codex-api cell spawns with
  `CODEX_HOME=<that path>` and `inherit_auth=False`, so the subscription login can never
  leak into the API lane.
- Fail loud before launch when a codex-api cell has no `auth.codex_home`, or the declared
  home has no `auth.json` (existence check only — the file is never read), mirroring the
  claude-api `auth: {env: ...}` contract.
- Codex `subscription` cells are byte-identical to today (isolated Worktrail home,
  ChatGPT auth inherited).
- The spec's loader-rejection sentence is replaced by the above contract.

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-tier-routing`: the "Harness auth follows the target's pool" requirement gains the
  codex api lane and drops the interim rejection clause.

## Impact

- `src/worktrail/orchestrator/spawnlib.py`: `_prepare_child_env`'s codex branch resolves
  override/inherit from the cell's pool+auth; new `OperatorConfigError` paths.
- `tests/orchestrator/test_spawnlib.py`: new coverage.
- `router/skill_dispatch.py` unchanged: its `--codex-home`/`--no-inherit-codex-auth`
  flags already give the front door the same control explicitly.
