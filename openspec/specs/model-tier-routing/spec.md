# model-tier-routing Specification

## Purpose
TBD - created by archiving change model-tier-routing. Update Purpose after archive.
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

#### Scenario: Subscription gate leaves the API lane open
- **WHEN** `claude-sub` carries an active `billing` gate and `claude-api:opus` is ungated
  and opted in
- **THEN** a `t1-deep` selection SHALL return `claude-api:opus` (after any earlier ungated
  targets)

### Requirement: Harness auth follows the target's pool
`build_cmd` and the child environment SHALL be built from the selected cell. For a claude
`subscription` target the spawn SHALL omit `--bare` and SHALL remove `ANTHROPIC_API_KEY` from
the child environment; for a claude `api` target the spawn SHALL pass `--bare` and copy the
env var named in `auth.env` into the child environment, failing loud before launch when it
is unset. A codex `api` target SHALL be rejected by the loader until its per-spawn auth
selection is live-verified (design D6).

#### Scenario: Ambient API key never bills a subscription lane
- **WHEN** the operator's shell exports `ANTHROPIC_API_KEY` and a `claude-sub` cell is spawned
- **THEN** the child process environment SHALL NOT contain `ANTHROPIC_API_KEY` and the
  command SHALL NOT include `--bare`

#### Scenario: API lane forces key auth
- **WHEN** a `claude-api` cell with `auth: {env: ANTHROPIC_API_KEY}` is spawned
- **THEN** the command SHALL include `--bare` and the child environment SHALL carry the key

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

