## MODIFIED Requirements

### Requirement: CI watch runs to a classified terminal outcome

After the pull request exists, the pipeline SHALL wait for its checks to settle using the
provider's blocking watch, bounded by the caller's watch budget, and SHALL classify the
settled state as exactly one of: all-pass, transient infrastructure failure, code defect, or
watch budget exhausted. A transient infrastructure failure SHALL be rerun without counting as
a patch iteration, a bounded number of times. A code defect SHALL be reported to the caller
with the failing check names and failed-step log excerpt, leaving the PR open and the run
record unfinished so the caller can repair and re-invoke; the pipeline SHALL persist the
patch-iteration count on the run record and SHALL treat the fifth code-defect report as the
iteration ceiling. When the watch budget is exhausted, the pipeline SHALL re-query the live
PR's state once before finishing; if the PR is already merged, the pipeline SHALL continue into
the all-pass completion flow (merge-state guard, review-thread gate, finish as merged) instead
of finishing as recoverably failed. The re-query SHALL only read state -- it SHALL NOT rerun
checks. The pipeline SHALL never leave a PR open with an unclassified outcome.

#### Scenario: All checks pass
- **WHEN** the watch settles with no failing check
- **THEN** the pipeline proceeds to the merge-state guard and review-thread gate

#### Scenario: Transient infrastructure failure
- **WHEN** a failing check's name or log matches the transient-infrastructure markers
  (container initialization, job setup, registry daemon errors)
- **THEN** the failed run is rerun, the patch-iteration count is unchanged, and the watch
  re-enters

#### Scenario: Code defect reported to caller
- **WHEN** the watch settles with a failing check that is not transient
- **THEN** the result reports the failing check names and log excerpt, the PR stays open,
  the run record records the incremented patch iteration and is not finished, and the exit
  status distinguishes this from a landed or refused outcome

#### Scenario: Iteration ceiling
- **WHEN** a code defect is reported after the patch-iteration count has reached five
- **THEN** the run record is finished as recoverably failed with a summary of the iterations
  and the pipeline stops

#### Scenario: Watch budget exhausted
- **WHEN** checks are still pending after the watch budget and its bounded re-issues, and the
  re-queried PR state is not merged
- **THEN** the run record is finished as recoverably failed noting that checks were still
  pending, and the result says so

#### Scenario: PR merged by the time the watch budget is exhausted
- **WHEN** the watch budget is exhausted and the re-queried PR state is merged
- **THEN** the pipeline runs the review-thread gate and, when nothing blocks, finishes the run
  record as merged with a merge result noting the external merge, and the outcome is landed,
  not ceiling

#### Scenario: Re-query fails at the watch budget
- **WHEN** the watch budget is exhausted and the PR state re-query fails or returns malformed
  data
- **THEN** the pipeline treats the PR as not merged and finishes as recoverably failed exactly
  as before
