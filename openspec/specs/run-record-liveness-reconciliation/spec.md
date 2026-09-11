# run-record-liveness-reconciliation Specification

## Purpose
Makes run-record liveness truthful about what it knows: heartbeat freshness is
activity evidence, while a `worktrail-detach` handle can provide actual local
process evidence. Automatic orphan reconciliation requires both a stale
heartbeat and confirmed terminated ownership.
## Requirements
### Requirement: Run records bind a detached owner after a successful launch

The system SHALL provide a validated way to associate an already-open run
record with a `worktrail-detach` launch identity, including its name and an
explicit non-default state directory when supplied. The full-real workflow
SHALL bind that identity to `$RUN` after a successful detached launch and
before monitoring it. Binding metadata SHALL be optional so records created by
older callers remain valid.

#### Scenario: A full-real launch is associated with its run

- **WHEN** the workflow successfully launches `worktrail-live full-real` with
  `worktrail-detach`
- **THEN** the returned detach identity is persisted on the pre-existing run
  record before the workflow starts monitoring the launch

#### Scenario: An older record has no detached owner metadata

- **WHEN** liveness reads a non-terminal record with no bound detached owner
- **THEN** it SHALL remain readable and report unavailable owner evidence
  rather than inferring that the run process is dead

### Requirement: Liveness distinguishes heartbeat freshness from detached process state

The `liveness` command SHALL continue to report heartbeat freshness and
dispatch identity compatibility, and SHALL additionally report the state of a
bound detached owner and a reconciliation classification. A running detached
owner SHALL classify as active even when its heartbeat is stale. A stale
heartbeat paired with an `exited` or `gone` detached owner SHALL classify as a
confirmed orphan. Missing, malformed, or unknown owner evidence SHALL classify
as unknown rather than confirmed dead.

#### Scenario: A detached orchestrator is still running after its heartbeat expires

- **WHEN** a non-terminal record has an expired `updated_at` heartbeat and its
  bound detached owner reports `running`
- **THEN** liveness SHALL report the stale heartbeat separately and classify
  the record as an active process

#### Scenario: A detached owner exited without finishing its record

- **WHEN** a non-terminal record has an expired heartbeat and its bound
  detached owner reports `exited` or `gone`
- **THEN** liveness SHALL classify the record as a confirmed orphan

### Requirement: Orphan sweeping requires confirmed dead-owner evidence

`sweep-orphans` SHALL terminalize only non-terminal records classified as
confirmed orphans. It SHALL not terminalize a record whose detached owner is
running, whose heartbeat is fresh after owner exit, or whose owner evidence is
unavailable or unknown. Its JSON summary SHALL separately report records
skipped for an active detached process and an unknown owner so an operator can
reconcile them without treating the absence of a heartbeat as a verdict.

#### Scenario: A stale heartbeat belongs to a running detached process

- **WHEN** `sweep-orphans` encounters a non-terminal record with a stale
  heartbeat and a bound owner reporting `running`
- **THEN** it SHALL leave the record non-terminal and list it as skipped for
  an active process

#### Scenario: A confirmed orphan is swept

- **WHEN** `sweep-orphans` encounters a non-terminal record classified as a
  confirmed orphan
- **THEN** it SHALL finish the record with the requested completion status and
  record an auto-reconciliation note that identifies the liveness evidence

