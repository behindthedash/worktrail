# quarantined-group-resume Specification

## Purpose

Provide explicit, audited recovery of quarantined orchestrator groups while preserving unrelated group records and refusing edits to a journal owned by a live run.

## Requirements

### Requirement: A named quarantined group can be returned to the resume path
The system SHALL provide a `worktrail-resume-group` command that, given a repo, a spec/change
identifier, and one or more group names, removes each named group's record from the run
journal's `groups` map and removes the journal's `integrate_complete` marker, so the next
`worktrail-live full-real --resume` re-integrates and re-verifies that group through the
ordinary path. Records for groups that were not named SHALL be left byte-for-byte unchanged,
including `MERGED` records.

#### Scenario: Quarantined group cleared
- **WHEN** the command is run for a group whose journal record has `state: "QUARANTINED"`
- **THEN** that group's entry is removed from `groups`, `integrate_complete` is removed from
  the journal, and the command exits 0

#### Scenario: Other groups untouched
- **WHEN** a journal holds a `MERGED` group, an `OPEN` group, and the named `QUARANTINED` group
- **THEN** only the named group's entry is removed and the `MERGED` and `OPEN` entries are
  unchanged

#### Scenario: Group is not quarantined
- **WHEN** the named group's record has a state other than `QUARANTINED`
- **THEN** the journal is not modified, the command reports the group's actual state, and it
  exits non-zero

#### Scenario: Group or journal is absent
- **WHEN** the named group has no journal record, or no run journal exists for the given spec
- **THEN** the journal is not created or modified, the command reports what was missing, and it
  exits non-zero

### Requirement: Budget-exhausted quarantines can be cleared in bulk
The system SHALL accept an `--all-resumable` mode that selects every group in the journal whose
`state` is `QUARANTINED` and whose `quarantine_reason` is `budget_exhausted`
(`integrate.QUARANTINE_BUDGET_EXHAUSTED`). Groups quarantined for any other reason SHALL NOT be
selected by this mode and SHALL only be cleared when named explicitly.

#### Scenario: Only budget-exhausted groups are selected
- **WHEN** `--all-resumable` runs against a journal with one `budget_exhausted` group and one
  `merge_conflict` group, both `QUARANTINED`
- **THEN** the `budget_exhausted` group's record is removed and the `merge_conflict` group's
  record is left in place

#### Scenario: Nothing is resumable
- **WHEN** `--all-resumable` finds no `budget_exhausted` quarantined group
- **THEN** the journal is not modified and the command reports that there was nothing to clear

### Requirement: The prior quarantine is recorded, not discarded
When a group's record is cleared, the system SHALL append an entry to the journal's
`resumed_quarantines` list carrying at least the group name, the cleared record's
`quarantine_reason`, its `quarantine_detail`, its `pr_url`, and a `cleared_at` timestamp.
Existing `resumed_quarantines` entries SHALL be preserved, so a group cleared twice has two
entries.

#### Scenario: Audit entry written
- **WHEN** a group whose record carries `quarantine_reason: "merge_conflict"` is cleared
- **THEN** the journal's `resumed_quarantines` contains an entry naming that group and
  `merge_conflict`

#### Scenario: Repeat recovery appends
- **WHEN** a group is quarantined and cleared a second time
- **THEN** `resumed_quarantines` holds both the earlier and the new entry

### Requirement: The command refuses to edit a journal a live run owns
The system SHALL check the journal's RunLock before writing and, when a live process holds it,
SHALL leave the journal unmodified, report that a run is in progress, and exit non-zero.

#### Scenario: Live run holds the lock
- **WHEN** the journal's lock file is held by a live process
- **THEN** no group record is removed and the command exits non-zero

### Requirement: The next step is reported and dry-run writes nothing
On success the system SHALL print the names of the cleared groups and the
`worktrail-live full-real --resume` invocation that re-integrates them. In `--dry-run` mode the
system SHALL report exactly the same selection without modifying the journal.

#### Scenario: Resume invocation printed
- **WHEN** one or more groups are cleared
- **THEN** the output names each cleared group and contains the `full-real --resume` command
  for that repo and spec

#### Scenario: Dry run
- **WHEN** the command runs with `--dry-run` against a clearable group
- **THEN** the journal file's contents are unchanged and the output names the group that would
  be cleared

### Requirement: Quarantine triage points at the recovery command
`worktrail-quarantine-selfcheck`'s human-readable output SHALL name `worktrail-resume-group` as
the recovery action for the quarantined groups it reports, and the `worktrail-go` Route E
reference SHALL cite the same command for quarantined orchestrator groups. The selfcheck's
findings/reconciled/resumable classification and its exit code SHALL be unchanged.

#### Scenario: Selfcheck output names the command
- **WHEN** `worktrail-quarantine-selfcheck` reports a quarantined group in non-JSON mode
- **THEN** the output includes `worktrail-resume-group`

#### Scenario: Classification unchanged
- **WHEN** the selfcheck runs against a journal with a reconcilable, a resumable, and a
  human-triage quarantine
- **THEN** each is classified exactly as before this change and the exit code is unchanged
