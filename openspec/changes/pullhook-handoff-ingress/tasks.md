## 1. PullHook client and envelope validation

- [ ] 1.1 Add a minimal PullHook client for claim and ack using stdlib/available HTTP dependencies, bearer consume credential, bounded timeout, and secret-safe errors. files: `src/worktrail/workqueue/pullhook_ingress.py`, tests
- [ ] 1.2 Define strict adapter/validation for `datalena.worktrail-handoff.v1`; reject unsupported schema versions and missing identity/target/focus provenance before queue mutation. files: ingress module/tests
- [ ] 1.3 Map a valid envelope to `create_handoff()` arguments, including `captured_by`, repo/remote/base branch, context, approach, artifacts, and implementation intent. files: ingress module/tests

## 2. Consumer-side exactly-once materialization

- [ ] 2.1 Add a durable external-event materialization record keyed by schema + event/dedupe identity and storing resulting handoff ID/path. It must survive process restart and be safe to inspect before creation. files: WorkTrail queue metadata helper + tests
- [ ] 2.2 Implement claim -> validate -> dedupe -> create -> marker sequencing. A redelivered event whose marker exists returns the original handoff and does not create a new file. files: ingress module/tests
- [ ] 2.3 Cover crash/retry boundaries in tests: before create, after create/before marker, after marker/before ack, and after ack response loss. Reconciliation must not duplicate a handoff. files: tests

## 3. Git-backed work-queue durability

- [ ] 3.1 Add a narrow helper for newly captured external briefs that, when queue git sync is enabled, stages only the created brief plus WorkTrail-owned materialization metadata, commits with the event id, and pushes without pulling. files: ingress/git helper/tests
- [ ] 3.2 Ack PullHook only after required git persistence succeeds. On push failure, return nonzero/no ack; retry completes persistence idempotently. files: ingress module/tests
- [ ] 3.3 Verify behavior when `$WORK_QUEUE_DIR` is not a git repo: local durable creation remains valid unless configuration explicitly requires git persistence. files: tests

## 4. CLI and unattended retrieval

- [ ] 4.1 Register `worktrail-pullhook-ingress` in `pyproject.toml` with `--base-url`, `--channel`, consume credential via environment, `--queue-dir`, `--once`, bounded batch/drain options, and JSON output. Never accept a credential in a logged positional argument. files: `pyproject.toml`, ingress module/tests
- [ ] 4.2 Add operator documentation showing a local cron/systemd/scheduler invocation and the complete Datalena -> PullHook -> WorkTrail -> work-queue path. files: README/skill reference
- [ ] 4.3 Add `--dry-run`/validation mode that can inspect one claimed/peeked envelope without creating a brief or acknowledging it. files: ingress module/tests

## 5. Verification

- [ ] 5.1 Run focused WorkTrail unit tests, full `PYTHONPATH=src pytest -q`, formatting/lint, and `openspec validate pullhook-handoff-ingress --strict`.
- [ ] 5.2 Against a test PullHook channel and temporary git-backed queue, publish one Datalena fixture, run ingress, verify one canonical brief + one materialization record + successful push + ack.
- [ ] 5.3 Redeliver/replay the same event and verify no second brief is created and the original handoff ID is returned.
- [ ] 5.4 Simulate push failure and verify the relay item is not acknowledged and a retry completes without duplicate brief creation.
