# pr-landing-pipeline Specification

## Purpose
Defines the single sequence every Worktrail path follows to open or update a pull request,
so that no path can omit a mandatory landing step: compile-marker gate, label computation,
push, PR create/update, CI watch with merge-state and review-thread gates, and run-record
completion with a real terminal state.
## Requirements
### Requirement: Every PR-opening path lands through the shared pipeline

Every code path in Worktrail that opens or updates a pull request — the queue-triage
fold/propose apply, the close-stale OpenSpec action, drain's sync-pending,
stale-bookkeeping, and archive remediations, the orchestrator's group-PR creation, and the
agent-executed spec/implementation closeout — SHALL do so by invoking the shared landing
pipeline. No such path SHALL construct its own `gh pr create` invocation, compute its own
label set, or decide on its own whether to watch CI. The only permitted exception is a
sandbox-only development tool that never produces a policy-governed pull request.

#### Scenario: Queue-triage fold or propose opens a PR
- **WHEN** `apply --confirm` executes a `fold-into-change` or `propose-change` verdict
- **THEN** the pull request is opened through the shared pipeline and the apply result
  carries the pipeline's landing outcome alongside the PR URL

#### Scenario: Close-stale action opens a PR
- **WHEN** the close-stale OpenSpec action has flipped the remaining checkboxes and archived
  the change in its worktree
- **THEN** the same action lands the resulting pull request through the shared pipeline
  rather than leaving commit, push, PR, and CI watch to the calling agent

#### Scenario: Drain remediation opens a PR
- **WHEN** a drain remediation action (sync-pending, stale-bookkeeping, or OpenSpec archive)
  has committed its change on a short-lived branch
- **THEN** the pull request is opened through the shared pipeline with the action's own
  timeout as the CI watch budget

#### Scenario: New hand-rolled PR creation is rejected
- **WHEN** a new `gh pr create` call site appears anywhere under the package other than the
  pipeline module or the registered sandbox-only exception
- **THEN** the call-site enforcement test fails and names the unregistered file

### Requirement: Compile marker is current before anything is pushed

Before pushing, the pipeline SHALL identify every OpenSpec change directory whose task
checklist differs from the base branch, using the same discovery CI's scope check uses, run
the compile against each, and commit the resulting content-fingerprint marker. If, after
that attempt, any such change directory's marker is missing or does not match its current
content, the pipeline SHALL refuse to push, open, or update a pull request, and SHALL report
which change directory failed and why. A landing whose diff touches no change directory's
task checklist SHALL skip this step without error.

#### Scenario: Marker missing and compile cannot pass
- **WHEN** a change directory's tasks changed against the base branch, no marker exists,
  and the compile reports scope gaps
- **THEN** nothing is pushed, no pull request is created or updated, and the result names
  the change directory and the compile's gap report

#### Scenario: Marker stale after a task edit
- **WHEN** a change directory carries a marker recorded against an earlier version of its
  task checklist and the compile is unavailable in this environment
- **THEN** nothing is pushed and the result reports the marker as stale for that directory

#### Scenario: Marker written and committed
- **WHEN** the compile passes for a changed change directory
- **THEN** the marker is committed on the head branch before the push, and the pull request
  passes CI's scope check without a hand-added marker commit

#### Scenario: Diff touches no task checklist
- **WHEN** the landing's diff against the base branch contains no OpenSpec task checklist
- **THEN** the compile step is skipped and the pipeline proceeds to label computation

### Requirement: Labels are computed by the preflight gate and applied on create and update

The pipeline SHALL compute the pull request's `go:risk-<level>` and `go:no-automerge` labels
by running the preflight gate against the committed head, so the label set is the one the
repo's policy, the classifier gates, and the live required-check state require, and so the
push-time label-enforcement hook's marker is recorded. On create, the pipeline SHALL pass
exactly those labels. On update of an existing open pull request for the same head branch,
the pipeline SHALL ensure the PR carries them, adding only what is missing and never removing
an existing `go:no-automerge`. The pipeline SHALL refuse to push when the preflight gate
denies.

#### Scenario: Fresh PR carries the computed labels
- **WHEN** no pull request exists for the head branch and the preflight gate passes with a
  computed label set
- **THEN** the created pull request carries exactly that label set

#### Scenario: Existing PR is updated, not duplicated
- **WHEN** an open pull request already exists for the head branch
- **THEN** no second pull request is created, any missing computed label is added, and the
  pipeline continues to the CI watch against the existing PR

#### Scenario: Preflight denies
- **WHEN** the preflight gate exits non-zero for the committed head
- **THEN** nothing is pushed and the result quotes the gate's output

### Requirement: Standard PR body

