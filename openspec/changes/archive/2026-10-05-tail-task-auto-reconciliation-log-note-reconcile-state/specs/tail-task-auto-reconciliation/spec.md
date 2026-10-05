## MODIFIED Requirements

### Requirement: Reconciliation outcome is recorded and reported
The run journal's `unreconciled_tail_evidence` findings SHALL be enriched
with the outcome of each reconciliation attempt (whether a PR was opened,
already existed, merged, or the attempt was quarantined, and the PR URL when
one exists), and every finding message derived from those findings SHALL
reflect the recorded outcome instead of a fixed instruction to reconcile
manually. This covers every such message, including the dashboard-facing
finding and the run-complete console note, not the dashboard alone.

A finding whose recorded outcome is `merged` has already reached the base
branch. No finding message SHALL state or imply that such a finding's commits
never merged onto base, and no finding message SHALL instruct a reader to
reconcile it. When every finding for a run is `merged`, the run's finding
messages SHALL report no unreconciled evidence for that run at all; the
journal retains the findings as history.

A finding whose recorded outcome is `quarantined`, or which carries no
recorded outcome, SHALL continue to be reported as requiring manual
reconciliation.

#### Scenario: Reconciliation opened a PR
- **WHEN** a tail task's reconciliation attempt results in an OPEN PR
- **THEN** the corresponding `unreconciled_tail_evidence` journal entry
  records that PR's URL and state, and the finding messages for that task
  indicate a reconciliation PR is already open awaiting merge rather than
  instructing a human to reconcile it

#### Scenario: Reconciliation was quarantined
- **WHEN** a tail task's reconciliation attempt is quarantined (e.g. merge
  conflict, push failure, PR-creation failure)
- **THEN** the corresponding `unreconciled_tail_evidence` journal entry
  records the quarantine reason, and the finding messages continue to
  indicate the task needs manual/human triage

#### Scenario: A finding whose commits reached base via a squash-merged reconciliation PR
- **WHEN** a run's reconciliation PR for a tail task merges by squash, so the
  task's own commit is not an ancestor of the base branch even though its
  change is on it, and the finding records outcome `merged`
- **THEN** the run's finding messages report no unreconciled evidence for that
  finding — no message asserts its commits never merged onto base and no
  message instructs a reader to reconcile it — while the journal keeps the
  finding as history

#### Scenario: A verification-only tail task whose branch already contained base
- **WHEN** a tail task ran no code change of its own and its worktree branch
  already contains the base branch, so detection flags it with an empty diff
  and reconciliation records outcome `merged`
- **THEN** the run's finding messages report no unreconciled evidence for that
  finding, and the run does not carry a manual-reconciliation instruction for
  it

#### Scenario: Partially reconciled findings report only the outstanding ones
- **WHEN** a run's `unreconciled_tail_evidence` findings mix outcomes (some
  `merged`, some awaiting an open reconciliation PR, some quarantined)
- **THEN** the run's finding messages describe each outstanding finding
  according to its own recorded outcome, and no message asserts that a
  `merged` finding's commits never merged onto base
