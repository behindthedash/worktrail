# land-pr-required-context-coverage Specification

## Purpose
Keep the landing pipeline's CI watch from settling on checks it was never meant to trust. A PR
whose fast third-party statuses (for example Vercel) register before the required GitHub Actions
contexts would otherwise end the no-checks grace period, be watched to a clean exit, and be
reported as a settled pass before any required check had even been created, so merge handling
proceeded on CI it never observed.
## Requirements
### Requirement: CI watch settles only on required-context coverage
The landing pipeline SHALL resolve the base branch's ruleset-required status check contexts
before the CI watch and SHALL treat checks as registered only when every required context
appears among the checks reported for the PR. While coverage is incomplete the watch SHALL
keep polling within its existing grace period, and SHALL report budget exhausted rather than
settled when that grace period is spent with any required context still missing. A blocking
watch that exits with no failures while coverage is still incomplete SHALL NOT be reported as
settled; the watch SHALL re-enter against its remaining re-issue budget. When the required
contexts cannot be read, or the branch has no required contexts configured, the watch SHALL
behave exactly as it did before this change.

#### Scenario: Only non-required checks have registered
- **WHEN** the PR reports checks but none of them is a required context
- **THEN** the grace loop keeps polling instead of entering the blocking watch

#### Scenario: Required contexts register during the grace period
- **WHEN** a later grace poll reports every required context among the PR's checks
- **THEN** the grace loop exits and the blocking watch is entered

#### Scenario: Grace period spent with a required context missing
- **WHEN** the grace period is exhausted and at least one required context is still absent
- **THEN** the watch returns budget exhausted with no failing checks, not settled

#### Scenario: Watch exits clean before coverage
- **WHEN** the blocking watch exits with no failing checks while a required context is still
  absent from the PR's reported checks
- **THEN** the watch does not return settled and re-enters against its remaining re-issue budget

#### Scenario: Watch exits clean with full coverage
- **WHEN** the blocking watch exits with no failing checks and every required context is
  present
- **THEN** the watch returns settled with no failing checks and budget not exhausted

#### Scenario: Required-context query fails
- **WHEN** the required-context read returns no answer
- **THEN** any reported check ends the grace period and a clean watch exit settles, as before
  this change

#### Scenario: Branch has no required contexts
- **WHEN** the base branch's ruleset declares zero required status check contexts
- **THEN** any reported check ends the grace period and a clean watch exit settles, as before
  this change

#### Scenario: Merged PR is still terminal
- **WHEN** the PR is already merged while required contexts are still missing
- **THEN** the watch returns settled, as before this change

### Requirement: Merge-state block is classified only on required-context reporting
The landing pipeline SHALL NOT classify a BLOCKED merge state as a completed
`blocked_product_decision` while any of the base branch's ruleset-required status check
contexts has not reported a terminal conclusion for the PR. While coverage is incomplete the
pipeline SHALL re-poll the merge-state guard within a bounded budget, and SHALL report a
reconcilable ceiling with `failed_recoverable`, naming the outstanding contexts, when that
budget is spent with coverage still incomplete. A required context that has reported a
terminal conclusion — including a failing one — SHALL count as reported. When the required
contexts cannot be read, or the branch has no required contexts configured, the pipeline SHALL
classify a BLOCKED merge state exactly as it did before this change.

#### Scenario: Required context is still pending
- **WHEN** the merge state is BLOCKED and a required context appears in the status check
  rollup with no terminal conclusion
- **THEN** the pipeline re-polls the merge-state guard instead of classifying the PR

#### Scenario: Required context is absent from the rollup
- **WHEN** the merge state is BLOCKED and a required context does not appear in the status
  check rollup at all
- **THEN** the pipeline re-polls the merge-state guard instead of classifying the PR

#### Scenario: Coverage completes during the re-poll
- **WHEN** a later poll reports every required context with a terminal conclusion while the
  merge state is still BLOCKED
- **THEN** the pipeline classifies the PR as `blocked_product_decision` and completes the run
  record, as before this change

#### Scenario: Merge state clears during the re-poll
- **WHEN** a later poll reports a merge state that is no longer BLOCKED
- **THEN** the pipeline leaves the block branch and continues its normal flow for that state

#### Scenario: Re-poll budget spent without coverage
- **WHEN** the re-poll budget is exhausted with the merge state BLOCKED and at least one
  required context still unreported
- **THEN** the outcome is a ceiling with `failed_recoverable` and a merge result naming the
  outstanding required contexts, and the run record is not completed as
  `blocked_product_decision`

#### Scenario: Required context reported a failure
- **WHEN** the merge state is BLOCKED and every required context has a terminal conclusion,
  one of which is a failure
- **THEN** the pipeline classifies the PR as `blocked_product_decision` without re-polling

#### Scenario: Required-context query returned no answer
- **WHEN** the required-context read returned no answer and the merge state is BLOCKED
- **THEN** the pipeline classifies the PR as `blocked_product_decision` immediately, as before
  this change

#### Scenario: Branch has no required contexts
- **WHEN** the base branch's ruleset declares zero required status check contexts and the
  merge state is BLOCKED
- **THEN** the pipeline classifies the PR as `blocked_product_decision` immediately, as before
  this change

