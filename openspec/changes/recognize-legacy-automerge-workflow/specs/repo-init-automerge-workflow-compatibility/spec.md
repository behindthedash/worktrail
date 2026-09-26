## ADDED Requirements

### Requirement: propose recognizes the legacy auto-merge workflow
`worktrail-repo-init` SHALL recognize both the canonical
`.github/workflows/worktrail-auto-merge.yml` and the legacy
`.github/workflows/auto-merge.yml` as its auto-merge workflow. If either path exists,
`propose` SHALL leave both existing files byte-identical and SHALL NOT write another auto-merge
workflow. `apply` SHALL ensure `AUTOMERGE_LABELS` when either recognized path exists. A fresh
repo with neither path SHALL receive the canonical path.

#### Scenario: Legacy-only repo is not given a duplicate workflow
- **WHEN** `propose` runs against a repo containing only `.github/workflows/auto-merge.yml`
- **THEN** that file is reported as skipped, remains byte-identical, and
  `.github/workflows/worktrail-auto-merge.yml` is not written

#### Scenario: Fresh repo receives the canonical workflow
- **WHEN** `propose` runs against a repo containing neither recognized path
- **THEN** it writes `.github/workflows/worktrail-auto-merge.yml`

#### Scenario: Both workflow names remain operator-owned
- **WHEN** `propose` runs against a repo containing both recognized paths
- **THEN** neither file is modified or deleted

#### Scenario: Apply provisions labels for a legacy-only repo
- **WHEN** `apply` runs against a repo containing only `.github/workflows/auto-merge.yml`
- **THEN** it ensures every label in `AUTOMERGE_LABELS`

### Requirement: recognized auto-merge workflows participate in drift reporting
`compute_drift` SHALL compare each existing recognized auto-merge workflow byte-for-byte with
`build_automerge_workflow()` and report a separate, path-specific drift entry for every differing
file. Drift remains report-only.

#### Scenario: Legacy workflow drift is visible
- **WHEN** a legacy-only `.github/workflows/auto-merge.yml` differs from the current generated
  workflow
- **THEN** `drift` includes `.github/workflows/auto-merge.yml` and `propose` leaves its content
  unchanged

#### Scenario: Both names are checked independently
- **WHEN** both recognized paths exist and only one differs from the generated workflow
- **THEN** `drift` identifies only the differing path

### Requirement: new PR-label guidance identifies the effective workflow
When `propose` writes an absent `docs/engineering/pull-requests.md`, it SHALL name the effective
recognized auto-merge workflow path. The legacy path is effective when it is the only recognized
path; the canonical path is effective when neither or both paths exist. An existing doc remains
byte-identical.

#### Scenario: Legacy-only repo gets legacy-aware guidance
- **WHEN** `propose` writes the PR-label document in a legacy-only repo
- **THEN** the document names `.github/workflows/auto-merge.yml` and does not name the canonical
  workflow path

#### Scenario: Canonical path is preferred for new guidance
- **WHEN** `propose` writes the PR-label document in a repo with both recognized paths
- **THEN** the document names `.github/workflows/worktrail-auto-merge.yml`
