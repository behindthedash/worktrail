## Purpose

Resilient creation of stacked task worktrees in `add_stacked_worktree()`:
automated resolve-and-retry when stacking a sibling dependency branch
conflicts, verified-clean acceptance of any resolution, and a hard fail-loud
fallback so a task worktree is never silently missing a dependency's commits.
## Requirements
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

### Requirement: Carry already-merged dependency content across a squash-merge boundary
When a stacked task worktree is created and one of the task's dependencies is in a
DONE-like status with no task branch remaining (its group PR was merged and the branch
cleaned up before this worktree was created), and at least one of that dependency's
declared files is missing from the freshly stacked worktree, the system SHALL bring the
merged base content into the worktree by merging the freshest available base ref
(remote base after a fetch, falling back to the local base ref) before dependency-file
validation runs. The merge SHALL NOT auto-resolve conflicts in favor of either side: a
genuine content-level conflict between the stacked worktree and the base ref SHALL fail
the merge loudly (aborting it) rather than being silently resolved, so
dependency-file validation's fail-loud backstop remains the source of truth when the
carried content genuinely diverges. (An earlier version of this requirement resolved
conflicts in favor of the stacked worktree via `-X ours`, "consistent with the existing
group-branch squash reconciliation" -- that referenced mechanism was later found unsafe
and removed; see `docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md`.)

#### Scenario: Tail task dispatched after dependencies squash-merged and cleaned up
- **WHEN** a tail (e2e/cleanup) task's worktree is created after every dependency's
  group PR has been squash-merged into base and the dependency task branches deleted
- **THEN** the stacked worktree contains each dependency's declared files, dependency-file
  validation passes without raising `WorktreeMissingDependencyFileError`, and the tail
  task is dispatched normally

#### Scenario: Dependency branches still present
- **WHEN** a task's worktree is created while all of its dependencies' task branches
  still exist
- **THEN** stacking behavior is unchanged from before this requirement (branch off the
  first dependency, merge the siblings) and no base-ref merge is attempted

#### Scenario: Base ref unavailable
- **WHEN** the carry step is needed but the base ref cannot be fetched or resolved
  (offline, no remote configured, or the caller did not supply remote/base)
- **THEN** the system does not silently proceed with a worktree missing dependency
  content: dependency-file validation still fails loud with
  `WorktreeMissingDependencyFileError`, exactly as before this change

#### Scenario: Base ref content genuinely conflicts with the stacked worktree
- **WHEN** the carry merge encounters a real content-level conflict between the
  stacked worktree's pre-squash content and the base ref -- including on a file
  the triggering dependency never declared
- **THEN** the merge is aborted rather than auto-resolved in favor of either side,
  and dependency-file validation's fail-loud backstop is the mechanism that
  surfaces the missing content, instead of the conflict being silently discarded

