## ADDED Requirements

### Requirement: Foreign journal entries block a resume
When `full_real` resumes an existing run journal for a `--spec` path, the
system SHALL treat any non-event journal entry whose `task` id has no
matching task in the task set currently loaded for that `--spec` path as a
foreign entry, and SHALL raise an error identifying every foreign task id and
both the journal's originating context and the `--spec` path just requested,
before any journal entry is reconciled onto in-memory task state.

#### Scenario: Journal belongs to an unrelated spec/change
- **WHEN** `full_real` is invoked with `--spec docs/specs/<id>` and an
  existing run journal at that spec's resolved journal path contains entries
  for task ids that do not appear in `docs/specs/<id>/tasks/TASK-*.md`
- **THEN** the run SHALL fail with an error naming the foreign task ids and
  both spec paths, and SHALL NOT reconcile any entry from that journal onto
  the current task set

#### Scenario: Journal genuinely belongs to the current spec
- **WHEN** `full_real` resumes a journal whose every non-event entry's `task`
  id matches a task currently loaded for the requested `--spec` path
- **THEN** the run SHALL proceed exactly as before this change — resume,
  reconcile, and continue fan-out — with no new error or warning

#### Scenario: Partial collision is treated as a mismatch
- **WHEN** a resumed journal contains a mix of entries that match the current
  task set and entries that do not
- **THEN** the run SHALL fail with the same foreign-entry error rather than
  reconciling only the matching subset

### Requirement: Operator can discard a foreign journal
An operator who receives the foreign-journal error SHALL be able to recover
by re-running `full_real` with the existing `--fresh` flag (`resume=False`),
which discards the journal and starts the run from a clean state, without
requiring any change to this guard.

#### Scenario: Recovery via --fresh
- **WHEN** an operator re-runs `full_real --spec docs/specs/<id> --fresh`
  after a foreign-journal error on that spec path
- **THEN** the prior journal is discarded and the run starts fresh, with no
  foreign-journal error raised (there is no journal left to cross-check)
