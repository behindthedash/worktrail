## ADDED Requirements

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
