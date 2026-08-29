# tail-task-auto-reconciliation Specification

## Purpose
Automatically integrates a completed tail-kind (e2e/cleanup) task's own
unreconciled commits onto the base branch via a small dedicated PR, instead of
requiring a human to notice the `unreconciled-tail-evidence` dashboard finding
and reconcile the evidence by hand before a worktree-cleanup pass deletes it.
## Requirements
### Requirement: Automatic reconciliation PR for unreconciled tail evidence
When a `full-real` orchestrator run's tail-task detection
(`detect_unreconciled_tail_evidence`) finds one or more terminal tail-kind
tasks whose own commits never merged onto base, the system SHALL attempt to
open a PR carrying each such task's own commits onto base, reusing the same
per-group integration path (branch creation, push, PR creation, conflict
handling, reconcile-safe resume) used for ordinary impl-task group PRs.

#### Scenario: Single unreconciled tail task gets its own PR
- **WHEN** a `full-real` run's tail dispatch completes and detection finds
  exactly one terminal tail task whose branch HEAD is not an ancestor of
  `<remote>/<base>`
- **THEN** the system opens a PR whose head branch carries only that task's
  own commits (including its `tasks.md`/task-status update), targeting the
  run's base branch, without any manual step

#### Scenario: Multiple unreconciled tail tasks each get an independent PR
- **WHEN** detection finds more than one terminal tail task each carrying
  unmerged commits on its own worktree branch
- **THEN** the system attempts reconciliation independently for each task, so
  one task's PR creation failing or being quarantined does not prevent the
  other task's PR from being opened

### Requirement: Reconciliation reuses existing conflict and quarantine handling
The system SHALL NOT introduce new merge-conflict-resolution logic for tail
reconciliation. A reconciliation attempt that cannot merge the tail task's
branch cleanly onto base SHALL be quarantined through the same mechanism and
recorded with the same quarantine-reason vocabulary used for ordinary group
integration failures.

#### Scenario: Tail branch conflicts with the current base
- **WHEN** merging a tail task's own branch onto base during reconciliation
  produces a merge conflict that cannot be auto-resolved
- **THEN** the reconciliation attempt for that task is recorded as
  QUARANTINED with a merge-conflict reason, no partial or forced merge is
  pushed, and the existing quarantined-group detector surfaces this without
  requiring any new code path

### Requirement: Reconciliation is safe to retry across resumed runs
Attempting reconciliation for the same tail task more than once (e.g. because
a run resumes, or the reconcile step runs again on a later `full-real`
invocation over the same journal) SHALL NOT create duplicate branches, open
duplicate PRs, or re-attempt a merge that already merged.

#### Scenario: Reconciliation re-runs after a prior PR was already opened
- **WHEN** reconciliation runs again for a tail task that already has an OPEN
  reconciliation PR from a prior attempt
- **THEN** the system reuses the existing PR instead of creating a new one

#### Scenario: Reconciliation re-runs after the prior PR already merged
- **WHEN** reconciliation runs again for a tail task whose prior
  reconciliation PR has since merged
- **THEN** the system records the task as already reconciled and takes no
  further action for it

### Requirement: Reconciliation outcome is recorded and reported
The run journal's `unreconciled_tail_evidence` findings SHALL be enriched
with the outcome of each reconciliation attempt (whether a PR was opened,
already existed, merged, or the attempt was quarantined, and the PR URL when
one exists), and the dashboard-facing finding message SHALL reflect that
outcome instead of a fixed instruction to reconcile manually.

#### Scenario: Reconciliation opened a PR
- **WHEN** a tail task's reconciliation attempt results in an OPEN PR
- **THEN** the corresponding `unreconciled_tail_evidence` journal entry
  records that PR's URL and state, and the dashboard finding for that task
  indicates a reconciliation PR is already open awaiting merge rather than
  instructing a human to reconcile it

#### Scenario: Reconciliation was quarantined
- **WHEN** a tail task's reconciliation attempt is quarantined (e.g. merge
  conflict, push failure, PR-creation failure)
- **THEN** the corresponding `unreconciled_tail_evidence` journal entry
  records the quarantine reason, and the dashboard finding continues to
  indicate the task needs manual/human triage

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

