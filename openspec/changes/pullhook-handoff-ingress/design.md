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

### Decision 6: Feedback routing uses trusted source configuration

The `worktrail.feedback.v1` adapter accepts only the GGB source identity
`{id: "gracefully-giving-back", kind: "website-feedback"}` and binds it to the
trusted repository slug `gracefully-giving-back`. WorkTrail's normal repository
policy resolves remote and base branch; the event body cannot select a local
checkout, queue directory, remote, or branch. In particular,
`feedback.metadata.sourceFileHint` is untrusted descriptive evidence and MUST
NOT be used as a path to read or write.

### Decision 7: Feedback becomes a normal triageable handoff

The stable feedback request UUID is the external `event_id` and the consumer's
idempotency identity. The adapter maps the feedback title and body to the
canonical handoff focus and context, respectively, and preserves the source
URL and captured metadata as provenance. The event does not request autonomous
implementation; implementation intent remains unspecified so normal WorkTrail
triage applies. Optional values remain optional and are not invented.

The adapter validates all fields it consumes, rejects unsupported envelope or
metadata keys, and rejects an encoded JSON envelope larger than PullHook's
262,144-byte default request limit before creating a brief. Supported metadata
keys are `pageUrl`, `elementSelector`, optional `elementText`, optional
`sourceFileHint`, optional `componentHint`, `changeKindGuess`, and `viewport`.
It must not persist screenshot data URLs or allow metadata to override trusted
routing.

The accepted producer shape is:

```json
{
  "schema": "worktrail.feedback.v1",
  "event_id": "<feedback UUID>",
  "source": {"id": "gracefully-giving-back", "kind": "website-feedback"},
  "occurred_at": "<ISO 8601 timestamp>",
  "feedback": {
    "id": "<same feedback UUID>",
    "title": "Website feedback: <change kind>",
    "body": "<submitted request text>",
    "url": "<optional absolute HTTP(S) page URL>",
    "metadata": {
      "pageUrl": "<captured page URL, may be a relative path>",
      "elementSelector": "<captured selector>",
      "elementText": "<optional captured text>",
      "sourceFileHint": "<optional descriptive hint>",
      "componentHint": "<optional descriptive hint>",
      "changeKindGuess": "<producer classification>",
      "viewport": {"width": 1280, "height": 720}
    }
  }
}
```

## Failure Semantics

- Unknown/malformed event: no queue mutation; leave unacknowledged and return a safe rejection. PullHook lease expiry/retry/dead-letter policy remains responsible for eventual handling.
- Duplicate event already materialized: treat as success and ack without creating a second brief.
- WorkTrail create failure: no ack.
- Git commit/push failure when git durability is required: no ack. The retry must discover the already-created event marker and complete persistence/ack without duplicating the brief.
- Unknown source identity, source kind, malformed feedback, invalid URL, oversized content, or unsupported schema: no brief and no ack; report a safe, actionable rejection while preserving the PullHook item for operator diagnosis/retry policy.
- A source identity other than the exact GGB pair has no trusted repository mapping: no brief and no ack. Routing errors are not repaired by accepting a payload-supplied target.
