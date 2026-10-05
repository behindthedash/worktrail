# model-tier-routing Specification

## Purpose
Defines the single machine-wide routing file that maps roles to complexity tiers and tiers to ordered
execution targets, with per-agent reasoning effort, capacity/auth/retirement gates, and deterministic
migration of legacy keys. Prevents provider/model intent from scattering across environment variables
and hardcoded defaults, and keeps a retired or gated model from being launched at all.
## Requirements
### Requirement: Agent-entry schema supports an optional reasoning-effort field
A `routing.tiers`/`routing.roles`/`routing.fallback` agent-entry (validated by
`policy._validate_agent_entry()`) SHALL accept an optional `effort` string field
alongside the existing `agent_cli`/`agent_model` fields. An entry with no `effort` key
SHALL resolve identically to today's behavior (no effort flag passed to the spawned
CLI).

#### Scenario: Agent-entry with effort validates and resolves
- **WHEN** a `routing.tiers` entry is `{agent_cli: codex, agent_model: gpt-5.6-sol, effort: high}`
- **THEN** `resolve_routing()`'s returned entry SHALL include `effort: "high"` alongside `agent_cli`/`agent_model`

#### Scenario: Agent-entry without effort is unaffected
- **WHEN** a `routing.tiers` entry is `{agent_cli: codex, agent_model: gpt-5.6-sol}` (no `effort` key)
- **THEN** `resolve_routing()`'s returned entry SHALL have `effort: None`, and dispatch SHALL behave exactly as before this change

### Requirement: build_cmd() translates a resolved effort into the correct per-agent CLI flag
`spawnlib.build_cmd()` SHALL accept an optional `effort` parameter and, when set,
append the agent-specific reasoning-effort flag to the spawned command: `--effort
<value>` for claude, `-c model_reasoning_effort=<value>` for codex, `--variant <value>`
for opencode. When `effort` is `None` or omitted, `build_cmd()`'s output SHALL be
byte-identical to its pre-change output for the same other arguments.

#### Scenario: Claude command includes --effort
- **WHEN** `build_cmd(prompt, agent="claude", model="opus", effort="xhigh")` is called
- **THEN** the returned argv SHALL include `["--effort", "xhigh"]`

#### Scenario: Codex command includes the -c config override
- **WHEN** `build_cmd(prompt, agent="codex", model="gpt-5.6-sol", effort="high")` is called
- **THEN** the returned argv SHALL include `["-c", "model_reasoning_effort=high"]`

#### Scenario: OpenCode command includes --variant
- **WHEN** `build_cmd(prompt, agent="opencode", model="opencode/deepseek-v4-flash", effort="high")` is called
- **THEN** the returned argv SHALL include `["--variant", "high"]`

#### Scenario: No effort configured leaves the command unchanged
- **WHEN** `build_cmd(prompt, agent="codex", model="gpt-5.6-terra")` is called with no `effort` argument
- **THEN** the returned argv SHALL contain no `-c model_reasoning_effort=...` (or equivalent) flag, identical to this function's behavior before this change

### Requirement: Review role defaults to claude:opus
`routing.roles` SHALL support a `review` entry naming a tier (`{tier, prefer?, independent?}`);
when absent, review SHALL resolve as `{tier: t1-deep, independent: true}` where a `t1-deep`
row exists, else `default_tier`. Judgment roles SHALL still ignore the task's own purpose and
complexity rows.

#### Scenario: Review role uses the configured override regardless of task complexity
- **WHEN** `roles.review: {tier: t1-deep}` is configured and a task with `complexity: trivial`
  is dispatched for the `review` role
- **THEN** the review spawn SHALL select from the `t1-deep` row, never the trivial row

