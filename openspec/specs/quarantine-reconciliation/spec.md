# quarantine-reconciliation Specification

## Purpose
TBD - created by archiving change quarantine-reconciliation. Update Purpose after archive.
## Requirements
### Requirement: Recompute group→file membership from the cached RunPlan
For a QUARANTINED finding, the system SHALL locate that spec's cached RunPlan
(`<repo>-worktrees/runplans/<spec_id>-*.json`), pass its `tasks` list to
`coordinator.plan_groups()`, and match the finding's `group` name against the
returned group names to obtain the set of task ids — and each task's declared
`files` — belonging to that group.

#### Scenario: RunPlan cache present and group matches
- **WHEN** reconciliation runs for a finding whose spec has a RunPlan cache
  file, and `plan_groups()` over that RunPlan's tasks produces a group whose
  `name` equals the finding's `group`
- **THEN** the reconciler obtains the union of `files` across that group's
  task ids

#### Scenario: RunPlan cache missing
- **WHEN** no `<spec_id>-*.json` file exists under `<repo>-worktrees/runplans/`
  for the finding's spec id
- **THEN** reconciliation is skipped for that finding and it passes through
  unresolved, exactly as `check_repo()` reported it today

### Requirement: Reconcile against base-branch file presence
The system SHALL check, for each file in a group's recomputed file set,
whether that path exists in the base branch's tree (`git ls-tree <base>:<path>`
or equivalent), where `<base>` SHALL default to the repository's current
checked-out branch if not otherwise specified.

#### Scenario: All group files present on base
- **WHEN** every file in the group's recomputed file set exists in the base
  branch's tree
- **THEN** the finding is reconciled (auto-resolved) with reconciliation
  method `base-branch-files`, and excluded from the returned `findings` list

#### Scenario: One or more group files missing from base
- **WHEN** at least one file in the group's recomputed file set does not exist
  in the base branch's tree
- **THEN** the base-branch signal does not reconcile the finding; the system
  proceeds to the merged-PR file-match signal

### Requirement: Reconcile against a merged PR's changed-file set
When the base-branch signal does not reconcile a finding, the system SHALL
list recently-merged PRs targeting the base branch and check, for each
candidate, whether its changed-file set is a superset of the group's
recomputed file set.

#### Scenario: A merged PR's files are a superset of the group's files
- **WHEN** at least one recently-merged PR's changed-file set contains every
  file in the group's recomputed file set
- **THEN** the finding is reconciled (auto-resolved) with reconciliation
  method `merged-pr-files`, recording the matching PR's URL, and excluded from
  the returned `findings` list

#### Scenario: No merged PR matches
- **WHEN** no recently-merged PR's changed-file set is a superset of the
  group's recomputed file set
- **THEN** the finding remains unreconciled and is included in the returned
  `findings` list exactly as `check_repo()` reports it today

#### Scenario: PR lookup fails
- **WHEN** the `gh` CLI invocation used to list or inspect merged PRs fails
  (network error, authentication error, non-zero exit)
- **THEN** the merged-PR signal is treated as inconclusive (not confirmed);
  the finding remains unreconciled and is included in the returned `findings`
  list — never silently dropped on an error

### Requirement: Reconciliation record retained for auto-resolved findings
For every finding excluded from the returned `findings` list by reconciliation,
the system SHALL retain a reconciliation record (spec id, group, reconciliation
method, and matching evidence — base-branch file list or matching PR URL)
accessible via a separate return field, distinct from the active `findings`
list, so an auto-resolved quarantine is never a silent, unauditable drop.

#### Scenario: Reconciled finding is auditable
- **WHEN** `check_repo()` auto-resolves a finding via either reconciliation
  signal
- **THEN** the finding does not appear in the returned `findings` list, and a
  corresponding entry naming the spec id, group, method, and evidence appears
  in the returned reconciliation record

