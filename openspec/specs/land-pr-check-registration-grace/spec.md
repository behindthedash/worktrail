# land-pr-check-registration-grace Specification

## Purpose
Size the landing pipeline's wait for CI checks to register from the run's own watch budget
rather than a fixed number of attempts. The grace period ahead of the CI watch exists for the
race immediately after `gh pr create`, where a workflow has not attached its checks yet; a fixed
9-second grace reported a run granted a 600-second budget as having exhausted its watch budget
after spending a fraction of it, with a merge result that was false on its face — the watch had
never been entered. The grace now scales with `watch_timeout_s`, bounded below by the
pre-existing fixed grace and above by a single watch window, and its exhaustion is reported
distinctly from the main watch loop's.
## Requirements
### Requirement: Check-registration grace scales with the run's watch budget
The landing pipeline's grace period for checks that have not yet registered SHALL be sized
from the run's configured watch timeout rather than a fixed number of attempts. The grace
period SHALL NOT be shorter than the pre-existing fixed grace, and SHALL NOT exceed the
duration of a single watch window. The polling interval within the grace period SHALL be
unchanged.

#### Scenario: A longer watch timeout grants a longer grace period
- **WHEN** the CI watch runs with a watch timeout larger than the pre-existing fixed grace
  period and checks never register
- **THEN** the pipeline probes for registered checks more times than the pre-existing fixed
  attempt count, and no more often than the pre-existing polling interval

#### Scenario: A very short watch timeout keeps the original grace period
- **WHEN** the CI watch runs with a watch timeout shorter than the pre-existing fixed grace
  period and checks never register
- **THEN** the pipeline still probes the pre-existing fixed number of times before giving up

#### Scenario: Grace period never outlasts one watch window
- **WHEN** the CI watch runs with a large watch timeout and checks never register
- **THEN** the total time spent probing does not exceed that watch timeout

#### Scenario: Checks register within the scaled grace period
- **WHEN** checks register on a probe that falls beyond the pre-existing fixed attempt count
  but within the scaled grace period
- **THEN** the pipeline enters the main watch loop instead of reporting an exhausted budget

#### Scenario: A merge during the scaled grace period is terminal
- **WHEN** the PR is observed merged on a probe within the scaled grace period
- **THEN** the pipeline reports a settled watch without an exhausted budget, as before this
  change

### Requirement: Exhausted registration grace is reported distinctly from watch exhaustion
When the check-registration grace period is spent without checks registering, the landing
pipeline SHALL report a reconcilable ceiling whose merge result identifies the failure as
checks never having registered, distinct from the merge result used when the main watch loop
exhausts its own budget. The outcome and final status SHALL remain the reconcilable ceiling
they are today.

#### Scenario: Registration grace is exhausted
- **WHEN** the grace period is spent with no checks registered
- **THEN** the outcome is a ceiling with `failed_recoverable` and a merge result naming
  unregistered checks, not the merge result used for watch-budget exhaustion

#### Scenario: The main watch loop exhausts its own budget
- **WHEN** checks register and the main watch loop then exhausts its re-issue budget
- **THEN** the merge result is the watch-budget-exhaustion one, unchanged by this change
