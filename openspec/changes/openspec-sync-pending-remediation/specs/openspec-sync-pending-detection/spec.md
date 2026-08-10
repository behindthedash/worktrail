## ADDED Requirements

### Requirement: OpenSpec delta reconciliation stage

The dashboard SHALL report a task-complete, verification-complete active
OpenSpec change as `sync-pending` when the change declares a structural delta
that is not reflected in its canonical capability specs. It SHALL report the
change as `complete` when every declared structural delta is reflected, or when
the change contains no delta specs.

#### Scenario: Unsynced delta remains visible
- **WHEN** all change tasks are complete and at least one declared requirement
  or scenario addition, removal, or rename is not reflected in the canonical
  capability spec
- **THEN** the dashboard reports `stage: sync-pending` and directs the operator
  to sync the change

#### Scenario: Reconciled delta leaves the sweep
- **WHEN** all change tasks are complete and every declared structural delta is
  reflected in the canonical capability specs
- **THEN** the dashboard reports `stage: complete` with archive as the next
  lifecycle action

#### Scenario: Change has no delta specs
- **WHEN** a task-complete OpenSpec change has no `specs/**/spec.md` artifacts
- **THEN** the dashboard does not invent sync work and reports it complete

### Requirement: Verification takes precedence over synchronization

The dashboard SHALL evaluate unfinished PR verification before OpenSpec delta
reconciliation so a change is never synchronized from code that has not merged.

#### Scenario: Unsynced change still has an open group PR
- **WHEN** all tasks are checked, the run journal contains an unmerged group PR,
  and the delta is not reflected in canonical specs
- **THEN** the dashboard reports `verify-pending`, not `sync-pending`

### Requirement: Reconciliation is deterministic and read-only

The dashboard SHALL determine structural reconciliation from repository files
without invoking an agent or the OpenSpec CLI. ADDED and MODIFIED requirement
and scenario headings SHALL be present, REMOVED requirement headings SHALL be
absent, and RENAMED FROM/TO headings SHALL respectively be absent/present.

#### Scenario: Dashboard scan runs unattended
- **WHEN** the dashboard evaluates an active OpenSpec change
- **THEN** reconciliation reads only the delta and canonical spec files and
  performs no write, subprocess, network, or model operation

