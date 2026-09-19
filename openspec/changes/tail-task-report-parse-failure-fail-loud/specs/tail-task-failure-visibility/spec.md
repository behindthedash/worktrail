## ADDED Requirements

### Requirement: Failed tail tasks are recorded in the journal
After the tail pass completes, the system SHALL identify every held-out tail-kind
(`e2e`/`cleanup`) task whose status is terminal and not `done`, and SHALL write them to the
run journal as `failed_tail_tasks`, mapping each task id to its terminal status and the last
recorded failure detail for that task. When no tail task failed, `failed_tail_tasks` SHALL be
absent from the journal (removed if written by an earlier attempt).

#### Scenario: Tail worker returns no report-back JSON
- **WHEN** a verify-only tail task's worker returns output containing no report-back JSON
  block, so the report parse fails and no commit can be salvaged
- **THEN** the journal contains `failed_tail_tasks` naming that task id with its terminal
  status and the parse-failure detail

#### Scenario: Tail tasks all succeed
- **WHEN** every dispatched tail task reaches `done`
- **THEN** `failed_tail_tasks` is absent from the journal

#### Scenario: Tail task never dispatched
- **WHEN** a held-out tail task is still `pending` at the end of the run because it was never
  dispatched
- **THEN** it is not listed in `failed_tail_tasks`

### Requirement: The completion banner names failed tail tasks
When one or more tail tasks failed, the system SHALL NOT print the unqualified
`=== PIPELINE RUN COMPLETE ===` banner; it SHALL print a banner that marks the run as having
failed tail work and names the failed task ids. When no tail task failed, the banner SHALL be
unchanged from today.

#### Scenario: Banner on tail failure
- **WHEN** tail task `2.1` ends `failed` after an unparseable report-back
- **THEN** the final banner names `2.1` and marks the tail as failed, and the unqualified
  `=== PIPELINE RUN COMPLETE ===` line is not printed

#### Scenario: Banner on a clean run
- **WHEN** no tail task failed
- **THEN** the run prints `=== PIPELINE RUN COMPLETE ===` exactly as before this change

### Requirement: full-real exits non-zero when tail tasks failed
The `full-real` subcommand of `worktrail-live` SHALL return a non-zero exit code when the run
result reports failed tail tasks, and SHALL continue to return `0` otherwise. The scheduler's
result dict SHALL carry the `failed_tail_tasks` mapping so the command can decide without
re-reading the journal.

#### Scenario: Non-zero exit on failed tail task
- **WHEN** `full-real` completes a run in which a tail task failed
- **THEN** the command returns a non-zero exit code

#### Scenario: Zero exit on a run with no tail failure
- **WHEN** `full-real` completes a run in which no tail task failed
- **THEN** the command returns `0`
