## ADDED Requirements

### Requirement: Repair a retained worktree missing dependency content on resume
When `ensure_wt` (or its `_pipeline_scheduler` counterpart `_ensure_wt`) resumes into an
already-existing task worktree (the `wt.exists()` branch) and the first
`_require_dependency_files` check raises `WorktreeMissingDependencyFileError`, the system
SHALL re-attempt `_carry_squash_merged_dependencies` once against the retained worktree
(when `remote`/`base` are available) and re-run `_require_dependency_files` before
re-raising. The retained worktree's branch and any uncommitted or committed in-progress
work SHALL NOT be discarded or recreated as part of this repair.

#### Scenario: Retained worktree missing content from a dependency that squash-merged after worktree creation
- **WHEN** a task's worktree already exists on resume, a dependency squash-merged and
  had its branch deleted after the worktree was created, and the first dependency-file
  check raises `WorktreeMissingDependencyFileError`
- **THEN** the system fetches and merges the freshest base ref into the retained
  worktree via the same carry used at creation time, and if that carry brings in the
  missing content, dependency-file validation passes on the re-check and the task
  resumes normally with no raised error and no worktree recreation

#### Scenario: Repair attempt does not resolve the drift
- **WHEN** the repair attempt runs (carry re-attempted, dependency files re-checked) but
  the retained worktree is still missing declared dependency content afterward
- **THEN** the system raises `WorktreeMissingDependencyFileError` exactly as before this
  change, so a genuinely unresolvable drift still fails loud

#### Scenario: Retained worktree already healthy
- **WHEN** a task's worktree already exists on resume and the first
  `_require_dependency_files` check passes
- **THEN** no repair is attempted and behavior is unchanged from before this change

## MODIFIED Requirements

### Requirement: Carry already-merged dependency content across a squash-merge boundary
When a stacked task worktree is created and one of the task's dependencies is in a
DONE-like status with no task branch remaining (its group PR was merged and the branch
cleaned up before this worktree was created), the system SHALL bring the merged base
content into the worktree by merging the freshest available base ref (remote base after
a fetch, falling back to the local base ref) before dependency-file validation runs, then
proceed only once the worktree's `HEAD` is confirmed to already contain that base ref
(`git merge-base --is-ancestor`) -- a no-op when it already does. The merge SHALL NOT
auto-resolve a genuine content-level conflict in favor of either side, EXCEPT for one
narrow, deterministic case: when the merge conflict is confined entirely to the current
OpenSpec change's own `openspec/changes/<change_id>/tasks.md` checklist file (no other
file conflicts), the system SHALL resolve it deterministically by taking the union of
checked (`- [x]`) task lines from both sides of the conflict, commit the resolution, and
continue -- since each concurrently-merged group's integration independently checks off
only its own tasks in that shared file and squash-merge history loses the common
ancestor, making an add/add conflict there routine and always safely resolvable this way.
Any conflict touching any other file, alone or together with tasks.md, SHALL still abort
the merge and fail loud exactly as before this change. (An earlier version of this
requirement resolved conflicts in favor of the stacked worktree via `-X ours`,
"consistent with the existing group-branch squash reconciliation" -- that referenced
mechanism was later found unsafe and removed; see
`docs/specs/research/carry-squash-merged-dependencies-x-ours-risk.md`. A later version
also gated this carry on at least one of the dependency's declared files being missing
from the worktree -- a bare path-existence check that can never detect staleness when the
declared file already existed in the repo before the dependency's own change, which is
the common case for any edit to an established file; that gate was removed so the carry
attempt (and the exact `merge-base --is-ancestor` check that already no-ops when nothing
is missing) is what determines whether content needs to move, not a broken existence
proxy; see `docs/specs/research/tail-dispatch-worktree-stale-pre-existing-file.md`.)

#### Scenario: Tail task dispatched after dependencies squash-merged and cleaned up
- **WHEN** a tail (e2e/cleanup) task's worktree is created after every dependency's
  group PR has been squash-merged into base and the dependency task branches deleted
- **THEN** the stacked worktree contains each dependency's declared files, dependency-file
  validation passes without raising `WorktreeMissingDependencyFileError`, and the tail
  task is dispatched normally

#### Scenario: Dependency's declared file already existed before its own change
- **WHEN** a DONE, branch-gone dependency's declared file is an edit to a file that
  already existed in the repo before the dependency's own change (not a newly-created
  file) -- so the path trivially exists in the stacked worktree regardless of which
  commit it forked from
- **THEN** the carry attempt still runs (it is not skipped merely because the path
  exists), and the `merge-base --is-ancestor` check brings the worktree's `HEAD` up to
  the base ref when it is not already an ancestor, so the dependency's actual edit lands
  and dependency-file validation passes

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
  the triggering dependency never declared, or touching `tasks.md` together with any
  other file
- **THEN** the merge is aborted rather than auto-resolved in favor of either side,
  and dependency-file validation's fail-loud backstop is the mechanism that
  surfaces the missing content, instead of the conflict being silently discarded

#### Scenario: Conflict confined to the change's own tasks.md checklist
- **WHEN** the carry merge conflicts, and the conflict is confined entirely to
  `openspec/changes/<change_id>/tasks.md` with no other file in conflict
- **THEN** the system resolves the file by taking the union of checked task lines from
  both sides, commits the merge, and dependency-file validation proceeds against the
  resolved worktree instead of the merge aborting
