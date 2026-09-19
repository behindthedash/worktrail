## Context

`worktrail-handoff` is the canonical queue writer. Its skill explicitly prohibits hand-writing queue Markdown and supports `captured-by` provenance. PullHook is already deployed as a durable pull relay and exposes claim/ack semantics. The private `work-queue` repository is a git-backed copy of the operator's `$WORK_QUEUE_DIR`; it is data, not an integration API.

## Goals / Non-Goals

**Goals**
- Turn externally accepted work into exactly one canonical WorkTrail handoff.
- Preserve pull-based security: no inbound connection to the local WorkTrail machine.
- Ack only after the queue mutation is durable.
- Keep transport schema adaptation isolated from core queue semantics.

**Non-Goals**
- Executing the handoff immediately; normal WorkTrail triage/drain decides that.
- Teaching PullHook WorkTrail semantics.
- Letting producers write queue Markdown or Git directly.
- Pulling/merging the work-queue git repository during a live queue mutation.

## Decisions

### Decision 1: Consumer runs beside the local queue

The ingress command runs where `$WORK_QUEUE_DIR` and WorkTrail are installed. It uses PullHook's consume credential and calls the existing WorkTrail Python API, preserving all validation/routing/dedup behavior.

### Decision 2: Claim -> validate -> dedupe -> create -> git persist -> ack

The consumer claims one relay item with a bounded lease. It validates the event, checks durable external identity, creates through `create_handoff`, persists an external-event marker, and, when git sync is configured/required, commits and pushes. Only then does it acknowledge the PullHook item.

### Decision 3: External idempotency is explicit

PullHook producer idempotency prevents most duplicates, but WorkTrail also needs consumer-side protection against redelivery after a crash between creation and ack. Store a deterministic marker keyed by `schema + event_id/dedupe_key` in WorkTrail-owned queue metadata (or canonical brief provenance that can be scanned deterministically). The check and marker write are part of the materialization transaction boundary.

### Decision 4: work-queue repo is persistence, not the API

The consumer never opens a PR to `behindthedash/work-queue`. The local queue remains authoritative for atomic claim semantics. For externally ingested new briefs, the consumer performs the same safe push-only synchronization doctrine already documented for the git-backed queue. It must not `git pull` a live queue.

### Decision 5: Schema adapters are allowlisted

Ingress rejects unknown schemas. The Datalena v1 adapter maps:
- target repository/remote/base branch -> WorkTrail repo fields
- handoff focus/context/approach/artifacts -> canonical handoff body
- producer capture source -> `captured-by`
- source PR/finding/evidence -> provenance references in artifacts/context.

Future producers add adapters rather than weakening validation.

## Failure Semantics

- Unknown/malformed event: no queue mutation; leave unacked or dead-letter according to explicit operator mode.
- Duplicate event already materialized: treat as success and ack without creating a second brief.
- WorkTrail create failure: no ack.
- Git commit/push failure when git durability is required: no ack. The retry must discover the already-created event marker and complete persistence/ack without duplicating the brief.
