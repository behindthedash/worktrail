## ADDED Requirements

### Requirement: Carry already-merged dependency content across a squash-merge boundary
When a stacked task worktree is created and one of the task's dependencies is in a
DONE-like status with no task branch remaining (its group PR was merged and the branch
cleaned up before this worktree was created), and at least one of that dependency's
declared files is missing from the freshly stacked worktree, the system SHALL bring the
merged base content into the worktree by merging the freshest available base ref
(remote base after a fetch, falling back to the local base ref) before dependency-file
validation runs. Apparent conflicts between pre-squash stacked content and the squashed
base commit SHALL be resolved in favor of the stacked worktree's content, consistent
with the existing group-branch squash reconciliation.

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
