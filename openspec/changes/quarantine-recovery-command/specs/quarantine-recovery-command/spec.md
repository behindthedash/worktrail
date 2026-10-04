# quarantine-recovery-command Specification

## Purpose

Compose the documented manual quarantine-recovery loop — repair the retained task branch
against current base, clear the failed/escalated journal entries and the QUARANTINED group
record, then resume — into one bounded, re-runnable command that fails closed and never leaves
the journal half-repaired.

## ADDED Requirements

### Requirement: One command composes branch repair and journal recovery
The system SHALL provide a `worktrail-live recover` subcommand taking a repo, a spec/change
identifier, and at least one explicit selection (`--group` name(s) and/or `--tasks` ids). In
one invocation it SHALL validate the selection against the run journal, repair the retained
task branches of the selected failed/escalated tasks against the current base, clear the
selected failed/escalated journal entries and the selected groups' QUARANTINED records, and
print the `worktrail-live full-real --resume` invocation for the same repo and spec that
re-integrates them, carrying the base and remote the recovery resolved. It SHALL NOT launch a
live run, and it SHALL leave every journal record outside the selection unmodified.

#### Scenario: The incident-shaped recovery is one command
- **WHEN** a run journal records group `feature-1` as QUARANTINED with failed task `1.1`, whose
  retained branch predates the current base, and `worktrail-live recover --repo <repo> --spec
  <spec> --group feature-1` runs
- **THEN** the retained branch is merged up to date, the failed entry and the group record are
  cleared in one write, and the output contains the `worktrail-live full-real --resume`
  invocation for that repo and spec

#### Scenario: No selection is refused
- **WHEN** recovery runs with neither `--group` nor `--tasks`
- **THEN** nothing is modified and the command exits non-zero naming the missing selection

#### Scenario: Outside the selection nothing changes
- **WHEN** the journal holds records and entries for groups and tasks outside the selection
- **THEN** after recovery those records and entries are unchanged

#### Scenario: The command does not launch the resume
- **WHEN** recovery succeeds
- **THEN** the output names the next `full-real --resume` invocation and no live run is started

### Requirement: Retained task branches are merged up to date with the current base
For every selected task with a failed or escalated journal entry that has a retained branch,
the system SHALL fetch the base branch and decide whether that branch already contains the
current base tip. When it does not, the system SHALL merge the current base into the retained
branch, so the branch contains the base tip, before any journal mutation, and SHALL NOT push.
When the task has no retained branch, the system SHALL report that there is nothing to repair
for it and continue. When the branch's checkout has uncommitted changes, the system SHALL
refuse without merging and without journal mutation.

#### Scenario: A stale retained branch is merged up to date
- **WHEN** a selected task's retained branch carries its own work and was forked before commits
  now on the base
- **THEN** the command merges the current base into that branch, leaving a merge commit on it,
  and the recovery continues

#### Scenario: A branch that already contains base is not merged
- **WHEN** the current base tip is already an ancestor of the retained branch
- **THEN** no merge commit is created for that branch and the recovery continues

#### Scenario: No retained branch to repair
- **WHEN** a selected task has a failed or escalated entry but no retained branch exists
- **THEN** the command reports that nothing was repaired for it and continues

#### Scenario: Uncommitted work refuses the repair
- **WHEN** the retained branch's checkout has uncommitted changes
- **THEN** the command merges nothing, changes no journal state, and exits non-zero naming the
  checkout

### Requirement: A conflicting branch repair fails closed and names the conflicted paths
When merging the current base into a retained branch conflicts, the system SHALL abort the
merge so the branch is left unchanged, report the exact conflicted paths, change no journal
state, and exit non-zero. A repair that already succeeded stays applied. A later re-run SHALL
proceed once the branch contains the base.

#### Scenario: Conflict surfaces the files and clears nothing
- **WHEN** merging the current base into a retained branch conflicts on one path
- **THEN** the merge is aborted, the output names that exact path, the run journal is unchanged,
  and the command exits non-zero

