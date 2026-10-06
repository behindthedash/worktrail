## Why

WorkTrail's handoff queue is intentionally local: `worktrail-handoff` writes canonical briefs under `$WORK_QUEUE_DIR`, and the queue may optionally be a private git repository. PullHook provides a safe remote intake path, but the existing WorkTrail consumer only materializes Datalena's handoff schema. Gracefully Giving Back (GGB) now publishes website feedback through PullHook using a separate, versioned feedback schema, so those accepted submissions still need a WorkTrail adapter.

PullHook already supplies the missing transport: authenticated channels, durable SQLite storage, idempotent publish, claim/ack lifecycle, and pull-based consumption. WorkTrail needs a consumer bridge that retrieves accepted-work envelopes and materializes them through the existing `create_handoff`/CLI contract.

## What Changes

- Add a `worktrail-pullhook-ingress` command that consumes a configured PullHook channel.
- Validate supported event schemas before any queue mutation; support both `datalena.worktrail-handoff.v1` and GGB's `worktrail.feedback.v1`.
- Claim an item, map the envelope into canonical `create_handoff()` arguments, and stamp `captured-by` from the producer.
- Preserve external event/dedupe/source provenance in the handoff body/artifacts so a queue item can be traced back to its merged source PR or submitted website feedback.
- Bind GGB's supported source identity to the trusted WorkTrail repository slug `gracefully-giving-back`; never treat payload metadata such as `sourceFileHint` as a filesystem path or repository selector.
- Deduplicate before creating a brief using a durable external event marker/index so redelivery cannot create another handoff.
- When `$WORK_QUEUE_DIR` is a git repository, commit and push the newly created brief before acknowledging the PullHook item. Ack only after durable local creation and required git sync succeed.
- On validation, WorkTrail creation, or required git-sync failure, do not ack; allow lease expiry/retry/dead-letter behavior to remain PullHook's responsibility.
- Add bounded `--once` and drain modes suitable for cron/systemd/local scheduler use. This is a retriever, not a public listener.

## Capabilities

### New Capabilities
- `pullhook-handoff-ingress`: pull-based, idempotent external accepted-work ingestion into WorkTrail's canonical handoff queue.

### Modified Capabilities
None. Existing handoff creation, queue claim/done/release, and PullHook remain authoritative for their own domains.

## Impact

- A second strict envelope adapter, trusted GGB repository binding, operator documentation, and conformance/e2e verification for GGB feedback ingestion.
- Local configuration/secrets for PullHook consume credential/channel.
- Optional git-backed `$WORK_QUEUE_DIR` becomes the durable off-machine record for externally ingested briefs.
- No requirement for producer repositories to install WorkTrail or hold work-queue GitHub credentials.

## Origin

This change originally specified and delivered Datalena's PullHook consumer (WorkTrail PR #1273). Its completed tasks remain recorded below. This completion pass adds the separately versioned GGB feedback producer contract and the consumer behavior, configuration, failure handling, and verification needed to turn GGB submissions into canonical WorkTrail briefs.
