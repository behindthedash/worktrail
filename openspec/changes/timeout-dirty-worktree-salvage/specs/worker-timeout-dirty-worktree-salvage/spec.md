## Purpose

Preserves safely scoped implementation work when a worker times out before it
can commit and report, while leaving unsafe work for explicit human triage.

## ADDED Requirements

### Requirement: Timed-out scoped implementation work is recovered
When an implement or fix worker times out, the system SHALL inspect the task
worktree before recording the timeout as a failure. If the worktree has
uncommitted changes and every changed path is within that task's declared file
scope, the system SHALL commit those changes to the task branch, record a
successful implementation or fix result identifying the recovery commit, and
continue through the existing review and cleanup lifecycle. The recovered
result SHALL identify that it was salvaged after a timeout; it SHALL NOT claim
that the timed-out worker completed its tests or review.

#### Scenario: Scoped implementation edit survives a timeout
- **WHEN** an implement worker times out after editing only declared source and
  test files, without creating a commit
- **THEN** the system creates a recovery commit, records a salvaged successful
  implement result with that commit, and dispatches the task's ordinary review
  step

#### Scenario: Scoped fix edit survives a timeout
- **WHEN** a fix worker times out after editing only its declared files, without
  creating a commit
- **THEN** the system creates a recovery commit and continues with the existing
  post-fix lifecycle

#### Scenario: A timeout after an existing worker commit remains recoverable
- **WHEN** an implement or fix worker times out after advancing the task
  branch's HEAD
- **THEN** the system retains the existing committed-work evidence and does not
  create a duplicate recovery commit

### Requirement: Unsafe timeout work remains failed and preserved
The system SHALL NOT auto-commit timeout leftovers for a review or cleanup
worker, for a worktree with no committable change, or when any changed path is
outside the task's declared file scope or cannot be safely classified against
that scope. In those cases it SHALL retain the existing timeout failure outcome,
leave the task worktree intact, and record or print a diagnostic that identifies
why automatic recovery was refused.

#### Scenario: Out-of-scope change blocks recovery
- **WHEN** an implement worker times out with one declared-file change and one
  changed path outside its declared file scope
- **THEN** the system records the timeout as failed, creates no recovery commit,
  leaves both changes in the task worktree, and reports the out-of-scope path

#### Scenario: Review timeout is not inferred as success
- **WHEN** a review worker times out with a dirty worktree
- **THEN** the system records the timeout as failed and does not create a
  recovery commit or review verdict

#### Scenario: Empty timeout worktree remains failed
- **WHEN** an implement or fix worker times out without an advanced HEAD or
  committable dirty change
- **THEN** the system retains the existing timeout failure behavior
