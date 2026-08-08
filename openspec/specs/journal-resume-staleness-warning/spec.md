# journal-resume-staleness-warning Specification

## Purpose
TBD - created by archiving change journal-resume-staleness-warning. Update Purpose after archive.
## Requirements
### Requirement: Warn on resume of a journal with stale quarantined groups

When `full_real` (pipeline scheduler) resumes an existing run journal
(`resume=True`, journal file present) and the reconciled journal's `groups`
records include one or more groups with `state == "QUARANTINED"`, the
system SHALL compute, for each such group, how many commits `base` has
advanced since that group's task branches were originally forked from
`base`. When that count is non-zero for a group, the system SHALL print an
explicit warning naming the group, the commit count, and recommending a
`--fresh` re-run, before the run reaches `=== PIPELINE RUN COMPLETE ===`.

#### Scenario: Quarantined group, base has moved

- **WHEN** `full_real` resumes a journal whose `groups` record contains a
  group `feature-1` with `state: "QUARANTINED"`, and `base` has advanced 3
  commits since `feature-1`'s task branch was forked from `base`
- **THEN** the run prints a warning naming `feature-1`, stating the base has
  moved 3 commit(s) since that group's branch was forked, and recommending
  `--fresh`, before printing `=== PIPELINE RUN COMPLETE ===`

#### Scenario: Quarantined group, base unchanged

- **WHEN** `full_real` resumes a journal whose `groups` record contains a
  `QUARANTINED` group whose task branch's fork point is identical to the
  current `base` HEAD (zero commits of drift)
- **THEN** the run SHALL NOT print the staleness warning for that group

#### Scenario: No quarantined groups

- **WHEN** `full_real` resumes a journal whose `groups` record contains no
  group with `state == "QUARANTINED"`
- **THEN** the run SHALL NOT print the staleness warning (existing resume
  output, including `_resume_drift_report`'s generic drift line, is
  unaffected)

#### Scenario: Fresh run (no resume)

- **WHEN** `full_real` runs with `--fresh` (no journal reconciliation) or
  with no prior journal file present
- **THEN** the staleness warning SHALL NOT be evaluated or printed

#### Scenario: Branch lookup fails for a quarantined group

- **WHEN** a `QUARANTINED` group's task branch cannot be resolved (deleted,
  or the `git merge-base`/`git rev-list` calls fail)
- **THEN** the system SHALL skip the staleness warning for that group
  without raising an error and SHALL NOT block or fail the resume

