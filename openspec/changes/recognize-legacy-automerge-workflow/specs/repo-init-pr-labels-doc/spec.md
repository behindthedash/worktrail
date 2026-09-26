## MODIFIED Requirements

### Requirement: propose scaffolds the PR labels doc
`worktrail-repo-init propose` SHALL write `docs/engineering/pull-requests.md` into the target
repo when it is absent, and SHALL leave an existing file byte-identical and report it under
"skipped". The doc SHALL name all five labels in `AUTOMERGE_LABELS` (`go:risk-low`,
`go:risk-medium`, `go:risk-high`, `go:risk-critical`, `go:no-automerge`), state that a PR
without `go:risk-low` or `go:risk-medium` is never auto-merged, document setting, changing, and
verifying a label, and identify the effective recognized auto-merge workflow path. It SHALL NOT
embed a repository owner or name.

#### Scenario: Fresh repo gets the doc
- **WHEN** `propose` runs against a repo with no `docs/engineering/pull-requests.md`
- **THEN** the file is written, reported under "written", and contains every name in
  `AUTOMERGE_LABELS`

#### Scenario: Existing doc is never rewritten
- **WHEN** `propose` runs against a repo whose `docs/engineering/pull-requests.md` was
  hand-edited
- **THEN** the file is left byte-identical and reported under "skipped"

#### Scenario: propose --check reports presence without writing
- **WHEN** `propose --check` runs against a repo
- **THEN** the reported state says whether the doc exists and no file is written

#### Scenario: Legacy-only repo gets a correct workflow path
- **WHEN** `propose` writes the doc in a repo with only
  `.github/workflows/auto-merge.yml`
- **THEN** the doc identifies `.github/workflows/auto-merge.yml`
