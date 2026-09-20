# resolve-worker-scope-discipline Specification

## Purpose
Forbids the resolve worker from resurrecting files the base branch deleted, tracks a confirmed
forbidden-path violation per group, and surfaces it as its own outcome. Prevents a conflict
resolution from silently merging back code that was intentionally removed.
## Requirements
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

### Requirement: The forbidden-path guard SHALL judge scope against the base tip
`_forbidden_paths_touched()` SHALL treat a path as touched only when it both changed between the
worker's pre-run HEAD and the group branch's post-run HEAD **and** differs from that path's
content at the base tip (`<remote>/<base>`). A path whose post-run content at the group branch is
identical to the base tip's SHALL NOT be reported as a forbidden path, regardless of whether it
appears in the pre/post diff. Both deny-list tiers — the absolute spec root and the
declared-file-exempt remainder — SHALL be applied to this narrowed set.

#### Scenario: a path merged in unchanged from base is not a violation
- **WHEN** a resolve worker merges `<remote>/<base>` into the group branch, bringing in
  `.github/workflows/gitleaks.yml` and `openspec/changes/<spec>/tasks.md` exactly as base has
  them, and edits nothing else under a denied prefix
- **THEN** `_forbidden_paths_touched()` returns no forbidden paths and the worker is not struck

#### Scenario: a path the worker actually edited is still a violation
- **WHEN** the group branch's post-run content for a denied path differs from the base tip's,
  and the path also appears in the pre/post diff
- **THEN** that path is reported as a forbidden path exactly as it is today

#### Scenario: a base change outside the pre/post diff is not attributed to the worker
- **WHEN** a denied path differs from the base tip but did not change between the worker's
  pre-run and post-run HEADs
- **THEN** it is not reported, because the pre/post diff remains the outer bound of what this
  worker could have done

### Requirement: The guard SHALL refresh the base tip and fall back rather than disarm
Before comparing against the base tip, the system SHALL run a best-effort
`git fetch <remote> <base>`. If that fetch fails, or the base-tip diff command fails, the system
SHALL fall back to the unnarrowed pre/post touched set — today's behavior — and SHALL log that
base-tip narrowing was unavailable. It SHALL NOT return an empty result on that path, so a
transient git failure can never silently disable the guard.

#### Scenario: base tip unavailable
- **WHEN** the fetch of `<remote>/<base>` or the diff against it exits non-zero
- **THEN** the guard evaluates the deny list against the unnarrowed pre/post set and logs that
  narrowing was unavailable

#### Scenario: empty pre-run SHA still fails open unchanged
- **WHEN** `pre_sha` is empty because `rev-parse` failed
- **THEN** the guard returns no forbidden paths and runs no git diff at all, exactly as today

