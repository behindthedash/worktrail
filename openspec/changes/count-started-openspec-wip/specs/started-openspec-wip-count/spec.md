## Purpose

Defines the OpenSpec work-start signal used by queue triage so repository WIP
limits throttle implementation in progress rather than parked proposals.

## ADDED Requirements

### Requirement: Started OpenSpec work is distinguished from a proposal
The system SHALL treat an OpenSpec change as started only when its `tasks.md`
contains at least one checked top-level checklist task. A change with no
`tasks.md`, no recognized task, or only unchecked tasks SHALL be treated as
unstarted for WIP-cap counting.

#### Scenario: Parked proposal has no completed task
- **WHEN** an OpenSpec change has a `proposal.md` and its `tasks.md` contains
  only unchecked top-level tasks
- **THEN** the change is not included in the repository's started-work count

#### Scenario: A completed task starts work
- **WHEN** an OpenSpec change's `tasks.md` contains at least one checked
  top-level task
- **THEN** the change is included in the repository's started-work count

#### Scenario: Planning-only change has no task list
- **WHEN** an OpenSpec change has a `proposal.md` but no `tasks.md`
- **THEN** the change is treated as unstarted for WIP-cap counting

### Requirement: WIP caps count only started OpenSpec work
When evaluating a repository's nonzero `max_active_changes` policy cap, the
system SHALL compare the cap against the count of started OpenSpec changes.
Unstarted proposals SHALL NOT cause a `propose-change` verdict to be held by
that cap.

#### Scenario: Parked proposals do not exhaust the cap
- **WHEN** a repository has a `max_active_changes` cap of 1, one unstarted
  proposal, and no started OpenSpec change
- **THEN** a `propose-change` verdict is not held by the cap

#### Scenario: Started work exhausts the cap
- **WHEN** a repository has a `max_active_changes` cap of 1 and one started
  OpenSpec change
- **THEN** a `propose-change` verdict is held by the cap

### Requirement: Unstarted proposals remain overlap candidates
The system SHALL continue to include every OpenSpec change with a readable
`proposal.md` in overlap scans and fold-candidate ranking, whether or not the
change has started checklist work.

#### Scenario: Unstarted proposal remains available for a fold
- **WHEN** a repository contains an unstarted OpenSpec proposal with a readable
  `proposal.md`
- **THEN** the proposal is returned as an active overlap and fold candidate
