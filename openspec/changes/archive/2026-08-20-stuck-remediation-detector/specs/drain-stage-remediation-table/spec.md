## ADDED Requirements

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

## MODIFIED Requirements

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
