## MODIFIED Requirements

### Requirement: A run's group records survive a later phase's journal rewrite
The system SHALL preserve the run journal's `groups` map and its `integrate_complete` marker
across every journal write performed after a group record is first persisted -- across any later
phase of the run, across any later invocation of the run that shares that journal, and through
to the run's final write -- and not only across the tail phase's rewrite. A group quarantined
during the pipeline phase SHALL still carry its QUARANTINED record in the journal the run leaves
behind, so `worktrail-resume-group` can name and clear it once the run has completed. A writer
that rebuilds the journal dict from scratch rather than extending the journal on disk SHALL
carry those records forward explicitly, so no wholesale rebuild drops a record for a group it did
not itself resolve or clear; a writer that builds a `groups` map of its own SHALL write a map
that is a superset of the records already on disk, its own updates for the groups it touched
winning. A phase SHALL still record the state of any group it resolves itself, so this is a
carry-forward of another writer's records rather than a freeze of the whole map. A record removed
deliberately (`worktrail-resume-group`, or `--re-integrate`) SHALL NOT be restored by a later
write of the same run.

#### Scenario: A quarantined group is still clearable after the run completes
- **WHEN** a run quarantines a group during its pipeline phase and then executes its tail phase
- **THEN** the completed journal still carries that group's QUARANTINED record, and
  `worktrail-resume-group` names that group and clears it

#### Scenario: The record survives the whole run, not just the tail rewrite
- **WHEN** a run quarantines a group during its pipeline phase and then executes its tail phase,
  its post-tail reconciliation and checkbox writes, and every other journal write to completion
- **THEN** the journal the run leaves behind still carries that group's QUARANTINED record

#### Scenario: Non-quarantined group records are carried forward
- **WHEN** the pipeline phase has recorded a MERGED group and the tail phase then writes the
  journal
- **THEN** that MERGED record is still present after the write

#### Scenario: The integrate marker is not erased
- **WHEN** the pipeline phase's groups are integrated and the tail phase then writes the
  journal
- **THEN** the journal still carries its `integrate_complete` marker

#### Scenario: A later phase still records its own group state
- **WHEN** the tail phase itself resolves or updates a group
- **THEN** the journal written by that phase reflects the phase's own update, not only the
  state carried forward from the pipeline phase

#### Scenario: A wholesale rebuild carries the records forward
- **WHEN** a journal writer that does not hold the group records in memory rebuilds the journal
  dict from scratch
- **THEN** the write preserves every `groups` record and the `integrate_complete` marker already
  on disk instead of dropping the ones the writer does not know about

#### Scenario: An explicit clear is not undone
- **WHEN** a group's record has been removed from the journal by `worktrail-resume-group` (or by
  `--re-integrate`)
- **THEN** a later journal write of that run does not restore it
