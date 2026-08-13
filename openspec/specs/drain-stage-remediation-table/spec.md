## Purpose

A data-driven remediation table in `drain.py` that pairs each safe,
unattended-recoverable `detect_stage()` stage with a finder and an action, so
adding a new stall-remediation category is a one-line table entry instead of
a new hand-written function plus two call sites plus a new summary-dict key.
Deliberately excludes `orchestrator-stuck` (`fanout_failed`), which stays
human-recovery-only.
## Requirements
### Requirement: Data-driven remediation table
The system SHALL maintain a `REMEDIATION_TABLE` in `drain.py` that pairs each
safe, unattended-recoverable `detect_stage()` stage with a finder function
(returns findings for that stage across `--repos-root`) and an action
function (applies the remediation to one finding). Adding a new safe
remediation category SHALL require only a new table entry, not a new
top-level sweep function or a new call site inside `drain()`.

#### Scenario: Table drives the sweep, not hand-written per-stage loops
- **WHEN** `drain()`'s unattended sweep runs (pre-loop or post-loop pass)
- **THEN** it iterates `REMEDIATION_TABLE` once via a single generic sweep
  function, rather than calling one hardcoded function per stage

### Requirement: Per-finding failure isolation
The system SHALL catch any exception raised by a remediation action for one
finding and continue processing the remaining findings — in that stage and
in every other table entry — rather than aborting the sweep.

#### Scenario: One finding's remediation fails
- **WHEN** a remediation action raises for one finding (e.g. `gh pr create`
  fails, or the spawned process errors)
- **THEN** the sweep logs the failure with the stage's log label and the
  finding's repo/spec id, and continues to the next finding and the next
  table entry

### Requirement: `orchestrator-stuck` is never auto-remediated
The system SHALL NOT include the `orchestrator-stuck` (`fanout_failed`) stage
in `REMEDIATION_TABLE`.

#### Scenario: A spec is stuck with a fanout_failed sidecar
- **WHEN** `detect_stage()` reports a spec's stage as `orchestrator-stuck`
- **THEN** no table entry matches it and the unattended sweep takes no
  action on that spec — it remains visible on the dashboard for human
  recovery, exactly as before this change

### Requirement: Stale-bookkeeping remediation
The system SHALL detect specs in the `stale-bookkeeping` stage across
`--repos-root` and, for each, flip the affected task(s) `status:` to
`completed` and open a docs-only pull request, without invoking the
orchestrator.

#### Scenario: A spec has one or more stale-bookkeeping tasks
- **WHEN** `detect_stage()` reports a spec's stage as `stale-bookkeeping`
  with one or more stale task ids
- **THEN** the sweep flips each stale task's `status:` frontmatter field to
  `completed` via the existing devkit task-status-completion mechanism,
  commits the change on a short-lived branch, and opens a pull request
  carrying the `go:risk-low` label

#### Scenario: No stale-bookkeeping specs found
- **WHEN** no repo under `--repos-root` currently reports the
  `stale-bookkeeping` stage
- **THEN** the sweep performs no git or PR operations for this remediation
  category and the summary's `resumed_stale_bookkeeping` key is an empty list

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

