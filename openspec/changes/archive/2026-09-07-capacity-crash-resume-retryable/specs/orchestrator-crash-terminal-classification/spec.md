## ADDED Requirements

### Requirement: A capacity-exhaustion crash is journaled as retryable
When an exception escapes a task's drive loop and is caught by the orchestrator's per-task
crash wrapper, the orchestrator SHALL classify it before journaling. An exception that reports
that every configured cell able to serve the task is capacity gated (`NoExecutionTarget`, or any
subclass of it) SHALL be journaled with `terminal_status: "retryable"`; every other exception
SHALL be journaled with `terminal_status: "failed"`, unchanged. The classification SHALL be
identical for both drive entrypoints (the direct live run and the pipeline scheduler). The
journal entry's `notes` SHALL keep the existing `drive crashed: <repr>` text in both cases, and
the crashed task's in-run status SHALL remain `failed` so no dependent is dispatched into the
same gate during the current run.

#### Scenario: Every cell gated during a task drive
- **WHEN** a task's drive raises `NoExecutionTarget` because every cell in its routing row is
  capacity gated
- **THEN** the journal gains a `drive` failure entry for that task whose
  `report.terminal_status` is `retryable` and whose `report.notes` begins with `drive crashed:`

#### Scenario: An ordinary crash stays terminal
- **WHEN** a task's drive raises any exception that is not a capacity-exhaustion error
- **THEN** the journal entry for that task carries `report.terminal_status: "failed"`, exactly as
  before

#### Scenario: A configuration error is not retryable
- **WHEN** a task's drive raises `InvalidCandidate` (an explicit provider/model override that is
  not in the supported catalog)
- **THEN** the journal entry carries `report.terminal_status: "failed"`, because a resume would
  hit the same misconfiguration

#### Scenario: Both drive entrypoints classify identically
- **WHEN** the same capacity-exhaustion exception escapes the drive loop under the pipeline
  scheduler rather than the direct live run
- **THEN** the journaled `terminal_status` is `retryable`, the same as the direct live run

#### Scenario: The crashed task is still failed for the rest of the run
- **WHEN** a task is journaled `retryable` after a capacity-exhaustion crash
- **THEN** its in-memory status for the remainder of that run is `failed`, so the runnable
  frontier does not immediately re-dispatch it and its dependents stay blocked

### Requirement: Resume re-dispatches a capacity-gated task without --fresh
Because journal replay re-applies only non-`retryable` terminal statuses onto task status, a
task journaled `retryable` by a capacity-exhaustion crash SHALL be `pending` after replay and
SHALL therefore be re-dispatched by the next resume, with no `--fresh` re-run and no surgical
journal clearing. Replay SHALL NOT require any other entry, flag, or operator action for this.

#### Scenario: Resume after capacity returns
- **WHEN** a run whose only failure entry for a task is a capacity-exhaustion `drive` crash is
  resumed after the gate has lifted
- **THEN** replay leaves that task `pending` and the resume dispatches it again

#### Scenario: A genuine failure still needs intervention
- **WHEN** a run whose task carries a non-capacity `drive` crash entry is resumed
- **THEN** replay bakes `failed` back onto the task and the resume does not re-dispatch it

#### Scenario: The operator is told which case happened
- **WHEN** a capacity-exhaustion crash is caught
- **THEN** the run prints a line naming the task and the capacity exhaustion and stating the task
  will be re-dispatched on resume, distinct from the `-- marking failed` line printed for any
  other crash
