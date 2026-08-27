## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: Review role defaults to claude:opus
`routing.roles` SHALL support a `review` entry naming a tier (`{tier, prefer?, independent?}`);
when absent, review SHALL resolve as `{tier: t1-deep, independent: true}` where a `t1-deep`
row exists, else `default_tier`. Judgment roles SHALL still ignore the task's own purpose and
complexity rows.

#### Scenario: Review role uses the configured override regardless of task complexity
- **WHEN** `roles.review: {tier: t1-deep}` is configured and a task with `complexity: trivial`
  is dispatched for the `review` role
- **THEN** the review spawn SHALL select from the `t1-deep` row, never the trivial row

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

## REMOVED Requirements

### Requirement: Per-agent default models resolve from routing only
**Reason**: `agents.<harness>.default_model` duplicated the `t2-build` row and was the source
of tier-losing fallbacks; `default_tier` + `select_cell` replace it.
**Migration**: `worktrail-routing --migrate` drops `agents` and writes `default_tier`;
`spawnlib.default_model_for_agent()` is deleted.
