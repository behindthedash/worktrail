## Purpose

Prevents a tail-kind (e2e/cleanup) task with an empty `files:` scope from
being dispatched with a vague fallback that lets the worker silently
re-derive and reimplement the whole change instead of doing nothing.

## ADDED Requirements

### Requirement: Explicit no-op instruction for zero-file tail tasks

When the cold-worker prompt is built for a tail-kind (`e2e` or `cleanup`)
task whose `files:` list is empty, the system SHALL render a scope
instruction that states plainly that zero files are expected to change and
that the task is verification-only against the already-integrated base,
instead of the generic `"(see task file)"` fallback used for other tasks
with no declared file list.

#### Scenario: Tail task with empty files list gets an explicit no-op instruction

- **WHEN** the worker prompt is built for a task with `kind: e2e` (or
  `kind: cleanup`) and an empty `files:` list
- **THEN** the rendered prompt does not contain the bare `"(see task file)"`
  scope fallback and instead states that no files are expected to change
  and the task is verification-only against the base

#### Scenario: Tail task with a declared files list is unaffected

- **WHEN** the worker prompt is built for a task with `kind: e2e` (or
  `kind: cleanup`) that does declare a non-empty `files:` list
- **THEN** the rendered prompt's scope lists those files exactly as before
  this change, with no no-op instruction added

#### Scenario: Non-tail task with an empty files list is unaffected

- **WHEN** the worker prompt is built for a task whose `kind` is not `e2e`
  or `cleanup` and whose `files:` list is empty
- **THEN** the rendered prompt keeps the existing `"(see task file)"`
  fallback unchanged

### Requirement: No PR is opened for a tail task that made no commits

When a tail-kind task's dispatch produces no commits (the zero-file,
verification-only case), the integration flow SHALL NOT open a PR for that
task's branch.

#### Scenario: Zero-file tail task produces no commits

- **WHEN** a tail-kind task with an empty `files:` list is dispatched under
  the no-op instruction and its worker makes no commits
- **THEN** the integration flow does not create or discover a PR for that
  task, and the run does not report a near-duplicate PR for work already
  covered by the group's own integration

#### Scenario: Tail task that legitimately produced commits is unaffected

- **WHEN** a terminal tail-kind task has its own commits that never merged
  onto base (the case already covered by `tail-task-auto-reconciliation`)
- **THEN** this requirement does not change that reconciliation behavior —
  a PR carrying those commits is still opened or reused exactly as
  `tail-task-auto-reconciliation` already specifies
