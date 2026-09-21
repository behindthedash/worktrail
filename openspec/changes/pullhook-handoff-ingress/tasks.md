## 1. PullHook client

- [x] 1.1 In `src/worktrail/workqueue/pullhook_client.py`, add a minimal PullHook client
      for claim, peek, and ack using stdlib/available HTTP dependencies, a bearer consume
      credential, a bounded timeout, and secret-safe errors (the credential never appears in
      an exception message or log line). In `tests/workqueue/test_pullhook_client.py`, cover
      claim, peek without claim, ack, timeout, and credential redaction.
      (Requirement: WorkTrail consumes external handoff events by pulling.)
      files: src/worktrail/workqueue/pullhook_client.py, tests/workqueue/test_pullhook_client.py

## 2. Envelope validation and mapping

- [x] 2.1 In `src/worktrail/workqueue/pullhook_envelope.py`, define the strict allowlisted
      adapter for `datalena.worktrail-handoff.v1`: reject unsupported schema versions and
      missing identity/target/focus provenance before any queue mutation, and map a valid
      envelope to `create_handoff()` arguments, including `captured_by`, repo/remote/base
      branch, context, approach, artifacts (carrying the external event, source repository,
      merged PR, and source finding/evidence references), and implementation intent. In
      `tests/workqueue/test_pullhook_envelope.py`, cover a valid v1 envelope, an unknown
      schema, each missing required field, and provenance retention in the mapped arguments.
      (Requirements: Only supported versioned envelopes are materialized; Materialization
      uses WorkTrail's canonical handoff writer; External provenance is retained.)
      files: src/worktrail/workqueue/pullhook_envelope.py, tests/workqueue/test_pullhook_envelope.py

## 3. External-event materialization record

- [x] 3.1 In `src/worktrail/workqueue/external_events.py`, add a durable external-event
      materialization record keyed by schema + event/dedupe identity that stores the
      resulting handoff ID/path in WorkTrail-owned queue metadata. It must survive process
      restart and be safe to inspect before creation. In
      `tests/workqueue/test_external_events.py`, cover lookup-before-create, record, restart
      survival, and distinct keys for distinct schemas with the same event id.
      (Requirement: Redelivery cannot create duplicate handoffs.)
      files: src/worktrail/workqueue/external_events.py, tests/workqueue/test_external_events.py

## 4. Git-backed work-queue durability

- [ ] 4.1 In `src/worktrail/workqueue/queue_git_persist.py`, add a narrow helper for newly
      captured external briefs that, when queue git sync is enabled, stages only the created
      brief plus the WorkTrail-owned materialization metadata, commits with the event id, and
      pushes without pulling. Failure to push is reported to the caller. When `$WORK_QUEUE_DIR`
      is not a git repo, local durable creation remains valid unless configuration explicitly
      requires git persistence. In `tests/workqueue/test_queue_git_persist.py`, cover a
      successful commit+push, a push failure, a retry that completes persistence
      idempotently, and the non-git queue directory.
      (Requirement: Relay acknowledgement follows durable queue persistence.)
      files: src/worktrail/workqueue/queue_git_persist.py, tests/workqueue/test_queue_git_persist.py

## 5. Exactly-once materialization

- [ ] 5.1 In `src/worktrail/workqueue/pullhook_ingress.py`, implement the sequence claim ->
      validate -> dedupe -> create through `create_handoff()` -> record marker -> git persist
      -> ack, composing the client, envelope adapter, external-event record, and git helper.
      A redelivered event whose marker exists returns the original handoff and creates no new
      file. Ack only after required git persistence succeeds; on push failure return nonzero
      and do not ack, so a retry completes persistence idempotently. Add a dry-run mode that
      inspects one claimed/peeked envelope without creating a brief or acknowledging it. In
      `tests/workqueue/test_pullhook_ingress.py`, cover the crash/retry boundaries (before
      create, after create/before marker, after marker/before ack, after ack response loss),
      redelivery of a materialized event, push failure without ack, and dry-run creating and
      acking nothing.
      (Requirements: Redelivery cannot create duplicate handoffs; Relay acknowledgement
      follows durable queue persistence; Materialization uses WorkTrail's canonical handoff
      writer.)
      files: src/worktrail/workqueue/pullhook_ingress.py, tests/workqueue/test_pullhook_ingress.py
      depends: 1.1, 2.1, 3.1, 4.1

## 6. CLI and unattended retrieval

- [ ] 6.1 In `src/worktrail/workqueue/pullhook_ingress_cli.py`, add the
      `worktrail-pullhook-ingress` entry point with `--base-url`, `--channel`, the consume
      credential read from the environment (never accepted as a logged positional argument),
      `--queue-dir`, `--once`, bounded batch/drain options, `--dry-run`, and JSON output.
      Register it in `pyproject.toml`. In `tests/workqueue/test_pullhook_ingress_cli.py`,
      cover argument parsing, credential-from-environment only, `--once`, batch bounds, and
      JSON output.
      (Requirement: WorkTrail consumes external handoff events by pulling.)
      files: src/worktrail/workqueue/pullhook_ingress_cli.py, pyproject.toml, tests/workqueue/test_pullhook_ingress_cli.py
      depends: 5.1

## 7. Documentation

- [ ] 7.1 [docs] In `README.md`, add operator documentation showing a local
      cron/systemd/scheduler invocation of `worktrail-pullhook-ingress` and the complete
      Datalena -> PullHook -> WorkTrail -> work-queue path.
      files: README.md
      depends: 6.1

## 8. Verification

- [ ] 8.1 [e2e] Run the focused WorkTrail unit tests, then `PYTHONPATH=src pytest -q`,
      `ruff check .` and `ruff format --check .`, and
      `openspec validate pullhook-handoff-ingress --strict`.

- [ ] 8.2 [e2e] Against a test PullHook channel and a temporary git-backed queue, publish
      one Datalena fixture, run ingress, and verify one canonical brief + one materialization
      record + a successful push + an ack.

- [ ] 8.3 [e2e] Redeliver/replay the same event and verify no second brief is created and
      the original handoff ID is returned.

- [ ] 8.4 [e2e] Simulate a push failure and verify the relay item is not acknowledged and a
      retry completes without duplicate brief creation.

## 9. Folded from 20260920-182427-pullhook-handoff-ingress-unimplemented

Triage evidence for this fold is in `proposal.md`'s `## Folded from 20260920-182427-pullhook-handoff-ingress-unimplemented` section.

- [ ] 9.1 Implement the merged OpenSpec change worktrail/openspec/changes/pullhook-handoff-ingress (spec PR #1273): the worktrail-pullhook-ingress command that pulls datalena.worktrail-handoff.v1 events from a PullHook channel and materializes them as canonical handoff briefs.
      files: openspec/changes/pullhook-handoff-ingress/tasks.md
