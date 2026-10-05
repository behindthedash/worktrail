## MODIFIED Requirements

### Requirement: A single selector walks a tier row across targets in preference order
`runtime.selection.select_cell(routing, tier, prefer=None, exclude_harness=None, capacity,
now)` SHALL be the only function that turns a tier into a spawnable `(target, harness, model,
effort)` cell. `prefer` SHALL be resolved through the shared preference resolution: `None`
passes through, an exact `routing.targets` key passes through, and any other value SHALL raise
`UndeclaredPrefer`, naming the offending value and the declared target names -- a runtime
preference is never silently dropped. The selector SHALL: move the resolved target to the
front when that target has a cell in the row; leave a declared target with no cell in the row
as a no-op (the row's own order decides -- intentional degrade behavior, never a promotion and
never a failure); drop ineligible `api` targets and targets with no cell; order targets on a
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

#### Scenario: A declared target with no cell in the row stays a soft no-op
- **WHEN** `prefer` is `codex-sub`, `codex-sub` is declared, and `t1-deep` declares a cell only
  for `claude-sub`
- **THEN** `select_cell` SHALL raise nothing and attempt only the row's cells, exactly as
  `test_prefer_naming_target_without_cell_in_row_is_a_no_op` pins

#### Scenario: An undeclared preference fails loud
- **WHEN** `prefer` names no declared `routing.targets` entry -- a typo, a renamed or dropped
  target, or a bare harness name such as `claude` where the targets are `claude-sub` and
  `codex-sub`
- **THEN** `select_cell` SHALL raise `UndeclaredPrefer` naming the offending value and the
  declared target names, and no cell SHALL be served

#### Scenario: Row exhausted
- **WHEN** every cell in the requested row is gated or ineligible
- **THEN** `NoExecutionTarget` SHALL be raised listing each cell with its gate class and
  retry time, and no spawn SHALL be attempted

## ADDED Requirements

### Requirement: A harness-level preference resolves to a declared target before selection
`runtime.selection.first_target_for_harness(routing, harness)` SHALL return the first declared
`routing.targets` entry in file order whose `harness` matches, or `None` when the routing
declares no target for that harness. `spawnlib.prefer_target_for_harness(harness)` SHALL
resolve routing fresh (the operator's current routing file, the same per-call contract as the
model-default lookups) and return that same mapping, so a caller holding a bare harness name
(`verify.py`'s group worker, `queue_triage.py`'s evaluator, `retro.py`'s curation spawn) can
express a best-effort harness hint: the resolved target name is passed as `prefer`, and `None`
SHALL leave the row's own order to decide. A bare harness name SHALL NOT be passed as
`prefer` itself -- `prefer` names a declared target.

#### Scenario: A declared harness resolves to its first target
- **WHEN** targets are declared as `claude-sub` then `claude-api`, both harness `claude`
- **THEN** `prefer_target_for_harness("claude")` SHALL return `claude-sub`

#### Scenario: An undeclared harness resolves to no preference
- **WHEN** the routing declares no target with harness `claude`
- **THEN** `prefer_target_for_harness("claude")` SHALL return `None`, and a spawn SHALL
  proceed under the row's own order instead of raising

### Requirement: Primary-cell preflight shares the selector's preference resolution
`spawnlib.spawn_agent`'s primary-target preflight -- the step that identifies the cell the
caller derived its `extra_args` for -- SHALL resolve `prefer` through the same preference
resolution `select_cell()` uses, so the served cell and the primary decision cannot disagree
about what the preference named. `spawn_agent` SHALL raise `UndeclaredPrefer` before any
subprocess is launched when `prefer` names no declared target. A capacity gate that has
already moved selection past the requested primary SHALL still keep the caller's primary-only
`extra_args` off the served cell's argv (unchanged degrade behavior).

#### Scenario: An undeclared preference fails before any launch
- **WHEN** `spawn_agent(..., prefer=<a name no declared target matches>)` is called
- **THEN** the call SHALL raise `UndeclaredPrefer` and no subprocess SHALL be started

#### Scenario: A gated preferred cell still drops primary-only extra_args
- **WHEN** the preferred target's cell is capacity-gated before the first launch and the row
  serves the next cell
- **THEN** the caller's primary-only `extra_args` SHALL NOT be forwarded to the served cell's
  argv

#### Scenario: A row-absent preference does not break the spawn
- **WHEN** `prefer` names a declared target with no cell in the requested row
- **THEN** the spawn SHALL proceed on the row's own order without raising
