## ADDED Requirements

### Requirement: Tail tasks dispatch only when their dependency groups are MERGED
When the pipeline scheduler reaches tail dispatch, the system SHALL dispatch a held-out
tail-kind (`e2e`/`cleanup`) task only if every non-tail task in its `deps` belongs to a
planned group whose journal state is `MERGED`. A tail task with any dependency in a group
whose state is `OPEN`, `QUARANTINED`, or absent from the journal SHALL be held: it SHALL NOT
be dispatched, SHALL NOT be marked done, failed, or escalated, and SHALL remain `pending` on
disk so a later resume re-evaluates it.

#### Scenario: All dependency groups merged
- **WHEN** a tail task depends on tasks whose groups are all recorded as `MERGED` in the
  journal
- **THEN** the tail task is dispatched exactly as before this change

#### Scenario: Dependency group quarantined
- **WHEN** a tail task depends on a task whose group is recorded as `QUARANTINED`
- **THEN** the tail task is not dispatched and its status on disk is still `pending`

#### Scenario: Dependency group PR still open
- **WHEN** a tail task depends on a task whose group is recorded as `OPEN`
- **THEN** the tail task is not dispatched and its status on disk is still `pending`

#### Scenario: Dependency that is itself a tail task
- **WHEN** a `cleanup` task depends only on an `e2e` task whose own dependency groups are
  all `MERGED`
- **THEN** the gate does not hold the `cleanup` task; the frontier orders it after the
  `e2e` task as before

### Requirement: Held tail tasks are excluded without unblocking their dependents
The system SHALL exclude a held tail task from the tail pass by removing it from the tasks
handed to the tail scheduler, and SHALL NOT pre-mark it as completed. A task whose
dependencies include a held tail task SHALL therefore also not become runnable in that pass.

#### Scenario: Mixed held and dispatchable tail tasks
- **WHEN** one tail task is dispatchable and another is held because its dependency group is
  `QUARANTINED`
- **THEN** only the dispatchable task runs in the tail pass, and a third task depending on
  the held one is not started

#### Scenario: Every held-out tail task is blocked
- **WHEN** every held-out tail task is blocked by a non-merged dependency group
- **THEN** no tail pass is started and the scheduler returns the same no-op result as when
  there is no tail work

### Requirement: The hold is recorded and reported
When one or more tail tasks are held, the system SHALL write `pending_tail_blocked` to the
journal, mapping each held task id to the list of `<group>=<STATE>` entries that block it,
and SHALL print a `NOTE` line naming the held task ids and their blocking groups. When no
task is held, `pending_tail_blocked` SHALL be absent from the journal (removed if present
from an earlier attempt).

#### Scenario: Blocked map written
- **WHEN** tail task `T030` is held because dependency `T012` is in group `feature-2` with
  state `QUARANTINED`
- **THEN** the journal contains `pending_tail_blocked: {"T030": ["feature-2=QUARANTINED"]}`
  and the log contains a `NOTE` naming `T030` and `feature-2`

#### Scenario: Blocked map cleared on resume after merge
- **WHEN** a resumed run finds every dependency group `MERGED` for a task that was previously
  recorded in `pending_tail_blocked`
- **THEN** the task is dispatched and `pending_tail_blocked` is removed from the journal
