## ADDED Requirements

### Requirement: A run's group records survive a later phase's journal rewrite
The system SHALL preserve the run journal's `groups` map and its `integrate_complete` marker
across any journal write performed by a later phase of the same run. A group quarantined
during the pipeline phase SHALL still carry its QUARANTINED record after the tail phase has
written the journal, so `worktrail-resume-group` can name and clear it once the run has
completed. A phase SHALL still record the state of any group it resolves itself, so this is a
carry-forward of another writer's records rather than a freeze of the whole map.

#### Scenario: A quarantined group is still clearable after the run completes
- **WHEN** a run quarantines a group during its pipeline phase and then executes its tail phase
- **THEN** the completed journal still carries that group's QUARANTINED record, and
  `worktrail-resume-group` names that group and clears it

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
