# land-pr-required-context-coverage Specification

## Purpose
TBD - created by archiving change land-pr-wait-for-required-check-contexts. Update Purpose after archive.
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