Every pull request the pipeline creates SHALL use the standard PR body: the caller's summary,
the route and spec lineage when known, the pre-PR gate evidence line, the risk level, the
applied labels, and the auto-merge recommendation, in the section layout the route reference
defines. A caller SHALL NOT be able to omit the gate-evidence, risk, or label sections.

#### Scenario: Body carries the enforced sections
- **WHEN** the pipeline creates a pull request from a caller-supplied summary
- **THEN** the PR body contains the summary plus the pre-PR gate evidence, risk level,
  labels, and auto-merge recommendation sections

### Requirement: CI watch runs to a classified terminal outcome

After the pull request exists, the pipeline SHALL wait for its checks to settle using the
provider's blocking watch, bounded by the caller's watch budget, and SHALL classify the
settled state as exactly one of: all-pass, transient infrastructure failure, code defect, or
watch budget exhausted. A transient infrastructure failure SHALL be rerun without counting as
a patch iteration, a bounded number of times. A code defect SHALL be reported to the caller
with the failing check names and failed-step log excerpt, leaving the PR open and the run
record unfinished so the caller can repair and re-invoke; the pipeline SHALL persist the
patch-iteration count on the run record and SHALL treat the fifth code-defect report as the
iteration ceiling. The pipeline SHALL never leave a PR open with an unclassified outcome.

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
- **WHEN** checks are still pending after the watch budget and its bounded re-issues
- **THEN** the run record is finished as recoverably failed noting that checks were still
  pending, and the result says so

### Requirement: Merge-state guard and review-thread gate before completion

On all-pass, the pipeline SHALL re-query the live PR. If the PR is already merged, it SHALL
finish as merged. Otherwise it SHALL apply the merge-state guard: a blocked merge state with
a cancelled run alongside a successful run of the same check context SHALL be rerun up to
two times without counting as a patch iteration. It SHALL then run the review-thread gate
against the PR and the run record; an unresolved, uncorrelated review thread SHALL be
reported to the caller as blocking, leaving the PR open and the run record unfinished, and
the PR SHALL carry `go:no-automerge` while blocking. A merge state still blocked after both
the guard and the gate are exhausted SHALL finish as blocked on a product decision with the
raw check summary in the merge result.

#### Scenario: Already merged when checks settle
- **WHEN** the live PR state is merged at the all-pass re-query
- **THEN** the run record is finished as merged with a merge result noting the external merge

#### Scenario: Stray cancelled run
- **WHEN** the merge state is blocked and a cancelled run shares a check context with a
  successful run
- **THEN** the cancelled run is rerun, the merge state is re-queried, and no patch iteration
  is counted

#### Scenario: Unaddressed review thread
- **WHEN** the review-thread gate reports an unresolved thread with no correlated commit or
  recorded decision
- **THEN** the result reports the thread as blocking, the PR stays open and labeled
  `go:no-automerge`, and the run record is not finished

#### Scenario: Gate clears with auto-merge armed
- **WHEN** the review-thread gate reports nothing blocking and the PR has an auto-merge
  request
- **THEN** the run record is finished as PR-open with a merge result naming the arming
  mechanism

### Requirement: Run record is completed with a real state

Every landing SHALL stamp the pull request URL and merge result on a run record and finish it
with one of the allowed completion states, or — in checkpoint mode, for a PR opened mid-run
that the calling route continues past — append the same outcome as a decision entry instead
of finishing. A caller with no run record SHALL have one started for it by the pipeline,
carrying the caller's route and request summary, so that no landing is unrecorded.

#### Scenario: Caller supplies a run record
- **WHEN** a landing is invoked with an existing run record
- **THEN** that record gains the pull request URL and merge result and is finished with the
  classified state

#### Scenario: Caller has no run record
- **WHEN** a landing is invoked without a run record
- **THEN** the pipeline starts one for the caller's repository and route before landing, and
  the result names its path

#### Scenario: Checkpoint mode
- **WHEN** a landing is invoked in checkpoint mode and the outcome is all-pass
- **THEN** the run record gains a decision entry describing the outcome and is not finished,
  and control returns to the caller

### Requirement: Refusal leaves the remote untouched

Whenever the pipeline refuses — a dirty tree it was not asked to commit, a missing or stale
compile marker after the compile attempt, or a preflight denial — it SHALL make no push and
create no pull request, SHALL return a refused outcome naming the failed step and its
output, and SHALL leave any run record it started in a non-terminal state that names the
refusal.

#### Scenario: Refusal after a local commit
- **WHEN** the pipeline has committed the caller's changes and the compile marker step then
  refuses
- **THEN** the head branch has no remote counterpart, no pull request exists, and the
  refused result names the compile step

