## MODIFIED Requirements

### Requirement: Sync-pending remediation

The system SHALL detect devkit specs and active OpenSpec changes in the
`sync-pending` stage across `--repos-root` and, for each, spawn a headless
one-shot agent run of `/opsx:sync <spec_id>` to reconcile the spec against
merged code.

#### Scenario: A spec is in the sync-pending stage
- **WHEN** `detect_stage()` reports a spec's stage as `sync-pending`
- **THEN** the sweep spawns a one-shot agent CLI invocation of
  `/opsx:sync <spec_id>` for that spec and records the spawn's exit code

#### Scenario: No sync-pending specs found
- **WHEN** no repo under `--repos-root` currently reports the
  `sync-pending` stage
- **THEN** the sweep performs no spawn for this remediation category and
  the summary's `resumed_sync_pending` key is an empty list

#### Scenario: OpenSpec sync-pending finding
- **WHEN** the common dashboard scan reports an active OpenSpec change as
  `sync-pending`
- **THEN** the existing remediation row resolves its path as
  `openspec/changes/<change-id>` and dispatches `/opsx:sync <change-id>`

#### Scenario: Reconciled OpenSpec change is not repeated
- **WHEN** a prior sync has made every declared structural delta visible in the
  canonical capability specs
- **THEN** the next drain sweep does not return that change as a sync-pending
  finding