### Requirement: A 3-tier complexity fallback resolves independent of task-purpose classification
`routing.tiers` SHALL support plain `trivial`/`standard`/`hard` complexity keys
(matching today's existing task-frontmatter `complexity` field), each resolving to an
explicit agent/model pair, usable immediately without any task-purpose classification
mechanism.

#### Scenario: A hard-complexity task resolves to the hard tier's agent/model
- **WHEN** `routing.tiers: {hard: {agent_cli: codex, agent_model: gpt-5.6-sol}}` is configured and a task has `complexity: hard`
- **THEN** `dispatch.agent_for()` SHALL resolve that task to `codex`/`gpt-5.6-sol` via the existing `tier_map` resolution path

### Requirement: Configuring nothing new preserves current behavior exactly
A repo or task that configures no `effort` field and no new tier/role entries SHALL
dispatch identically to how it did before this change — no schema addition in this
change SHALL alter behavior unless explicitly configured.

#### Scenario: Untouched repo/task is unaffected
- **WHEN** a repo has no `routing.tiers`/`routing.roles` entries using `effort`, and a task carries no tier-matching frontmatter beyond what already existed
- **THEN** dispatch resolves via the same precedence and produces the same `build_cmd()` argv as before this change shipped

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
No separate provider/model catalog file SHALL be read at runtime, and every candidate list
(drain, dashboard, run record) SHALL be derived from `targets` + `tiers` through
`select_cell`, never from a synthesized sentinel model or a hand-ordered agent list.

#### Scenario: Candidates carry real model names
- **WHEN** the drain resolves an execution target
- **THEN** each candidate SHALL carry the target name and model that will actually be
  spawned, never a placeholder such as `"configured-default"`

#### Scenario: Capacity gating keys on the spawned model
- **WHEN** one cell of a target is capacity-gated and another cell of the same target is not
- **THEN** selection SHALL advance to the ungated cell of that target before advancing to a
  later target, rather than treating the whole target as gated

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

### Requirement: Routing declares ordered execution targets that separate harness from pool
The routing table SHALL declare `targets`, an ordered mapping of free-form target names to
`{harness, pool, api_opt_in?, auth?}`. `harness` SHALL be one of the spawnable CLIs
(`claude`, `codex`, `opencode`); `pool` SHALL be one of `subscription`, `free`, `api`. Target
order SHALL be the preference order every selection walks. Two targets MAY share a harness.
An `api`-pool target SHALL be eligible for selection only when it declares `api_opt_in: true`;
otherwise the loader SHALL warn and the selector SHALL skip it.

#### Scenario: Two targets share the claude harness
- **WHEN** routing declares `claude-sub: {harness: claude, pool: subscription}` and
  `claude-api: {harness: claude, pool: api, api_opt_in: true, auth: {env: ANTHROPIC_API_KEY}}`
- **THEN** both SHALL load as distinct targets, and a capacity gate recorded for
  `claude-sub` SHALL NOT gate `claude-api`

#### Scenario: API pool without opt-in is skipped
- **WHEN** a target declares `pool: api` without `api_opt_in: true`
- **THEN** the loader SHALL emit a warning naming the target and the selector SHALL never
  return a cell from it

#### Scenario: OpenRouter is reached through the opencode harness
- **WHEN** a target declares `{harness: opencode, pool: api, api_opt_in: true}` and a tier
  cell for it names `openrouter/<vendor>/<model>`
- **THEN** the spawn SHALL run `opencode run --model openrouter/<vendor>/<model>`

### Requirement: Tier rows are keyed by target and a missing cell means the target cannot serve that tier
`tiers.<row>.<target>` SHALL be `{model, effort?}` keyed by a declared target name. A tier row
with no cell for a target SHALL exclude that target from selection for that row. A top-level
`default_tier` SHALL name the row used when a spawn has no more specific tier (front-door
sessions, drain candidate selection, any former per-harness default-model lookup).

#### Scenario: Missing cell excludes the target
- **WHEN** `t1-deep` declares cells for `claude-sub` and `codex-sub` only
- **THEN** selecting `t1-deep` SHALL never return an `opencode-free` cell even when every
  other cell is gated

#### Scenario: default_tier replaces per-harness default models
- **WHEN** a spawn path needs a model with no purpose, complexity, or role tier and
  `default_tier: t2-build` is declared
- **THEN** it SHALL select from the `t2-build` row, and no `agents.<harness>.default_model`
  key SHALL be consulted or required

### Requirement: A single selector walks a tier row across targets in preference order
`runtime.selection.select_cell(routing, tier, prefer=None, exclude_harness=None, capacity,
now)` SHALL be the only function that turns a tier into a spawnable `(target, harness, model,
effort)` cell. It SHALL: move `prefer` (a target name) to the front when that target has a
cell in the row; drop ineligible `api` targets and targets with no cell; order targets on a
harness other than `exclude_harness` ahead of those on it (soft exclusion, never a failure
cause); return the first cell whose `(target, model)` carries no active capacity gate; and
raise `NoExecutionTarget` naming every cell and its gate only when the row is exhausted. It
SHALL be pure and deterministic given `capacity` and `now`. Orchestrator task and review
spawns, `spawn_agent`'s in-spawn hop, drain candidate selection, the drain and skill-dispatch
front-door sessions, and the conductor compile spawn SHALL all resolve through it.

#### Scenario: Fallback stays in the task's tier
- **WHEN** a `t1-deep` task's first cell `claude-sub:opus` is gated and `codex-sub:gpt-5.6-sol`
  is declared in the same row and ungated
- **THEN** the spawn SHALL run `codex` with `gpt-5.6-sol` and the row's effort for that cell,
  never `codex-sub`'s `t2-build` model

#### Scenario: Preference reorders a row without removing fallbacks
- **WHEN** `roles.review: {tier: t1-deep, prefer: codex-sub}` and `t1-deep` declares
  `claude-sub` before `codex-sub`
- **THEN** review SHALL select `codex-sub` first and degrade to `claude-sub` when
  `codex-sub`'s cell is gated

#### Scenario: Subscription pools precede free precede API by file order
- **WHEN** targets are declared in the order `claude-sub, codex-sub, opencode-free,
  claude-api` and all cells of a row are ungated
- **THEN** the selector SHALL return the `claude-sub` cell, and SHALL reach `claude-api` only
  after the three preceding cells are gated or absent

#### Scenario: Row exhausted
- **WHEN** every cell in the requested row is gated or ineligible
- **THEN** `NoExecutionTarget` SHALL be raised listing each cell with its gate class and
  retry time, and no spawn SHALL be attempted

### Requirement: Roles resolve to a tier, with optional preference and independence
`roles.<role>` SHALL be `{tier, prefer?, independent?}`. `dispatch` SHALL resolve a spawn's
tier as: the task's explicit tier override, else `roles.<role>.tier` for judgment roles, else
`purposes[task.purpose]`, else the task's `complexity` row, else `default_tier`; the
selector performs target/model resolution. `independent: true` SHALL pass the implementing
spawn's harness as `exclude_harness`. No role entry SHALL name a harness or model literally.

#### Scenario: Review degrades instead of failing when its preferred harness is gated
- **WHEN** `roles.review: {tier: t1-deep, independent: true}`, the implementer ran on
  `codex-sub`, and every claude cell in `t1-deep` is gated
- **THEN** review SHALL run on the next ungated `t1-deep` cell (a `codex-sub` cell if that is
  all that remains) and the run record SHALL name the serving target

#### Scenario: Literal CLI/model in a role is rejected
- **WHEN** routing declares `roles.review: {agent_cli: claude, agent_model: opus}`
- **THEN** loading SHALL fail with `OperatorConfigError` naming `worktrail-routing --migrate`

### Requirement: Capacity gates key on target and model

`agent-capacity.json` entries SHALL be keyed `<target>:<model>` (or bare `<target>` for a
target-wide gate). `spawn_agent` SHALL record outcomes under the served cell's key; the
selector SHALL consult the cell key and its bare-target key.

Exactly one resolution SHALL decide whether a cell is capacity-gated, and every reader SHALL
resolve through it: `spawn_agent`'s in-spawn selection, the drain's candidate selection, the
front-door dispatch's cell resolution, `worktrail-agent-capacity check-agent`, and the
dashboard's capacity snapshot. A query for a cell (`<target>:<model>`) SHALL be gated by an
active entry under that exact key or by an active bare `<target>` entry, and a bare-provider
entry SHALL win over a per-model entry that contradicts it. A query for a bare target or
harness SHALL be gated by an active bare entry, or by every entry under a `<query>:*` key being
active. An entry SHALL gate only while its `retry_after`/`reset_at` window is absent or still
in the future; an expired window SHALL NOT gate.

An account-level block SHALL be recorded under the bare target key, because it applies to every
model that target could serve, including models the reader does not know about. No reader SHALL
require a model-qualified entry to honour a bare one, and a gate recorded by the drain SHALL
therefore be visible to the spawn path, exactly as a gate recorded by a spawn is visible to the
drain.

The exhausted-row diagnostic (`NoExecutionTarget`'s attempted-cell list) SHALL name each
attempted cell by its gate key verbatim, so the string an operator is handed is the key
`worktrail-agent-capacity status` prints and `worktrail-agent-capacity clear` accepts. The
cell's harness MAY be named alongside that key.

#### Scenario: Subscription gate leaves the API lane open

- **WHEN** `claude-sub` carries an active `billing` gate and `claude-api:opus` is ungated
  and opted in
- **THEN** a `t1-deep` selection SHALL return `claude-api:opus` (after any earlier ungated
  targets)

#### Scenario: A drain-recorded target-wide gate is visible to the spawn path

- **WHEN** the cache holds a bare `claude-deepseek` entry whose `billing` gate is still active,
  and the resolved row's `claude-deepseek` cell declares model `deepseek-flash[1m]`
- **THEN** `spawn_agent`'s in-spawn selection SHALL skip that cell rather than serve it,
  `worktrail-agent-capacity check-agent` SHALL report the resolved target gated, and the
  dashboard's capacity snapshot SHALL report the cell gated -- all without any entry under the
  model-qualified key

#### Scenario: An expired target-wide gate gates nothing

- **WHEN** the bare `claude-deepseek` entry's window has already passed
- **THEN** a `claude-deepseek:deepseek-flash[1m]` query SHALL NOT be gated and that cell SHALL be
  selectable again, with no cache mutation required

#### Scenario: A per-model entry does not weaken a target-wide gate

- **WHEN** the bare `claude-sub` entry is gated and `claude-sub:opus` itself is recorded
  `available`
- **THEN** a query for `claude-sub:opus` SHALL still be gated, because a provider-wide gate is
  never weaker evidence than a per-model entry

#### Scenario: A bare query is gated only when every model of the target is

- **WHEN** a bare `claude-sub` query is made while `claude-sub:opus` is gated and
  `claude-sub:sonnet` is not
- **THEN** `claude-sub` SHALL NOT be reported gated, so the still-available model keeps the
  target selectable

#### Scenario: The exhausted-row diagnostic names the gate key

- **WHEN** every cell in the requested tier row is gated and `NoExecutionTarget` is raised
- **THEN** each attempted cell SHALL be named by its `<target>:<model>` gate key verbatim,
  alongside its gate class and retry time

### Requirement: A worker environment can be supplied from a declared profile without storing values

The routing table SHALL accept an `env_profiles` section mapping a profile name to
`{from, keys, expect?}`, where `from` names a JSON file containing an `env` object, `keys` lists
the key names to copy from that object, and `expect` optionally maps a key name to the literal
value it must equal. A profile SHALL NOT store any value.

A target MAY name a profile through `auth.profile`. Before launching a worker the system SHALL
resolve that profile, assert every `expect` entry against the file, and copy every `keys` entry
into the child environment — before the harness/pool branch, so the claude `subscription` lane's
key removal still applies. It SHALL do so using the same resolved routing table the cell was
selected from, without re-reading policy.

When the resolved table cannot supply the named profile, the system SHALL distinguish two cases.
A populated `env_profiles` table that does not declare the profile is an operator-config error
naming the target, the profile and the routing file. A resolved table that carries no
`env_profiles` at all is a resolver/caller fault: the error SHALL name the target, the profile
and the resolved routing source, SHALL attribute the failure to the resolved table, and SHALL NOT
instruct the operator to add an entry to the routing file; when the raw policy declares the named
profile, the error SHALL say so.

The system SHALL raise an operator-config error naming the target, the profile, the resolved file
and the offending key when the file is missing or unreadable, is not valid JSON, has no `env`
object, omits a declared key, has an empty or non-string value for one, or fails an `expect`
assertion. An error message SHALL NOT contain a value for any key not named in `expect`. The
system SHALL NOT launch a process when any of these fail.

`keys` and `expect` SHALL be independent: a key MAY be asserted without being copied, and copied
without being asserted.

#### Scenario: Declared keys reach the child environment

- **WHEN** a target declares `auth.profile: deepseek` and `env_profiles.deepseek` names a
  readable file with `keys: [ANTHROPIC_BASE_URL, ANTHROPIC_AUTH_TOKEN]`
- **THEN** the child environment SHALL contain both keys with the file's values

#### Scenario: A configured endpoint is proven before launch

- **WHEN** `env_profiles.deepseek.expect` asserts `ANTHROPIC_BASE_URL` and the file holds a
  different value
- **THEN** the spawn SHALL raise an operator-config error naming the key, the expected value and
  the file's actual value, before any process is launched

#### Scenario: A missing key fails loud

- **WHEN** a profile lists a key under `keys` that the source file's `env` object does not
  contain
- **THEN** the spawn SHALL raise an operator-config error naming the key and the file, before
  any process is launched

#### Scenario: A secret outside expect is never printed

- **WHEN** a profile's source file holds a credential under a key not named in `expect` and any
  resolution failure occurs
- **THEN** the raised error's message SHALL NOT contain that value

#### Scenario: An undeclared profile is a configuration error

- **WHEN** a target names an `auth.profile` that a populated resolved `env_profiles` table does
  not declare
- **THEN** the spawn SHALL raise an operator-config error naming the target, the profile and the
  routing file, before any process is launched

#### Scenario: A resolved table that lost env_profiles is a resolver/caller fault

- **WHEN** the routing file declares `env_profiles` including the profile a target's
  `auth.profile` names, but the resolved routing table carries no `env_profiles` at all
- **THEN** the spawn SHALL raise an operator-config error naming the target, the profile and the
  resolved routing source, SHALL attribute the failure to the resolved table (a resolver/caller
  fault) rather than to the routing file, SHALL say that the routing file does declare the
  profile, and SHALL NOT instruct the operator to add an entry to the routing file — all before
  any process is launched

#### Scenario: A profile applies to any harness

- **WHEN** a non-claude target declares `auth.profile`
- **THEN** its declared keys SHALL still be copied into the child environment

### Requirement: Profile and named-variable auth are mutually exclusive alternatives
A target's `auth` SHALL supply at most one of `env` and `profile`. When both are declared the
spawn SHALL raise an operator-config error before launching, and the routing table validator
SHALL surface a warning naming the target. `auth.codex_home` SHALL NOT be treated as conflicting
with `profile`, since it selects a home rather than supplying credentials.

A profile MAY be declared on a `subscription` target, but the routing validator SHALL warn,
because that lane removes `ANTHROPIC_API_KEY` after injection and a profile-supplied key
therefore does not survive.

#### Scenario: Both lanes declared fails loud
- **WHEN** a target declares `auth: {env: ANTHROPIC_API_KEY, profile: deepseek}`
- **THEN** the spawn SHALL raise an operator-config error naming the target, before any process
  is launched

#### Scenario: A profile supplies the api lane's credentials alone
- **WHEN** a claude `api` target declares `auth.profile` and no `auth.env`
- **THEN** the spawn SHALL NOT require `auth.env`, and SHALL NOT raise for its absence

#### Scenario: The profile is applied before the subscription key removal
- **WHEN** a `subscription` target's profile declares `ANTHROPIC_API_KEY`
- **THEN** the child environment SHALL NOT contain `ANTHROPIC_API_KEY`

### Requirement: Harness auth follows the target's pool
`build_cmd` and the child environment SHALL be built from the selected cell. For a claude
`subscription` target the spawn SHALL omit `--bare` and SHALL remove every provider-redirect
variable from the child environment (see "A subscription spawn cannot be redirected by an ambient
provider environment"); for a claude `api` target the spawn SHALL pass `--bare` unless the target
declares `auth.profile`, and SHALL obtain its credentials from either the env var named in
`auth.env` or the profile named in `auth.profile`, failing loud before launch when neither is
configured or the declared one cannot be resolved. For a codex `subscription` target the spawn
SHALL use the existing isolated Worktrail Codex home with the operator's ChatGPT login inherited,
unchanged. For a codex `api` target the spawn SHALL set `CODEX_HOME` to the path named in
`auth.codex_home` — a home the operator provisioned with `codex login --with-api-key` — and SHALL
NOT inherit the parent home's ChatGPT auth; it SHALL fail loud before launch when
`auth.codex_home` is undeclared or the declared home contains no `auth.json` (checked by
existence only, never read). This supersedes the interim rule that a codex `api` target be
rejected by the loader: its per-spawn auth selection is live-verified (routing-target-selector
task 3.6, codex-cli 0.149.1 — env-var and config-field selectors are inert; `CODEX_HOME`
isolation is the working mechanism).

#### Scenario: Ambient API key never bills a subscription lane
- **WHEN** the operator's shell exports `ANTHROPIC_API_KEY` and a `claude-sub` cell is spawned
- **THEN** the child process environment SHALL NOT contain `ANTHROPIC_API_KEY` and the
  command SHALL NOT include `--bare`

#### Scenario: API lane forces key auth
- **WHEN** a `claude-api` cell with `auth: {env: ANTHROPIC_API_KEY}` and no `auth.profile` is
  spawned
- **THEN** the command SHALL include `--bare` and the child environment SHALL carry the key

#### Scenario: API lane resolves a profile
- **WHEN** a `claude-api` cell with `auth: {profile: deepseek}` is spawned and that profile
  resolves
- **THEN** the command SHALL omit `--bare` and the child environment SHALL carry the profile's
  declared keys

#### Scenario: API lane with neither auth source fails loud
- **WHEN** a claude cell with `pool: api` declares neither `auth.env` nor `auth.profile`
- **THEN** the spawn SHALL raise an operator-config error naming the target and both remedies,
  before any process is launched

#### Scenario: Codex api lane spawns in its declared home without ChatGPT auth
- **WHEN** a codex cell with `pool: api` and `auth: {codex_home: <provisioned path>}` is
  spawned and that path contains an `auth.json`
- **THEN** the child environment SHALL carry `CODEX_HOME=<that path>` and the parent
  home's ChatGPT credentials SHALL NOT be copied into it

#### Scenario: Codex api lane without a declared home fails loud
- **WHEN** a codex cell with `pool: api` and no `auth.codex_home` is spawned
- **THEN** the spawn SHALL raise an operator-config error naming the target and the
  `auth: {codex_home: <path>}` remedy, before any process is launched

#### Scenario: Codex api lane with an unprovisioned home fails loud
- **WHEN** a codex cell with `pool: api` declares an `auth.codex_home` whose directory has
  no `auth.json`
- **THEN** the spawn SHALL raise an operator-config error naming the path and the
  `codex login --with-api-key` provisioning step, before any process is launched

#### Scenario: Codex subscription lane is unchanged
- **WHEN** a codex cell with `pool: subscription` is spawned
- **THEN** the child environment SHALL be prepared exactly as before this change (isolated
  Worktrail home, ChatGPT auth inherited)

### Requirement: A subscription spawn cannot be redirected by an ambient provider environment
A claude `subscription` spawn SHALL remove every variable that would redirect it to a different
endpoint, token, or model than the subscription it was selected for — not only
`ANTHROPIC_API_KEY` but also `ANTHROPIC_BASE_URL`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_MODEL`, the
`ANTHROPIC_DEFAULT_*_MODEL` aliases, and `CLAUDE_CODE_SUBAGENT_MODEL`. Worker environments are
built from the spawning process's environment, and an interactive session carries exactly those,
so a subscription cell selected because its tier wanted Anthropic would otherwise be silently
sent to the ambient endpoint instead.

#### Scenario: An ambient base URL does not hijack a subscription cell
- **WHEN** the spawning process's environment carries `ANTHROPIC_BASE_URL` and
  `ANTHROPIC_AUTH_TOKEN` and a `claude-sub` cell is spawned
- **THEN** the child environment SHALL contain neither

#### Scenario: Unrelated ambient variables survive
- **WHEN** a `claude-sub` cell is spawned from an environment carrying unrelated variables
- **THEN** those SHALL be preserved in the child environment

### Requirement: A profile-backed api cell keeps its settings-injected hooks
A claude `api` cell that declares `auth.profile` SHALL NOT be spawned with `--bare`. `--bare`
skips hooks defined in settings, which includes the worktree guard injected through `--settings`;
the profile's injected credentials already pin the endpoint and auth, which is the only purpose
`--bare` serves for that lane. An `api` cell without a profile SHALL continue to receive
`--bare`.

#### Scenario: Profile-backed api cell omits bare and keeps its guard
- **WHEN** a claude `api` cell declaring `auth.profile` is spawned
- **THEN** the command SHALL NOT include `--bare` and SHALL include `--settings`

#### Scenario: An api cell without a profile is unchanged
- **WHEN** a claude `api` cell declares `auth.env` and no `auth.profile` is spawned
- **THEN** the command SHALL include `--bare`

### Requirement: An explicit override reproduces the target's whole definition
The temporary routing file an explicit model or effort override writes SHALL reproduce the
selected target's complete definition — its harness, pool, `api_opt_in`, `auth`, and the one
`env_profiles` entry that `auth.profile` names. That file replaces the operator's routing file
for the duration of the override, so anything it omits is absent downstream rather than
inherited; omitting any of these makes an api- or profile-backed target unreachable through the
explicit-override path. The file SHALL continue to contain only paths and key names, never a
value.

#### Scenario: An api target survives an explicit model override
- **WHEN** an explicit override names a target whose `pool` is `api` with `api_opt_in` and
  `auth.profile` declared
- **THEN** the temporary routing file SHALL carry `api_opt_in`, the `auth` mapping, and the
  referenced `env_profiles` entry, and the target SHALL remain selectable

#### Scenario: A bracketed model id round-trips
- **WHEN** an explicit override requests a model id containing YAML flow indicators
- **THEN** the temporary routing file SHALL parse back to that exact model id

### Requirement: A retired model gates its own cell with a distinct failure class
`agent_capacity` SHALL support the failure class `model_unavailable` with a 24-hour default
cooldown. `worktrail-routing --check` SHALL compare every opencode cell's model id against
`opencode models` and record `model_unavailable` for ids that are absent. `spawn_agent` SHALL
record `model_unavailable` (not `transport`) when an opencode `UnknownError` recurs on the
same cell across every retry and the id is confirmed absent from the listing. The dashboard
capacity line SHALL name each gated cell and its class.

#### Scenario: --check gates a retired opencode model
- **WHEN** a tier cell names `opencode/x-preview-f-free` and `opencode models` does not list it
- **THEN** `--check` SHALL exit non-zero naming the cell and SHALL write a `model_unavailable`
  gate for `<target>:opencode/x-preview-f-free`

#### Scenario: A plain outage stays transport
- **WHEN** an opencode spawn fails with `UnknownError` and the model id IS present in
  `opencode models`
- **THEN** the recorded failure class SHALL remain `transport`

### Requirement: An infra failure hops within the same spawn after retries are exhausted
When the primary cell exhausts its configured retries on an infra failure, `spawn_agent` SHALL
re-select from the same tier row with the failed cell excluded and continue in the same call,
returning the report-back of the first cell that completes. Only when every cell fails SHALL
it return the last raw output.

#### Scenario: Dead primary cell, healthy second cell
- **WHEN** `t2-build`'s first cell fails all retries and its second cell is ungated
- **THEN** the same `spawn_agent` call SHALL produce a report-back from the second cell

### Requirement: Legacy routing keys fail loud and migrate deterministically
Loading a routing table containing `agents`, `fallback`, `drain.agent`,
`drain.fallback_agents`, `purpose_tiers`, tiers keyed by harness name, or a role with
`agent_cli`/`agent_model` SHALL raise `OperatorConfigError` naming the offending key and
`worktrail-routing --migrate`. `--migrate` SHALL rewrite the file into the targets/rows form
per design D9, write a `.bak` beside it, and produce a file that loads without warnings.

#### Scenario: Migrating the shipped 2026-08 file
- **WHEN** `--migrate` runs on a file with `fallback: [claude, codex, opencode]`, nested
  harness-keyed tiers, `agents.*.default_model` matching `t2-build`, and
  `roles.review: {agent_cli: claude, agent_model: opus}`
- **THEN** the output SHALL declare targets `claude-sub`, `codex-sub`, `opencode-free` in that
  order, tier cells re-keyed by those names, `default_tier: t2-build`, `roles.review: {tier:
  t1-deep, prefer: claude-sub, independent: true}`, and `drain: {max_workers: <previous>}`

### Requirement: The starter template is valid and names no opencode model
`worktrail-routing --init` SHALL write a file that loads without warnings, declares only
`subscription` targets, fills every shipped tier row for those targets, includes a commented
`free`-pool example, and instructs the operator to run `--check`.

#### Scenario: Template contains no unverifiable model id
- **WHEN** the starter template is loaded in a test
- **THEN** it SHALL pass validation and SHALL contain no `opencode/` model id outside a comment

### Requirement: Effort values are validated per harness
`--check` (and the loader, as a warning) SHALL flag an effort value outside the harness's
vocabulary: claude `low|medium|high|xhigh|max`; codex `minimal|low|medium|high|xhigh`;
opencode any value, reported as ignored by the harness.

#### Scenario: Codex-only value on a claude cell
- **WHEN** a `claude-sub` cell declares `effort: minimal`
- **THEN** `--check` SHALL report the cell and the accepted claude vocabulary

### Requirement: An auth failure gates its cell without retry
`agent_capacity.classify_failure` SHALL classify HTTP 401 / "unauthorized", a
consumed refresh token ("refresh token"), and "log out and sign in" wording as
`auth`. `agent_capacity` SHALL give `auth` a 24-hour default cooldown. When a
spawn's infra failure classifies as `auth`, `spawn_agent` SHALL record the gate
for the served cell on that first attempt and hop to the next cell in the row
with no further retry or backoff on the failed cell, logging the gated cell and
how to clear it. An `auth` gate is cooldown-derived and therefore probe-eligible:
once the probe cadence has elapsed, one spawn per cadence SHALL be let through,
and a successful one lifts the gate without operator action.

#### Scenario: Consumed refresh token gates on the first attempt
- **WHEN** a spawned codex cell exits non-zero with "Your access token could not
  be refreshed because your refresh token was already used"
- **THEN** the cell is recorded `unavailable` with `failure_class: auth` after one
  attempt, no backoff sleep occurs, and the spawn continues on the next cell in
  the row

#### Scenario: Auth gate outlives the run
- **WHEN** an `auth` gate is recorded
- **THEN** its `retry_after` is 24 hours out, so later spawns and later runs skip
  the cell until it expires, an operator clears it, or a probe-through spawn
  after the cadence succeeds

#### Scenario: Re-authenticated operator forgets to clear
- **WHEN** an `auth` gate was recorded more than one probe cadence ago and the operator has since
  re-authenticated
- **THEN** the next `check()` lets one spawn through, that spawn succeeds, and the gate is
  removed without `worktrail-agent-capacity clear`

### Requirement: Status distinguishes an expired gate from an active one
`worktrail-agent-capacity status` SHALL label each gated entry (status `unavailable`, `gated`,
or `blocked`) according to its `retry_after`/`reset_at` window: `(active)` when the window has
not passed, `(expired)` when it has. An entry with a gated status and no timestamp SHALL print
with neither label. `status` SHALL remain read-only and SHALL NOT remove any entry.

#### Scenario: Expired drain gate is labelled
- **WHEN** the cache holds a bare `claude` entry with status `unavailable`, `source: drain`, and
  a `retry_after` earlier than now
- **THEN** `status` prints that key with `(expired)` and its `failure:` and `retry:` lines, and
  the cache file is unchanged afterwards

#### Scenario: Active gate keeps its label
- **WHEN** the cache holds `claude-sub:opus` with status `unavailable` and a `retry_after` later
  than now
- **THEN** `status` prints that key with `(active)` and no `(expired)` marker

### Requirement: Clear supports an expired-only scope
`worktrail-agent-capacity clear --expired --reason TEXT` SHALL remove exactly the entries that
`status` would label `(expired)`, append one `clear` audit entry with scope `expired` listing
the removed keys, and leave every other entry (active gates, timestamp-less gates, `available`
entries) untouched. It SHALL require a non-empty `--reason` like every other clear, SHALL exit 0
without modifying the file when nothing is expired, and SHALL reject being combined with a
provider key or `--all`.

#### Scenario: Only expired entries are removed
- **WHEN** the cache holds an expired bare `claude` gate, an active `codex-sub:gpt-5` gate, and
  an `available` `claude-api:opus` entry, and the operator runs
  `clear --expired --reason "stale drain gates"`
- **THEN** only `claude` is removed and printed as `cleared: claude`, the other two entries
  remain, and the audit log gains one entry with scope `expired` and providers `["claude"]`

#### Scenario: Nothing expired
- **WHEN** every gated entry's window is still in the future and the operator runs
  `clear --expired --reason "sweep"`
- **THEN** the command exits 0, prints nothing cleared, and the cache file is byte-identical

### Requirement: A cooldown-derived gate is re-probed on a cadence
A capacity gate whose retry window was produced by a per-failure-class cooldown rather than by
the provider's own reset notice (`reset_source` absent or `cooldown`) SHALL NOT be treated as
authoritative for the whole window. Once the probe cadence (`GO_AGENT_GATE_PROBE_INTERVAL`
seconds, default 900) has elapsed since the entry's `checked_at` or its most recent `probe_at`,
`check()` SHALL let exactly one caller through per cadence, stamping `probe_at` on the entry
under the cache write lock so every other caller inside the cadence still sees the gate. The
caller's own spawn is the probe: a successful spawn records `available` (removing the gate) and
a failed spawn re-records the gate with a fresh window. An entry with `reset_source: provider`
SHALL never be probed, and an entry whose `failure_class` is `model_unavailable` SHALL never be
probed. `record()` SHALL accept `reset_source` and default it to `cooldown`; `spawn_agent`'s
exhausted-budget record SHALL pass `provider` together with the parsed timestamp when
`parse_explicit_reset()` finds one in the captured output, and SHALL otherwise fall back to the
class cooldown. `status` SHALL print a `probed:` line for an entry carrying `probe_at`.

#### Scenario: Stale billing gate lets one caller through after the cadence
- **WHEN** `claude-sub:fable` is gated `billing` with `retry_after` one hour out, `checked_at`
  twenty minutes ago, no `probe_at`, and the cadence is the default
- **THEN** the first `check("claude-sub", "fable")` returns without raising and the entry now
  carries `probe_at` equal to that call's `now`, and a second `check()` one second later raises
  `ProviderUnavailable`

#### Scenario: Gate inside the cadence is still authoritative
- **WHEN** the same gated entry has `checked_at` five minutes ago and no `probe_at`
- **THEN** `check()` raises `ProviderUnavailable` and the cache file is unchanged

#### Scenario: Provider-reported reset is never probed
- **WHEN** an entry is gated `billing` with `reset_source: provider`, `retry_after` three days out,
  and `checked_at` a day ago
- **THEN** `check()` raises `ProviderUnavailable` and no `probe_at` is written

#### Scenario: Retired model is never probed
- **WHEN** an entry is gated `model_unavailable` with `checked_at` two hours ago
- **THEN** `check()` raises `ProviderUnavailable` and no `probe_at` is written

#### Scenario: Successful probe clears the gate
- **WHEN** a probe-through spawn on the gated cell exits successfully
- **THEN** `spawn_agent` records `available` for that cell and a subsequent `check()` returns
  without raising

#### Scenario: Codex usage cap records the stated reset
- **WHEN** a codex cell exhausts its attempt budget with output containing "You've hit your usage
  limit ... try again at Aug 8th, 2026 2:17 AM."
- **THEN** the recorded gate's `retry_after` is that timestamp (UTC) and `reset_source` is
  `provider`

### Requirement: A zero-API-call result is classified as an infra failure, not a completed run

A spawn whose worker process exits 0 but made no API call SHALL NOT be recorded as a completed
run. The spawn's infra-failure classification (`is_infra_failure`) SHALL recognize the claude
runtime's zero-API-call result shape — a stream-json `result` event carrying `duration_api_ms:
0`, `num_turns: 0`, and zero `input_tokens`/`output_tokens`/cache-token counts — and classify
it as an infra failure, so the existing retry-then-hop path (bounded retries on the same cell,
then same-row re-selection) runs before any capacity gate is recorded or any success is
reported. The detection SHALL read the parsed usage dict, so `_parse_stream_json` SHALL retain
`duration_api_ms` from the result event.

The detection SHALL be specific to the zero-work shape. A legitimate completed result — any
result event with `num_turns >= 1` or a non-zero token count — SHALL remain a healthy spawn and
SHALL be recorded `available` exactly as before this change. A stream that carries no claude
`result` event (the opencode-synthesized usage dict, codex's plain last-message text) SHALL
never satisfy the detection.

#### Scenario: A zero-API-call result is an infra failure
- **WHEN** a spawned claude cell exits 0 with a result event reading `duration_api_ms: 0`,
  `num_turns: 0`, and every token count 0
- **THEN** `is_infra_failure(0, <that output>)` SHALL return True, the cell SHALL be retried
  rather than recorded `available`, and no successful run SHALL be returned for it

#### Scenario: A real completed turn is still classified healthy
- **WHEN** a result event carries a non-zero `duration_api_ms` and/or `num_turns >= 1` with
  non-zero `input_tokens`
- **THEN** the existing classification SHALL return False, the cell SHALL be recorded
  `available`, and the run SHALL be returned exactly as before this change

#### Scenario: An empty-but-real turn is not a no-op
- **WHEN** a completed turn reports no output text but carries `num_turns >= 1`
- **THEN** the spawn SHALL NOT be classified as a zero-API-call failure

### Requirement: An exhausted zero-API-call spawn gates its cell with a short-cooldown class

When the zero-API-call shape recurs through the served cell's whole retry budget, `spawn_agent`
SHALL record that cell's gate with the short-cooldown `startup` failure class (default cooldown
60 seconds) and continue through the existing same-row hop to the next ungated cell. It SHALL
NOT record `auth` (24-hour cooldown, gates without retry) or `model_unavailable` (24-hour
cooldown, never probed) for this shape, and no failure class's existing cooldown value SHALL
change. When the row has no servable cell left, the spawn SHALL return an exhausted result
carrying `failure_class: startup` rather than a successful-but-empty run.

#### Scenario: No-op exhausts its budget and the row hops to a healthy cell
- **WHEN** the first cell of a row returns the zero-API-call shape for every attempt, its
  retry budget is exhausted, and a later cell in the row is ungated
- **THEN** the same spawn call SHALL complete on the later cell, and the first cell's recorded
  gate SHALL carry `failure_class: startup`

#### Scenario: No alternate cell is left
- **WHEN** every attempt on the row's only servable cell returned the zero-API-call shape
- **THEN** `spawn_agent` SHALL return an exhausted result with `failure_class: startup`, which
  a caller that fails closed on exhausted results treats as a failed spawn rather than a
  completed empty run

#### Scenario: A coerced auth classification never gates the cell
- **WHEN** a zero-API-call result is exhausted through its retry budget and the exhausted
  classification runs
- **THEN** the recorded gate's class SHALL be `startup` (60-second default), never `auth` or
  `model_unavailable`, so the cell remains reachable within the same run instead of being
  sidelined for a day

### Requirement: The routing check proves spawn readiness against the resolved table

`worktrail-routing --check` SHALL decide a tier cell's status from the **resolved** routing
table — `load_policy()` followed by `resolve_routing()`, the same values `select_cell()` and
`build_child_env()` consume — and never from the raw routing mapping the loader validated. For
every tier cell whose target is declared, the check SHALL construct that cell's spawn: its
launcher command and its child environment, built with the resolved table's `env_profiles`,
against the checking process's own environment.

A cell whose construction raises, or whose target the selector can never serve, SHALL be
reported `FAIL` with the raised message and SHALL flip the exit code. As with an unresolvable
env profile, a readiness `FAIL` SHALL NOT be recorded as an `agent_capacity` gate.

The check SHALL report `FAIL` for an `api`-pool target that declares a tier cell but no
`api_opt_in`, naming the target and the remedy, because the selector drops such a target from
every row it appears in.

A claude `api` cell whose `auth.env` variable is unset in the checking process's environment
SHALL be reported as unready rather than `ok`: no spawn built from that environment can
authenticate it.

#### Scenario: A key the resolver drops is caught even though the file declares it

- **WHEN** the routing file declares `env_profiles` and a target whose `auth.profile` names one
  of them, but the resolved table omits `env_profiles`
- **THEN** `--check` SHALL report that target's cells `FAIL`, naming the target and the profile,
  and SHALL exit non-zero

#### Scenario: An api target that can never be selected is caught

- **WHEN** a target declares `pool: api` and a tier cell but no `api_opt_in`
- **THEN** `--check` SHALL report that cell `FAIL`, naming the target, the `api_opt_in` key and
  the routing file, and SHALL exit non-zero

#### Scenario: A named auth variable that is unset is caught

- **WHEN** a claude `api` cell declares `auth.env: ANTHROPIC_API_KEY` and the checking
  process's environment does not set it
- **THEN** `--check` SHALL report that cell unready and exit non-zero, rather than `ok`

#### Scenario: A codex api home check matches the spawn path

- **WHEN** a codex `api` cell declares an `auth.codex_home` whose directory has no `auth.json`
- **THEN** `--check` SHALL report that cell `FAIL` with the same remedy the spawn path raises,
  and SHALL exit non-zero

#### Scenario: A readiness failure is not a capacity gate

- **WHEN** a cell fails the readiness construction
- **THEN** the capacity cache SHALL contain no entry for that cell, so no later spawn can be
  silently routed past it

#### Scenario: A servable table still reports ok

- **WHEN** every declared cell's spawn construction succeeds
- **THEN** `--check` SHALL report every cell `ok` and exit zero

### Requirement: Explicit overrides retain the routing table that selected their target
When a live orchestrator spawn applies an explicit model or reasoning-effort
override, the temporary routing configuration SHALL reuse the target definition
from the resolved routing table that selected that target. The override path
SHALL NOT independently resolve repository or machine-wide policy to find that
target. If the selected-routing table does not declare the requested target,
the system SHALL fail with an actionable configuration error and SHALL NOT
launch a worker.

#### Scenario: Repository-local target survives an explicit model override
- **WHEN** a repository-local routing table selects a target that is absent from
  the machine-wide routing table and a live spawn has an explicit model override
- **THEN** the worker receives a temporary one-cell routing configuration for
  the repository-local target and dispatch proceeds using the override

#### Scenario: Explicit effort override preserves the selected target
- **WHEN** a live spawn selects a target and applies an explicit reasoning-effort
  override
- **THEN** the temporary one-cell routing configuration retains that target's
  declared harness and pool while applying the requested effort

#### Scenario: Selected-routing target is absent
- **WHEN** an explicit override requests a target absent from the routing table
  supplied for that spawn
- **THEN** dispatch fails before launching a worker and the configuration error
  identifies the missing target

