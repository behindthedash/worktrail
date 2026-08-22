# postmerge-reconciliation-audit Specification

## Purpose
Fleet-wide detection of merged PRs whose required status checks later
reported a failure — closing the systemic gap behind PR #164/#165/#207,
each of which was a live-only-discoverable mismatch between assumed and
actual GitHub API behavior that only a human noticed after the fact.

## Requirements

### Requirement: Fleet-wide merged-PR check reconciliation
The system SHALL provide a sweep that, for every repo discovered via the
same `docs/specs/worktrail-go-policy.yaml` opt-in mechanism `reconcile_pr_labels.py`
uses, lists PRs merged since that repo's last successful sweep and
re-classifies each PR's live `statusCheckRollup` using `verify.py`'s existing
`classify_checks()` function, unmodified.

#### Scenario: Merged PR whose required check reported failure after merge
- **WHEN** the sweep re-checks a merged PR's `statusCheckRollup` via `gh pr
  view --json statusCheckRollup`
- **THEN** if `classify_checks()` reports any failing required check name for
  that PR, the sweep records that PR (repo, URL, failing check names,
  merge time) as a post-merge check failure

#### Scenario: Merged PR with all required checks green
- **WHEN** the sweep re-checks a merged PR's `statusCheckRollup`
- **THEN** if `classify_checks()` reports no failing checks, the PR is not
  recorded and no dashboard entry is created for it

### Requirement: Incremental, bounded sweep cost
The system SHALL persist a per-repo "last swept" marker so each sweep only
re-checks PRs merged after the previous successful sweep for that repo,
instead of re-scanning full merged-PR history every run.

#### Scenario: Second sweep after a prior successful sweep
- **WHEN** a sweep for a repo has already completed successfully once
- **THEN** the next sweep for that repo only queries PRs merged after the
  persisted marker, and advances the marker to the current sweep time on
  success

#### Scenario: Sweep failure leaves the marker unchanged
- **WHEN** a sweep for a repo fails before completing (e.g. `gh` auth
  failure, network error)
- **THEN** the per-repo marker is left at its previous value so the next
  sweep re-covers the same window rather than silently skipping it

### Requirement: Read-only detection, no PR mutation
The system SHALL NOT modify any PR, label, branch, or code as part of this
audit — it only detects and reports.

#### Scenario: Post-merge check failure detected
- **WHEN** the sweep flags a merged PR with a post-merge required-check
  failure
- **THEN** the sweep does not comment on, label, revert, or otherwise modify
  that PR or its branch

### Requirement: Dashboard visibility
The system SHALL surface any flagged PRs from the most recent sweep as an
additive field in the `/go` orientation dashboard's JSON output, without
altering the shape or meaning of any existing dashboard field.

#### Scenario: Dashboard rendered after a sweep found flagged PRs
- **WHEN** `worktrail-dashboard` builds its JSON output and a prior sweep
  recorded one or more flagged PRs for a repo in scope
- **THEN** the dashboard JSON includes those flagged PRs in a new
  `postmerge_check_failures` field, and existing fields
  (`staleness_warnings`, `capacity`, etc.) are unchanged in shape and content

#### Scenario: Dashboard rendered with no flagged PRs
- **WHEN** no sweep has ever run for a repo, or the most recent sweep found
  no flagged PRs
- **THEN** the dashboard JSON's `postmerge_check_failures` field is empty for
  that repo, and no other dashboard behavior changes
