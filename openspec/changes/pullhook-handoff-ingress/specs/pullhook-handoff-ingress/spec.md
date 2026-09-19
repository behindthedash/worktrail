## ADDED Requirements

### Requirement: WorkTrail consumes external handoff events by pulling
WorkTrail SHALL provide an unattended ingress command that retrieves events from a configured PullHook channel using a consume-role credential. The consumer SHALL require no inbound network path to the WorkTrail host.

#### Scenario: Event is available
- **WHEN** a supported accepted-work event is available on the configured channel
- **THEN** WorkTrail can claim it and begin materialization

### Requirement: Only supported versioned envelopes are materialized
The ingress SHALL validate the event schema and required identity, target, provenance, and handoff fields before mutating the work queue. Unknown schemas or malformed envelopes SHALL NOT create a brief.

#### Scenario: Datalena v1 event is valid
- **WHEN** a `datalena.worktrail-handoff.v1` envelope contains all required fields
- **THEN** it is mapped to canonical WorkTrail handoff arguments

#### Scenario: Unknown schema arrives
- **WHEN** an event uses an unsupported schema/version
- **THEN** no handoff is created and the result identifies the unsupported schema

### Requirement: Materialization uses WorkTrail's canonical handoff writer
The ingress SHALL create accepted work through `create_handoff()`/the canonical WorkTrail handoff contract rather than writing Markdown directly.

#### Scenario: Valid external work is materialized
- **WHEN** a valid event is accepted
- **THEN** the resulting brief passes normal WorkTrail validation, routing, canonical-style, and capture-provenance rules

### Requirement: Redelivery cannot create duplicate handoffs
The ingress SHALL durably associate each external event identity with its resulting WorkTrail handoff and SHALL consult that association before creating a brief.

#### Scenario: Same event is delivered twice
- **WHEN** an already-materialized event is received again
- **THEN** no new brief is created and the original handoff identity is returned

#### Scenario: Process crashes after materialization
- **WHEN** the process restarts after creating/materializing an event but before successful relay acknowledgement
- **THEN** retry reconciles to the existing handoff rather than creating another

### Requirement: Relay acknowledgement follows durable queue persistence
The consumer SHALL acknowledge a PullHook item only after canonical handoff creation and all configured required persistence, including git-backed queue push when required, succeed.

#### Scenario: Git persistence succeeds
- **WHEN** the handoff and external-event marker are committed/pushed successfully
- **THEN** the PullHook item may be acknowledged

#### Scenario: Git persistence fails
- **WHEN** required queue git persistence fails
- **THEN** the PullHook item is not acknowledged and retry remains possible

### Requirement: External provenance is retained
The created handoff SHALL retain the producer capture source and references sufficient to trace the work back to the external event, source repository, merged PR, and source finding/evidence.

#### Scenario: Operator inspects queued brief
- **WHEN** an operator opens an externally materialized handoff
- **THEN** the brief identifies where the work originated without requiring PullHook database access