#### Scenario: Hand-repaired branch then recovers
- **WHEN** the branch's conflict is resolved by hand and the same recovery is re-run
- **THEN** the branch is found to contain the base, and the journal clearing proceeds

### Requirement: The journal is written once, only after every repair succeeds
The system SHALL apply all journal changes in memory and write the run journal with a single
atomic write, only after every selected branch repair has succeeded. Immediately before the
write it SHALL re-check the journal's RunLock and SHALL refuse the write, leaving the journal
unchanged, when a live run holds it. When a repair fails or the selection is refused, the
journal SHALL be left unchanged. `--dry-run` SHALL run no merges and write nothing, while still
reporting the selection and which retained branches would need repair.

#### Scenario: A failed repair leaves the journal untouched
- **WHEN** one selected branch's repair conflicts after another's was merged cleanly
- **THEN** no journal entry or group record is cleared, the journal is unchanged, and the
  already-merged branch keeps its merge commit

#### Scenario: A live run blocks the write
- **WHEN** a live run acquires the journal's RunLock while recovery is repairing branches
- **THEN** the journal is not written, the command reports the live run, and it exits non-zero

#### Scenario: Dry run
- **WHEN** recovery runs with `--dry-run` against a recoverable selection
- **THEN** no merge commit is created, the journal is unchanged, and the output names the groups
  and tasks that would be recovered

### Requirement: Recovery refuses what the commands it composes refuse
Recovery SHALL preserve the guardrails of the commands it composes: a named group with no
journal record or a state other than QUARANTINED is refused; a named task with no
failed/escalated entries is refused; a task holding a completion record is never cleared; and a
missing, unreadable, or non-object run journal is refused. Every refusal SHALL leave the
journal unchanged and exit non-zero.

#### Scenario: Named group is not quarantined
- **WHEN** `--group` names a group whose record state is MERGED or OPEN
- **THEN** nothing is repaired or cleared and the command exits non-zero

#### Scenario: Named task has nothing to clear
- **WHEN** `--tasks` names a task with no failed or escalated entries
- **THEN** the command refuses naming that task and leaves the journal unchanged

#### Scenario: Completion records are never dropped
- **WHEN** a selected task holds a successful completion entry
- **THEN** the command refuses and leaves the journal unchanged

### Requirement: A group selection resolves to the group's tasks from the cached RunPlan
For a `--group` selection the system SHALL derive the group's task ids from the spec's cached
RunPlan (the existing `quarantine_selfcheck.group_task_ids` resolution) and recover the group's
failed/escalated tasks. When that resolution returns nothing — the cache is missing or
unreadable, or the group is no longer in the plan — the system SHALL refuse rather than guess
which tasks belong to the group. A selected group whose tasks have no failed/escalated entries
SHALL still have its QUARANTINED record cleared.

#### Scenario: Group tasks are recovered from the plan
- **WHEN** a QUARANTINED group contains one failed task and two done tasks
- **THEN** the failed task's retained branch is repaired and its entry cleared, the done tasks
  are left alone, and the group record is cleared

#### Scenario: No plan resolution means no guessing
- **WHEN** the cached RunPlan cannot resolve the named group's tasks
- **THEN** the command refuses, names the group it could not resolve, and leaves the journal
  unchanged

#### Scenario: A delivered-but-quarantined group needs no repair
- **WHEN** every task of the selected group has already succeeded
- **THEN** the group record is cleared in one write and no branch is touched

### Requirement: Operator references point at the composed recovery command
The Route E guidance for quarantined orchestrator groups SHALL cite `worktrail-live recover` as
the composed entry point for returning a group to the pipeline, keeping the existing
`worktrail-resume-group` and `worktrail-live full-real --resume` mentions valid.

#### Scenario: Route E names the composed command
- **WHEN** the Route E reference describes recovering a quarantined orchestrator group
- **THEN** it names `worktrail-live recover`
