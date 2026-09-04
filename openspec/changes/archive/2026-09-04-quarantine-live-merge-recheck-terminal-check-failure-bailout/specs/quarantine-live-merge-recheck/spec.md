## MODIFIED Requirements

### Requirement: Bounded wait for an externally-armed auto-merge before finalizing quarantine
When the live recheck finds the PR's auto-merge already armed by automation
outside this run (not armed by this run itself), the system SHALL wait, once,
up to this run's existing external-merge poll budget for that merge to
complete before finalizing a quarantine verdict.

The system SHALL end that wait early, before the poll budget is exhausted, when
the PR's required checks have reached a terminal state in which none are still
pending and at least one has failed — a state in which the armed auto-merge
cannot complete without a new commit this run will not push. In that case the
system SHALL finalize the quarantine verdict immediately, with a reason naming
the failing required checks.

While required checks are still pending, the wait SHALL continue for the full
existing poll budget, unchanged — the early exit SHALL NOT shorten a legitimate
wait.

The system SHALL make the wait observable, emitting one log entry per poll that
identifies the group, what the wait is waiting on, and the poll number, so a
long wait is distinguishable from a hung process.

#### Scenario: Externally-armed auto-merge completes within the bounded wait
- **WHEN** a group's verification chain reports failure, the live recheck
  finds an auto-merge request armed by automation outside this run, and that
  auto-merge completes within the existing bounded external-merge wait
- **THEN** the system records the group as merged, not quarantined

#### Scenario: Externally-armed auto-merge does not complete within the bounded wait
- **WHEN** a group's verification chain reports failure, the live recheck
  finds an auto-merge request armed by automation outside this run, and that
  auto-merge does not complete before the bounded wait expires
- **THEN** the system records the group as quarantined with the original
  failure reason

#### Scenario: Required checks terminally failed while the auto-merge stays armed
- **WHEN** a group's verification chain reports failure, the live recheck
  finds an auto-merge request armed by automation outside this run, and the
  PR's required checks show no check still pending and at least one check
  failed
- **THEN** the system ends the bounded wait immediately without consuming the
  remaining poll budget, and records the group as quarantined with a reason
  naming the failing required checks

#### Scenario: Required checks still pending during the bounded wait
- **WHEN** a group's verification chain reports failure, the live recheck
  finds an auto-merge request armed by automation outside this run, and the
  PR's required checks include at least one check still pending — whether or
  not other checks have already failed
- **THEN** the system continues the bounded wait exactly as it does today,
  without ending it early

#### Scenario: Bounded wait is visible in the run log
- **WHEN** the system is waiting for an externally-armed auto-merge to
  complete
- **THEN** each poll of that wait emits a log entry naming the group, what the
  wait is waiting on, and the poll number
