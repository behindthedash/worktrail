# pullhook-handoff-ingress Specification

## Purpose

Define unattended ingestion of external accepted-work events from PullHook into the canonical WorkTrail handoff queue, with durable deduplication, persistence, and provenance.

## Requirements

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

#### Scenario: GGB feedback v1 event is valid
- **WHEN** a `worktrail.feedback.v1` envelope contains a UUID `event_id`, source `{id: "gracefully-giving-back", kind: "website-feedback"}`, an ISO 8601 `occurred_at`, a `feedback.id` matching `event_id`, non-empty `title` and `body`, and only supported optional `url` and `metadata` fields
- **THEN** it is validated and mapped to canonical WorkTrail handoff arguments with `focus=feedback.title`, `context=feedback.body`, `repo=gracefully-giving-back`, `captured_by=gracefully-giving-back:website-feedback`, and no implementation intent, without requiring Datalena-specific fields

#### Scenario: Feedback source is unknown or not configured
- **WHEN** a feedback envelope has a `source.id` or `source.kind` other than the supported GGB pair
- **THEN** WorkTrail creates no brief, does not acknowledge the PullHook item, and reports a safe configuration or validation error

#### Scenario: Feedback metadata cannot select a repository path
- **WHEN** feedback metadata contains `sourceFileHint`, `componentHint`, `pageUrl`, or other captured context
- **THEN** these values are retained only as bounded descriptive provenance and do not select a checkout, queue directory, remote, or branch

#### Scenario: Feedback contains unsupported or oversized data
- **WHEN** a feedback envelope contains unsupported fields, invalid field types, a non-HTTP(S) `feedback.url`, a screenshot data URL, or exceeds PullHook's 262,144-byte default request limit
- **THEN** WorkTrail rejects it before queue mutation and leaves the PullHook item unacknowledged

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
The created handoff SHALL retain the producer capture source and references sufficient to trace the work back to the external event and its source-specific provenance. Datalena handoffs retain source repository, merged PR, and finding/evidence. GGB feedback handoffs retain the source identity, feedback UUID, occurrence time, submitted page URL when present, and supported captured metadata.

#### Scenario: Operator inspects queued brief
- **WHEN** an operator opens an externally materialized handoff
- **THEN** the brief identifies where the work originated without requiring PullHook database access

#### Scenario: Operator inspects GGB feedback brief
- **WHEN** an operator opens a brief created from GGB website feedback
- **THEN** the brief includes its feedback identity, title/body, source URL when present, and supported context such as page URL, selected element, component/source-file hints, change-kind guess, and viewport without requiring PullHook database access

### Requirement: Feedback source routing is trusted and bounded
The ingress SHALL bind the exact GGB source identity `{id: "gracefully-giving-back", kind: "website-feedback"}` to the WorkTrail repository slug `gracefully-giving-back`; normal WorkTrail repository policy SHALL resolve its remote and base branch. It SHALL validate field types and reject an encoded JSON envelope larger than PullHook's 262,144-byte default request limit before creating a brief. Payload-supplied metadata SHALL NOT control filesystem paths, queue locations, git remotes, or branches. Screenshot data URLs SHALL NOT be materialized.

#### Scenario: Configured source is routed
- **WHEN** the configured GGB source submits a valid event
- **THEN** the resulting handoff records repository slug `gracefully-giving-back`, and WorkTrail's normal repository policy supplies any remote and base branch

#### Scenario: Malicious path hint is supplied
- **WHEN** `sourceFileHint` contains an absolute path, traversal segments, or another path-like value
- **THEN** WorkTrail treats it as bounded text evidence or rejects it under the field validation policy and never reads or writes that path

### Requirement: Invalid events remain available for diagnosis
The ingress SHALL leave malformed, unsupported, or unconfigured-source deliveries unacknowledged, return a safe rejection result, and perform no queue mutation. PullHook lease expiry, retry, and dead-letter behavior SHALL remain the responsibility of PullHook.

#### Scenario: Invalid GGB event is claimed
- **WHEN** the consumer claims an invalid GGB event
- **THEN** it reports the rejection, creates no external-event marker or handoff, and does not acknowledge the delivery
