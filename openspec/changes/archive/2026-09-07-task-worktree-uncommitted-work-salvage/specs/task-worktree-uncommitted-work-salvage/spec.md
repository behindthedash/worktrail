## Purpose

Ensures that work a task worker left uncommitted in its worktree is preserved
and reported at teardown instead of being silently discarded by a forced
`git worktree remove`.

## ADDED Requirements

### Requirement: Task worktree teardown inspects for uncommitted work
Task worktree teardown SHALL inspect the worktree for uncommitted tracked
modifications before removing it, rather than removing it unconditionally.

#### Scenario: Clean worktree is removed unchanged
- **WHEN** a task worktree has no uncommitted tracked modifications at teardown
- **THEN** it is removed exactly as it is today, with no additional commit and
  no salvage report

#### Scenario: Dirty worktree is detected before removal
- **WHEN** a task worktree has uncommitted tracked modifications at teardown
- **THEN** teardown detects them before the removal is issued

### Requirement: Uncommitted task work is preserved rather than discarded
When uncommitted tracked modifications are found in a task worktree at
teardown, they SHALL be preserved as a commit on that task's own branch before
the worktree is removed. Untracked files SHALL NOT be swept into the
preserved commit, so worker scratch is never captured.

#### Scenario: Uncommitted worker edits survive teardown
- **WHEN** a task worker leaves tracked file modifications uncommitted and the
  task's worktree is torn down
- **THEN** those modifications exist as a commit on the task's branch after
  teardown completes

#### Scenario: Worker scratch is not captured
- **WHEN** a task worktree contains untracked scratch files at teardown
- **THEN** those files are not included in any preserved commit

### Requirement: Salvaged task work is reported to the operator
Teardown SHALL report that a task worktree held uncommitted work and where
that work was preserved, so an operator can find it without inspecting git
state after the run.

#### Scenario: Salvage is surfaced in the run output
- **WHEN** uncommitted work is preserved during a task worktree teardown
- **THEN** the run output identifies the task whose worktree was dirty and the
  branch carrying the preserved work

#### Scenario: Clean teardown stays quiet
- **WHEN** a task worktree is clean at teardown
- **THEN** no salvage message is emitted for it

### Requirement: Salvage never blocks teardown
Preserving uncommitted work SHALL be best-effort: a failure to inspect or
commit the work SHALL NOT prevent the worktree from being removed, nor
propagate an error that masks the teardown's own outcome.

#### Scenario: Preservation fails
- **WHEN** the attempt to preserve a dirty task worktree's work fails
- **THEN** the failure is reported and the worktree removal still proceeds

#### Scenario: Existing removal errors are unchanged
- **WHEN** the worktree removal itself fails
- **THEN** the error surfaced to the caller is the same one raised today, not
  one originating from the salvage attempt
