## ADDED Requirements

### Requirement: Resolve-worker prompt forbids resurrecting base deletions
The `ROLE_RESOLVE` conflict-resolution instructions built by `build_group_prompt()` SHALL
explicitly instruct the worker that a path deleted by the base branch stays deleted — the
worker SHALL NOT restore or recreate such a path in order to "preserve both sides" of the
merge, especially a path outside the group's own declared task scope.

#### Scenario: Resolve prompt names the base-deletion rule
- **WHEN** `build_group_prompt(ROLE_RESOLVE, group, ctx)` renders the conflict-resolution
  instructions for a `CONFLICTING` PR
- **THEN** the rendered prompt includes an explicit instruction that a path the base deleted
  must not be resurrected, distinct from the general "preserve the intent of both sides"
  guidance

### Requirement: Confirmed forbidden-path violation is tracked per group
When `_spawn_group_worker()` finds that a resolve or ci-fix worker's pushed commit touched a
forbidden path (per `_forbidden_paths_touched()`) despite the worker reporting `status: success`,
the system SHALL record a confirmed forbidden-path violation for that group, keyed by group name,
in addition to logging the strike failure and returning failure exactly as it does today.

#### Scenario: Resolve worker touches a forbidden path
- **WHEN** a resolve worker spawned for group `feature-1` pushes a commit that touches a path
  under `openspec/` outside `feature-1`'s own spec root and reports `status: success`
- **THEN** `_spawn_group_worker()` returns `False` exactly as today, and the group's confirmed
  forbidden-path violation is recorded for `feature-1`

### Requirement: Confirmed forbidden-path violation surfaces as its own outcome, never silently merged
When a group has a confirmed forbidden-path violation recorded, `verify_one()` SHALL NOT invoke
`_recheck_merged_before_quarantine()` for that group regardless of the PR's live merge state, and
`run_all()`'s result dict SHALL record the group under a distinct `forbidden_path_violations`
bucket rather than `merged` or `quarantined`.

#### Scenario: Forbidden-path violation followed by an external merge
- **WHEN** a resolve worker's forbidden-path-touching commit is later merged by automation
  external to this run (e.g. this repo's own "CI: Auto-merge on open" workflow), and the group
  has a confirmed forbidden-path violation recorded
- **THEN** the system does not perform the live merge recheck, does not record the group as
  `merged`, and instead records it under `forbidden_path_violations` with the touched path(s)
  named in the reason

#### Scenario: No forbidden-path violation recorded
- **WHEN** a group's verification chain fails for any other reason and no forbidden-path
  violation was recorded for it
- **THEN** behavior is unchanged from before this change: the existing self-merge/quarantine/
  live-merge-recheck logic applies exactly as today
