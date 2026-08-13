## ADDED Requirements

### Requirement: Sync-pending remediation
The system SHALL detect specs in the `sync-pending` stage across
`--repos-root` and, for each, spawn a headless one-shot agent run of
`/opsx:sync <spec_id>` to reconcile the spec against merged code.

#### Scenario: A spec is in the sync-pending stage
- **WHEN** `detect_stage()` reports a spec's stage as `sync-pending`
- **THEN** the sweep spawns a one-shot agent CLI invocation of
  `/opsx:sync <spec_id>` for that spec and records the spawn's exit code

#### Scenario: No sync-pending specs found
- **WHEN** no repo under `--repos-root` currently reports the
  `sync-pending` stage
- **THEN** the sweep performs no spawn for this remediation category and
  the summary's `resumed_sync_pending` key is an empty list

## MODIFIED Requirements

### Requirement: Backward-compatible summary dict
`drain()`'s returned summary dict SHALL continue to include the
`resumed_quarantines`, `resumed_verify_pending`, and
`resumed_stale_bookkeeping` keys with their existing shape, and SHALL
additionally include a `resumed_sync_pending` key with the same
list-of-result-dict shape as the other three.

#### Scenario: Summary dict after a sweep with all three categories present
- **WHEN** `drain()` completes a run in which findings existed for all three
  remediation categories
- **THEN** the returned summary dict contains non-empty
  `resumed_quarantines`, `resumed_verify_pending`, and
  `resumed_stale_bookkeeping` lists, each shaped like the existing two keys'
  result dicts

#### Scenario: Summary dict after a sweep with all four categories present
- **WHEN** `drain()` completes a run in which findings existed for all four
  remediation categories
- **THEN** the returned summary dict contains non-empty
  `resumed_quarantines`, `resumed_verify_pending`,
  `resumed_stale_bookkeeping`, and `resumed_sync_pending` lists, each
  shaped like the existing three keys' result dicts
