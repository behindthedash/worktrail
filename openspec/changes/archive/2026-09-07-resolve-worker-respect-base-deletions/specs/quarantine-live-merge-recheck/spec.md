## MODIFIED Requirements

### Requirement: Self-merge violations and post-merge regressions are unaffected
The live merge recheck SHALL NOT run for, and SHALL NOT change the recorded verdict of, a group
already classified as a confirmed self-merge violation, a confirmed forbidden-path violation, or
a post-merge cumulative regression.

#### Scenario: Confirmed self-merge violation
- **WHEN** a group's verification chain reports failure and that failure is attributable to a
  confirmed self-merge violation
- **THEN** the system records the group as a self-merge violation exactly as it does today,
  without performing the live merge recheck

#### Scenario: Confirmed forbidden-path violation
- **WHEN** a group's verification chain reports failure and a resolve or ci-fix worker for that
  group has a confirmed forbidden-path violation recorded
- **THEN** the system records the group as a forbidden-path violation exactly as
  `resolve-worker-scope-discipline` specifies, without performing the live merge recheck, even if
  the PR's live state is `MERGED`

#### Scenario: Post-merge cumulative regression
- **WHEN** a group's PR is confirmed merged but then fails the cumulative post-merge regression
  check
- **THEN** the system records the group as a post-merge regression exactly as it does today,
  without performing the live merge recheck
