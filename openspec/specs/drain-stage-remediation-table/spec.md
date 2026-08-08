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
`resumed_quarantines` and `resumed_verify_pending` keys with their existing
shape, and SHALL additionally include a `resumed_stale_bookkeeping` key with
the same list-of-result-dict shape as the other two.

#### Scenario: Summary dict after a sweep with all three categories present
- **WHEN** `drain()` completes a run in which findings existed for all three
  remediation categories
- **THEN** the returned summary dict contains non-empty
  `resumed_quarantines`, `resumed_verify_pending`, and
  `resumed_stale_bookkeeping` lists, each shaped like the existing two keys'
  result dicts
