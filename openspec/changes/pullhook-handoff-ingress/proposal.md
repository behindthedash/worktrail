## Why

WorkTrail's handoff queue is intentionally local: `worktrail-handoff` writes canonical briefs under `$WORK_QUEUE_DIR`, and the queue may optionally be a private git repository. That works for local agents but leaves CI systems such as Datalena with no safe remote intake path.

PullHook already supplies the missing transport: authenticated channels, durable SQLite storage, idempotent publish, claim/ack lifecycle, and pull-based consumption. WorkTrail needs a consumer bridge that retrieves accepted-work envelopes and materializes them through the existing `create_handoff`/CLI contract.

## What Changes

- Add a `worktrail-pullhook-ingress` command that consumes a configured PullHook channel.
- Validate supported event schemas before any queue mutation; initially support `datalena.worktrail-handoff.v1`.
- Claim an item, map the envelope into canonical `create_handoff()` arguments, and stamp `captured-by` from the producer.
- Preserve external event/dedupe/source provenance in the handoff body/artifacts so a queue item can be traced back to its merged source PR.
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

- New WorkTrail CLI/module and tests.
- Local configuration/secrets for PullHook consume credential/channel.
- Optional git-backed `$WORK_QUEUE_DIR` becomes the durable off-machine record for externally ingested briefs.
- No requirement for producer repositories to install WorkTrail or hold work-queue GitHub credentials.

## Folded from 20260920-182427-pullhook-handoff-ingress-unimplemented

Implement the merged OpenSpec change worktrail/openspec/changes/pullhook-handoff-ingress (spec PR #1273): the worktrail-pullhook-ingress command that pulls datalena.worktrail-handoff.v1 events from a PullHook channel and materializes them as canonical handoff briefs. All tasks 1.1-8.4 are unchecked; no code exists. Datalena's publisher (datalena PR #2962, merged 2026-09-21) already posts to channel datalena-worktrail on pullhook.io, so events will accumulate unread until this ships.

`gh repo view` confirms behindthedash/worktrail is not archived. `openspec/changes/pullhook-handoff-ingress/tasks.md` exists with 11 unchecked tasks and 0 checked (confirmed via `grep -c` for `- [ ]` / `- [x]`), and `ls src/worktrail/workqueue/ | grep -i pullhook` returns nothing, so task 1.1's `src/worktrail/workqueue/pullhook_client.py` is absent — the brief's premise holds and its work is exactly this change's scope. The brief's path string `worktrail/openspec/changes/pullhook-handoff-ingress` is just repo-prefixed; the change resolves at `openspec/changes/pullhook-handoff-ingress`.
