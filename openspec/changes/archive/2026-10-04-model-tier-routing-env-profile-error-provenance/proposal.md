# Model-tier-routing: env-profile error provenance

## Why

`spawnlib._apply_env_profile` emits one byte-identical `OperatorConfigError` for two
structurally different failures: a resolved routing table that carries no `env_profiles`
at all (a resolver/caller fault — the table was dropped or never threaded into the spawn)
and a populated table that genuinely does not declare the named profile (an operator-config
error). The message hardcodes the second hypothesis — "not declared in routing.env_profiles
in `<file>` -- add an `env_profiles: {...}` entry there" — so when the resolved table is the
fault, it names the operator's routing file and prescribes editing it, both wrong.

This misfiled real work: on 2026-10-02, brief `20261002-030946` was captured straight from
this message while the actual cause was `resolve_routing()` dropping the `env_profiles` key
before the spawn read it (fixed separately in PR #1380). The resolver bug is gone; the
misdirecting raise is not — and a future drop of the same class would be misfiled identically.

## What Changes

- `_apply_env_profile` distinguishes its two inputs at the raise site:
  - **Populated resolved table, profile genuinely undeclared** — today's message kept
    verbatim (operator-config error naming the target, the profile and the routing file).
  - **Empty/absent resolved table while the cell names a profile** — a new error that names
    the target, the profile and the resolved routing source, attributes the fault to the
    resolved table (resolver/caller provenance), does not instruct editing the routing file,
    and — when `load_policy()` sees the profile declared — says so, so the operator learns
    the config is innocent.
- `build_child_env` and the spawn path thread the loader's declared `env_profiles` table
  through for that diagnostic attribution only; the table is never consulted for resolution
  (resolution still uses the resolved table the cell was selected from, without re-reading
  policy). The readiness preflight (`router/spawn_readiness.py`) deliberately keeps judging
  the resolved table alone and does not pass it.
- Sibling-raise audit recorded, no behavior change: `build_child_env`'s neither-`auth.env`-
  nor-`auth.profile` claude-api raise, `codex_api_home`'s two raises, and the both-auth-
  sources raise are each single-input conditions whose messages state the observed cell, not
  a hypothesis about the routing file — design.md records why none of them shares the defect.
- Sibling-change reconciliation for `openspec/changes/add-env-profiles` (all 10 tasks
  checked, not yet archived; it authored the current requirement text and the current raise):
  this delta **adopts** its requirement wording as the base and **differs** only by adding the
  resolved-table-omitted distinction described above. None of its decisions are reopened or
  re-derived, and its directory is untouched.
- No behavior change beyond the error-message attribution and the audit outcome; no secret
  value is ever printed (the empty-table branch opens no profile file at all).

## Capabilities

### New Capabilities

(none)

### Modified Capabilities

- `model-tier-routing`: the undeclared-profile scenario of "A worker environment can be
  supplied from a declared profile without storing values" is scoped to a populated resolved
  table, and a scenario is added pinning the empty/absent-resolved-table attribution
  (resolver/caller provenance; no routing-file edit instruction; the config-is-declared
  statement when `load_policy()` sees it).

## Impact

- `src/worktrail/orchestrator/spawnlib.py`: `_apply_env_profile` (raise-site branch, new
  diagnostic-only parameter), `build_child_env` (new optional keyword argument), `spawn_agent`
  (hoist the existing `load_policy()` call into a variable and thread the declared table).
- `src/worktrail/router/spawn_readiness.py`: unchanged (its input stays the resolved table —
  see the archived 2026-10-03 spawn-readiness-preflight change; its FAIL text picks up the new
  attribution naturally and still names the target and the profile).
- Tests: `tests/orchestrator/test_spawnlib.py` (regression coverage for both inputs and the
  innocence clause), `tests/router/test_spawn_readiness.py` (assertions extended to the new
  provenance markers).
- No API, dependency, or configuration-schema change.
