## ADDED Requirements

### Requirement: Automatic resolve-and-retry on sibling merge conflict
When `add_stacked_worktree()` is called with an `assembly_resolve_spawn`
callable and a sibling dependency branch merge conflicts, the system SHALL
spawn a `dispatch.ROLE_ASSEMBLY_RESOLVE` worker scoped to the worktree and the
conflicting file(s) before giving up, instead of immediately raising
`WorktreeStackConflictError`.

#### Scenario: Resolve spawn configured and conflict resolved
- **WHEN** a sibling dependency branch merge conflicts inside a freshly
  created stacked task worktree and `assembly_resolve_spawn` is provided
- **THEN** the system spawns a resolve worker scoped to that worktree and the
  conflicted files, and if the worker resolves and commits the merge, the
  worktree ends up with the merge concluded and carrying the sibling's
  commits, with no `WorktreeStackConflictError` raised

### Requirement: Verified-clean acceptance before trusting a resolution
The system SHALL accept a resolve attempt as successful only after verifying
git state directly: no `MERGE_HEAD` in progress, a clean `git status
--porcelain`, and no `<<<<<<<` conflict marker remaining in any file that was
listed as conflicted before the resolve attempt. A worker's self-reported
success SHALL NOT be trusted without this verification.

#### Scenario: Worker reports success but conflict markers remain
- **WHEN** the resolve worker's report-back claims success but a
  previously-conflicted file still contains a `<<<<<<<` conflict marker, or
  `MERGE_HEAD` is still present, or the worktree is not clean
- **THEN** the system treats the resolution as unverified and does not accept
  it as resolved

#### Scenario: Report-back unparseable but git state is clean
- **WHEN** the resolve worker's report-back cannot be parsed, but git state
  shows the merge concluded, the tree is clean, and no conflicted file
  retains a `<<<<<<<` marker
- **THEN** the system accepts the resolution as successful (salvaged from git
  state), consistent with the existing PR-integration-time salvage behavior

### Requirement: Hard fallback to raise-and-block on any unresolved case
When `assembly_resolve_spawn` is not provided, the resolve worker errors or
explicitly reports failure, the attempt exhausts its retry budget, or the
outcome cannot be verified clean, the system SHALL abort the conflicted merge
and raise `WorktreeStackConflictError` with the same message and behavior as
before this change. The system SHALL NEVER leave a task's worktree missing a
sibling dependency's commits without raising.

#### Scenario: No resolve spawn configured
- **WHEN** `add_stacked_worktree()` is called without an
  `assembly_resolve_spawn` (or with it explicitly `None`)
- **THEN** a sibling merge conflict aborts the merge and raises
  `WorktreeStackConflictError` exactly as before this change, with no attempt
  to spawn a resolve worker

#### Scenario: Resolve worker fails or times out
- **WHEN** `assembly_resolve_spawn` is provided but the resolve worker raises
  an exception, times out, or explicitly reports failure in its report-back
- **THEN** the system aborts the conflicted merge (`git merge --abort`) and
  raises `WorktreeStackConflictError`, identical to the no-resolve-spawn
  fallback

#### Scenario: Resolve attempt succeeds but verification fails
- **WHEN** the resolve worker completes without error but the git-state
  verification (no `MERGE_HEAD`, clean tree, no conflict markers) does not
  pass
- **THEN** the system aborts the conflicted merge and raises
  `WorktreeStackConflictError` rather than proceeding on an unverified
  resolution

### Requirement: Backward-compatible default behavior
`add_stacked_worktree()` SHALL default `assembly_resolve_spawn` to `None`, so
existing callers that do not pass the parameter observe no behavior change.

#### Scenario: Existing caller unaware of the new parameter
- **WHEN** an existing caller (including `live_run`'s cassette/demo path and
  any test) invokes `add_stacked_worktree()` without an
  `assembly_resolve_spawn` argument
- **THEN** behavior is identical to before this change: a sibling merge
  conflict raises `WorktreeStackConflictError` immediately
