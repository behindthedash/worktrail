## ADDED Requirements

### Requirement: OpenSpec change archive remediation

The system SHALL detect OpenSpec changes reported at the `complete` stage by
the common dashboard scan (`format == "openspec"`, `stage == "complete"` —
already the signal for "all tasks completed, code merged, delta reconciled
into `openspec/specs/`") across `--repos-root` and, for each, run
`openspec archive -y <change-id>` in a short-lived worktree, commit the
resulting archive move, and open a docs-only pull request carrying the
`go:risk-low` label.

The finder SHALL restrict matches to `format == "openspec"`. A devkit-format
spec reported at `stage == "complete"` carries a different meaning ("open PR
/ sync — verify merge state", not "ready to archive") and SHALL NOT be
selected by this remediation.

#### Scenario: An OpenSpec change is at the complete stage
- **WHEN** the common dashboard scan reports an OpenSpec change as
  `stage == "complete"`
- **THEN** the sweep runs `openspec archive -y <change-id>` in a short-lived
  worktree off the repo's base branch, commits the archive move, pushes the
  branch, and opens a docs-only pull request labeled `go:risk-low`

#### Scenario: No complete OpenSpec changes found
- **WHEN** no repo under `--repos-root` currently reports an OpenSpec change
  at `stage == "complete"`
- **THEN** the sweep performs no archive operation and the summary's
  `resumed_openspec_archive` key is an empty list

#### Scenario: A devkit spec at the complete stage is not archived
- **WHEN** the common dashboard scan reports a devkit-format spec as
  `stage == "complete"`
- **THEN** the archive remediation's finder does not select that spec

#### Scenario: Already-open archive PR is not re-attempted
- **WHEN** a prior sweep already opened an archive pull request for a
  change's archive branch and it has not yet merged
- **THEN** the next sweep detects the existing open PR and returns it as-is
  rather than re-running `openspec archive`

## MODIFIED Requirements

### Requirement: Backward-compatible summary dict
`drain()`'s returned summary dict SHALL continue to include the
`resumed_quarantines`, `resumed_verify_pending`, `resumed_stale_bookkeeping`,
and `resumed_sync_pending` keys with their existing shape, and SHALL
additionally include a `resumed_openspec_archive` key with the same
list-of-result-dict shape as the other four.

#### Scenario: Summary dict after a sweep with all three categories present
- **WHEN** `drain()` completes a run in which findings existed for all three
  remediation categories
- **THEN** the returned summary dict contains non-empty
  `resumed_quarantines`, `resumed_verify_pending`, and
  `resumed_stale_bookkeeping` lists, each shaped like the existing two keys'
  result dicts

#### Scenario: Summary dict after a sweep with all four categories present
- **WHEN** `drain()` completes a run in which findings existed for all four
  prior remediation categories
- **THEN** the returned summary dict contains non-empty
  `resumed_quarantines`, `resumed_verify_pending`,
  `resumed_stale_bookkeeping`, and `resumed_sync_pending` lists, each
  shaped like the existing three keys' result dicts

#### Scenario: Summary dict after a sweep with all five categories present
- **WHEN** `drain()` completes a run in which findings existed for all five
  remediation categories
- **THEN** the returned summary dict contains non-empty
  `resumed_quarantines`, `resumed_verify_pending`,
  `resumed_stale_bookkeeping`, `resumed_sync_pending`, and
  `resumed_openspec_archive` lists, each shaped like the existing four keys'
  result dicts
