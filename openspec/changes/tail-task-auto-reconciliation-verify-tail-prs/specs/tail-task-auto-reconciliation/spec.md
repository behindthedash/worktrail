## ADDED Requirements

### Requirement: Reconciliation PR receives the same CI verification as a group PR
When a tail task's reconciliation attempt yields a PR in `OPEN` state
(freshly opened or reused from a prior attempt), the system SHALL run that
PR through the same watch-until-green, review-thread-resolution, CI-fix, and
merge treatment (`Verifier.verify_one`) that an ordinary impl-group PR
receives from the pipeline scheduler, instead of leaving the PR unverified
once it exists. A finding whose reconciliation result is `merged`,
`quarantined`, or `superseded` at the point the PR is opened/reused SHALL NOT
be passed through this verification step, since there is no open PR for it
to verify.

#### Scenario: Freshly opened tail PR reaches CI verification
- **WHEN** reconciliation opens a new PR for an unreconciled tail task's
  commits
- **THEN** the system runs `verify_one` against that PR before reconciliation
  for that finding completes, so a failing check triggers the same
  auto-CI-fix attempt an ordinary group PR would get

#### Scenario: Reused open tail PR reaches CI verification
- **WHEN** reconciliation reuses an existing `OPEN` PR from a prior attempt
  for the same tail task
- **THEN** the system still runs `verify_one` against that PR rather than
  skipping verification because the PR already existed

#### Scenario: A CI-verified tail PR merges cleanly
- **WHEN** a tail reconciliation PR's checks pass (either immediately or
  after an automatic CI-fix) and its review threads are resolved
- **THEN** the PR is merged the same way an ordinary group PR is merged, with
  no additional manual step

#### Scenario: A tail PR that fails CI verification is quarantined, not left open forever
- **WHEN** a tail reconciliation PR fails `ensure_mergeable`, exhausts its
  CI-fix retries, or cannot resolve its review threads during verification
- **THEN** the attempt is recorded as quarantined through the same mechanism
  ordinary group verification failures use, rather than leaving the PR
  open and unattended
