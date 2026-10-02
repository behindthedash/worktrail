## Why

Worktrail spawns headless `claude` workers with `--setting-sources project,local`, which
excludes the operator's user-level `~/.claude/settings.json`. That exclusion is deliberate and
load-bearing: a user-level Stop hook fires on every worker that commits or writes, forcing an
extra continuation turn whose text becomes the final message the orchestrator parses for its
report-back JSON (investigation 20260711-130900).

The exclusion is right, but it also drops that file's `env` block — the only place an alternate
provider endpoint may be configured. A spawn whose endpoint lives only there makes **no API call
at all** and still **exits 0**, which is indistinguishable from an empty-but-successful run.

Measured on 2026-10-01 against `claude` 2.1.280, same prompt, same DeepSeek endpoint:

| Scenario | `duration_api_ms` | `input_tokens` | `total_cost_usd` | `stop_reason` |
|---|---|---|---|---|
| Clean env, default setting sources | 3379 | — | — | `end_turn` |
| Clean env, `--setting-sources project,local` | 0 | 0 | 0 | `stop_sequence` |

`ANTHROPIC_BASE_URL` is unset in a plain login shell, so nothing else carries it. The failure is
also context-dependent in a way that defeats manual testing: a spawn launched from an interactive
session inherits that session's environment and works, while the identical cell spawned from a
drain, cron job, or bridge process does not.

A second, symmetric defect surfaced while specifying the fix. `build_child_env`'s claude
`subscription` lane removes only `ANTHROPIC_API_KEY`, leaving `ANTHROPIC_BASE_URL`,
`ANTHROPIC_AUTH_TOKEN`, and the model-alias overrides in place. Since `spawn_agent` builds every
child env from `{**os.environ, ...}` and an interactive session carries exactly those, a
subscription worker selected *because the tier wanted Anthropic* is silently sent to the ambient
endpoint instead.

## What Changes

- Add a `routing.env_profiles` section: named environment sources, each declaring a JSON file, the
  key names to copy from its `env` object, and an optional assertion on a key's value. A profile
  stores no values — only a path, key names, and a non-secret expected value.
- Add `auth.profile` as an alternative to `auth.env` for a target's auth lane, and make the two
  mutually exclusive: they name two sources for one lane, and silently picking one is the failure
  class this change exists to remove.
- Resolve a target's profile before launch and copy the declared keys into the worker's
  environment, compensating for the settings-source exclusion without re-including user settings.
- Fail loudly — `OperatorConfigError` naming target, profile, file, and offending key — when a
  profile's file is missing, malformed, lacks an `env` object, omits a declared key, or fails its
  assertion. A message never contains a value for a key not named in `expect`.
- Close the mirror-image defect: the claude `subscription` lane now removes every
  provider-redirect variable, not just `ANTHROPIC_API_KEY`.
- Omit `--bare` for a profile-backed `api` cell. `--bare` skips every settings-injected hook,
  including the worktree guard; injected credentials already pin the endpoint, which is the only
  thing `--bare` was doing for that lane.
- Carry a target's full definition — `api_opt_in`, `auth`, and the one referenced `env_profiles`
  entry — through `explicit_cell_override`'s throwaway routing file, which replaces rather than
  layers over the operator's file. Without this an api- or profile-backed target is unreachable
  through every explicit-override path.
- Surface profile resolution in `worktrail-routing --check`, with a `FAIL` cell and a non-zero
  exit, deliberately without recording an `agent_capacity` gate.

## Capabilities

### New Capabilities

<!-- None. -->

### Modified Capabilities

- `model-tier-routing`: adds the `env_profiles` section and the `auth.profile` auth lane; makes
  the claude `subscription` lane strip every provider-redirect variable; makes a profile-backed
  `api` cell omit `--bare`; makes `explicit_cell_override` reproduce a target's whole definition.
