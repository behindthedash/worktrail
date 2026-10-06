## MODIFIED Requirements

### Requirement: Reconciliation reuses existing conflict and quarantine handling
The system SHALL NOT introduce new merge-conflict-resolution logic for tail
reconciliation. A reconciliation attempt that cannot merge the tail task's
branch cleanly onto base SHALL be quarantined through the same mechanism and
recorded with the same quarantine-reason vocabulary used for ordinary group
integration failures -- except when the attempt's branch contributes nothing
beyond base, in which case the attempt SHALL be recorded with the reconciled
(`merged`) outcome instead and no merge SHALL be attempted for it. "Contributes
nothing beyond base" covers a branch whose content base already contains and a
verification-only task stacked on a dependency whose content base already
carries (its branch carries no commits of its own beyond that stacked start
ref). An attempt whose branch carries changes base does not have SHALL still be
quarantined with the merge-conflict reason, and a branch that merely conflicts
with base SHALL NOT be read as delivered.

#### Scenario: Tail branch conflicts with the current base
- **WHEN** merging a tail task's own branch onto base during reconciliation
  produces a merge conflict that cannot be auto-resolved and the branch carries
  changes base does not have
- **THEN** the reconciliation attempt for that task is recorded as
  QUARANTINED with a merge-conflict reason, no partial or forced merge is
  pushed, and the existing quarantined-group detector surfaces this without
  requiring any new code path

#### Scenario: A stacked verification-only task is reconciled, not conflicted
- **WHEN** reconciliation runs for a tail task whose branch carries no commits
  of its own beyond the dependency start ref it was stacked on and base already
  contains that start ref's content
- **THEN** the attempt records the terminal delivered (merged) outcome without
  merging, the finding's reconciliation outcome is reported as `merged` rather
  than quarantined, and the run-complete note reports no unreconciled evidence
  for that task

#### Scenario: A branch carrying changes base lacks still quarantines
- **WHEN** a reconciliation attempt's branch carries commits whose content base
  does not have and merging it onto base conflicts
- **THEN** the attempt is recorded as QUARANTINED with the merge-conflict
  reason exactly as before this change, and no delivered or merged outcome is
  recorded for it
