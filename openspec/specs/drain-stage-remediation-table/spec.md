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
`resumed_sync_pending`, and `resumed_openspec_archive` keys with their
existing shape, and SHALL additionally include a `stuck_remediations` key
listing every identity flagged by the stuck-remediation detector during that
run.

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
  `resumed_openspec_archive` lists, each shaped like the existing keys'
  result dicts

#### Scenario: Summary dict when no identity is stuck
- **WHEN** `drain()` completes a run in which no identity crossed the
  stuck-detection threshold
- **THEN** the returned summary dict's `stuck_remediations` key is an empty
  list

#### Scenario: Summary dict when an identity is stuck
- **WHEN** `drain()` completes a run in which one or more identities crossed
  the stuck-detection threshold
- **THEN** the returned summary dict's `stuck_remediations` key lists each
  flagged identity's remediation key, repo name, spec id, and the streak
  length that triggered the flag

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

Before invoking `openspec archive` for a finding, the system SHALL parse the
change's `tasks.md` and refuse — raising, with no `openspec archive`,
commit, push, or pull-request attempted — if any task's status is not
completed. This check is independent of the dashboard scan's `complete`
stage determination: the `openspec` CLI itself only downgrades an
incomplete-tasks failure to a stdout warning and proceeds when `-y` is
passed, so the sweep SHALL NOT rely solely on upstream stage detection
before archiving.

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

#### Scenario: A finding's tasks.md still has an unchecked task despite complete-stage detection
- **WHEN** a finding selected for archive has a `tasks.md` in which at least
  one task's status is not completed, even though the dashboard scan
  reported the change as `stage == "complete"`
- **THEN** the sweep refuses and raises before invoking `openspec archive`,
  and no commit, push, or pull request is attempted for that finding

### Requirement: Stuck-remediation detection
The system SHALL persist, across nightly sweeps, a history of every
`REMEDIATION_TABLE` finding for which the row's action completed without
raising an exception, keyed by `(remediation_key, repo_name, spec_id)`. After
each sweep, the system SHALL compare this sweep's apparently-successful
findings against the persisted history and flag any identity that recurred
for at least `stuck_threshold` (default 3) consecutive sweeps despite each of
those sweeps' action reporting apparent success.

A finding whose action raised an exception on a given sweep SHALL NOT count
toward that identity's consecutive-success streak for that sweep — an
exception is already visible via the sweep's existing per-finding error log
line, so the detector's scope is limited to the case an operator cannot
already see: apparent success that never actually resolves the underlying
finding.

#### Scenario: Same finding recurs with apparent success across the threshold
- **WHEN** a `(remediation_key, repo_name, spec_id)` identity's finder
  returns that finding, and the row's action completes without raising, on
  `stuck_threshold` consecutive sweeps
- **THEN** the sweep flags that identity as a stuck remediation on the
  `stuck_threshold`-th sweep

#### Scenario: Finding clears before reaching the threshold
- **WHEN** a `(remediation_key, repo_name, spec_id)` identity's finder stops
  returning that finding after fewer than `stuck_threshold` consecutive
  apparently-successful sweeps
- **THEN** the identity is never flagged, and its recorded streak resets once
  the finder stops returning it

#### Scenario: Action failure does not count toward the streak
- **WHEN** a `(remediation_key, repo_name, spec_id)` identity's action raises
  an exception on one of the sweeps in an otherwise-consecutive run
- **THEN** that sweep does not extend the identity's consecutive-success
  streak, and the streak count used for `stuck_threshold` comparison resets
  to zero for that identity as of that sweep

#### Scenario: Detection applies uniformly to every table row
- **WHEN** any `REMEDIATION_TABLE` row's finder returns the same finding with
  an apparently-successful action for `stuck_threshold` consecutive sweeps
- **THEN** the identity is flagged regardless of which row's `key` it belongs
  to, with no per-row detection code required

### Requirement: Stuck-remediation history retention
The system SHALL bound the persisted stuck-remediation history so it does not
grow without limit across an unbounded number of nightly runs: history
records for an identity not observed within a fixed retention window SHALL
be pruned from the persisted file.

#### Scenario: Stale identity is pruned
- **WHEN** a `(remediation_key, repo_name, spec_id)` identity has not
  appeared in any sweep's findings for longer than the retention window
- **THEN** the next sweep that writes the history file removes that
  identity's records from the persisted state

### Requirement: Stuck-remediation CLI configuration
The system SHALL expose the consecutive-sweep threshold used for stuck
detection as a `worktrail-drain` CLI flag, defaulting to 3 consecutive
sweeps when not specified.

#### Scenario: Operator overrides the threshold
- **WHEN** `worktrail-drain` is invoked with an explicit stuck-detection
  threshold flag
- **THEN** the sweep uses that threshold instead of the default of 3 when
  deciding whether to flag a recurring identity

