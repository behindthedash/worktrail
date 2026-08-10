## MODIFIED Requirements

### Requirement: Sync-pending remediation

The system SHALL detect devkit specs and active OpenSpec changes in the
`sync-pending` stage across `--repos-root` and, for each, spawn a headless
one-shot agent run of `/opsx:sync <spec_id>` to reconcile the spec against
merged code.

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

