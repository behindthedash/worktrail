## Purpose

Defines the commit the review worker is told to diff against, so the review covers exactly the
task branch's own commits even when the branch it forked from has moved since.

## ADDED Requirements

### Requirement: The review diff base is the task branch's merge-base with its start ref

When dispatching a review-role worker for a task with a canonical repo available, the live
spawner SHALL set the review `base_commit` to the merge-base, computed in the canonical repo,
of the task's dependency start ref and the task worktree's current `HEAD`. The resulting
two-dot range `{base_commit}..HEAD` evaluated inside the task worktree SHALL contain only
commits made on the task branch.

#### Scenario: Root task reviewed after the base branch advanced

- **WHEN** a root task's worktree was forked from the canonical repo's tip, the canonical
  repo's tip has since received further commits, and the task is dispatched for review
- **THEN** `base_commit` is the fork point, not the canonical repo's current tip, and the
  later base commits do not appear in the reviewer's diff

#### Scenario: Root task reviewed while the base is unchanged

- **WHEN** a root task is dispatched for review and the canonical repo's tip is still the
  commit the worktree was forked from
- **THEN** `base_commit` is that commit's SHA, identical to the previous behavior

#### Scenario: Dependent task reviewed against the dependency tip it stacked on

- **WHEN** a task with a materialized dependency branch is dispatched for review and the
  dependency branch has not moved since the task forked from it
- **THEN** `base_commit` is the dependency branch tip's SHA

#### Scenario: Dependent task reviewed after its dependency branch advanced

- **WHEN** a task forked from its dependency branch, the dependency branch has since received
  further commits, and the task is dispatched for review
- **THEN** `base_commit` is the commit the task forked from, and the dependency's later
  commits do not appear in the reviewer's diff

### Requirement: Merge-base resolution degrades to the previous value

The spawner SHALL NOT fail a review dispatch because the merge-base could not be computed.

#### Scenario: No common ancestor

- **WHEN** the start ref and the worktree `HEAD` share no ancestor, or the worktree `HEAD`
  cannot be resolved
- **THEN** `base_commit` is the start ref resolved to a SHA in the canonical repo, exactly as
  before this change

#### Scenario: No canonical repo supplied

- **WHEN** the spawner was constructed without a canonical repo
- **THEN** `base_commit` is the literal `HEAD` sentinel, exactly as before this change
