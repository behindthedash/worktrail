# quarantine-live-merge-recheck Specification

## Purpose
Prevents a group whose PR actually merged (by this run's own auto-merge or by
a repo's independent external auto-merge automation) from being incorrectly
recorded as quarantined just because this run's own bounded verification
budget expired first.
## Requirements
### Requirement: Live merge recheck before finalizing an ordinary quarantine verdict
When a group's verification chain (mergeability check, CI wait/fix loop,
review-thread resolution, or the cumulative merge gate) reports failure for a
reason other than a confirmed self-merge violation or a post-merge cumulative
regression, the system SHALL query the PR's live state exactly once before
recording that group as quarantined.

#### Scenario: Verification budget exhausted but PR already merged externally
- **WHEN** a group's verification chain reports failure (e.g. the CI wait/fix
  loop exhausted its poll/strike budget) and, at that moment, the PR's live
  state is already `MERGED`
- **THEN** the system records the group as merged, not quarantined, and
  proceeds with the normal post-merge cleanup for that group

#### Scenario: Verification budget exhausted, PR not yet merged, no external automation armed
- **WHEN** a group's verification chain reports failure and the PR's live
  state is neither `MERGED` nor carrying an armed auto-merge request
- **THEN** the system records the group as quarantined with the original
  failure reason, unchanged from current behavior

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

### Requirement: Recheck is passive and never arms or attempts its own merge
The live merge recheck SHALL only observe the PR's live state — it SHALL NOT
call any merge or auto-merge-arming action, and SHALL NOT extend or restart
this run's own verification poll/strike budgets.

#### Scenario: Recheck runs against a PR with no armed auto-merge and not yet merged
- **WHEN** the live recheck observes a PR that is neither merged nor carrying
  any auto-merge request
- **THEN** the system does not arm auto-merge, does not attempt a merge, and
  proceeds directly to finalizing the quarantine verdict

### Requirement: Self-merge violations and post-merge regressions are unaffected
The live merge recheck SHALL NOT run for, and SHALL NOT change the recorded
verdict of, a group already classified as a confirmed self-merge violation or
a post-merge cumulative regression.

#### Scenario: Confirmed self-merge violation
- **WHEN** a group's verification chain reports failure and that failure is
  attributable to a confirmed self-merge violation
- **THEN** the system records the group as a self-merge violation exactly as
  it does today, without performing the live merge recheck

#### Scenario: Post-merge cumulative regression
- **WHEN** a group's PR is confirmed merged but then fails the cumulative
  post-merge regression check
- **THEN** the system records the group as a post-merge regression exactly as
  it does today, without performing the live merge recheck

