# repo-init-pr-labels-doc Specification

## Purpose

Defines the agent-facing PR-label instructions `worktrail-repo-init propose` scaffolds into
an onboarded repo. The scaffolded auto-merge workflow fails closed: a green PR carrying no
`go:risk-low` / `go:risk-medium` label is never armed and sits unmerged with
`mergeStateStatus: CLEAN` and no error, so an agent opening a PR with a bare `gh pr create`
has no in-repo signal that a label is required. This capability puts that signal in the repo
-- a `docs/engineering/pull-requests.md` naming every label and how to set, change, and
verify one, plus a pointer to it from `AGENTS.md` -- rendered for the repo's own branch
model.

## Requirements

### Requirement: propose scaffolds the PR labels doc
`worktrail-repo-init propose` SHALL write `docs/engineering/pull-requests.md` into the target
repo when it is absent, and SHALL leave an existing file byte-identical and report it under
"skipped". The doc SHALL name all five labels in `AUTOMERGE_LABELS` (`go:risk-low`,
`go:risk-medium`, `go:risk-high`, `go:risk-critical`, `go:no-automerge`), state that a PR
without `go:risk-low` or `go:risk-medium` is never auto-merged, and document setting, changing,
and verifying a label. It SHALL NOT embed a repository owner or name.

#### Scenario: Fresh repo gets the doc
- **WHEN** `propose` runs against a repo with no `docs/engineering/pull-requests.md`
- **THEN** the file is written, reported under "written", and contains every name in `AUTOMERGE_LABELS`

#### Scenario: Existing doc is never rewritten
- **WHEN** `propose` runs against a repo whose `docs/engineering/pull-requests.md` was hand-edited
- **THEN** the file is left byte-identical and reported under "skipped"

#### Scenario: propose --check reports presence without writing
- **WHEN** `propose --check` runs against a repo
- **THEN** the reported state says whether the doc exists and no file is written

### Requirement: the doc matches the repo's branch model
The doc SHALL list the one merge method each scaffolded protected branch allows, taken from
`merge_method_for_branch`, and SHALL include a promotion-PR rule (hold with `go:no-automerge`)
only when the branch model has a branch other than `dev` and `main`.

#### Scenario: Two-branch model
- **WHEN** the doc is rendered for `["dev", "prd"]`
- **THEN** it lists `dev` as squash and `prd` as merge, and includes the promotion-PR rule naming `prd`

#### Scenario: Three-branch model
- **WHEN** the doc is rendered for `["dev", "stg", "prd"]`
- **THEN** the promotion-PR rule names both `stg` and `prd`

#### Scenario: Main-only model
- **WHEN** the doc is rendered for `["main"]`
- **THEN** it lists `main` as squash and contains no promotion-PR rule

### Requirement: propose links the doc from AGENTS.md
`propose` SHALL add a "Pull requests" section to `AGENTS.md` that states every PR needs a
`go:risk-*` label and points at `docs/engineering/pull-requests.md`. The section SHALL be
inserted before the first tool-managed block marker (`<!-- name:start -->`), or appended when
there is none. An `AGENTS.md` that already contains the doc path SHALL be left byte-identical.
The pointer SHALL be added whether or not the doc file itself was newly written.

#### Scenario: AGENTS.md with a tool-managed block
- **WHEN** `AGENTS.md` contains an `<!-- aspens:start -->` block
- **THEN** the section is inserted before that block and the block's content is unchanged

#### Scenario: AGENTS.md without a tool-managed block
- **WHEN** `AGENTS.md` has none
- **THEN** the section is appended after the existing content

#### Scenario: Pointer already present
- **WHEN** `AGENTS.md` already mentions `docs/engineering/pull-requests.md`
- **THEN** `AGENTS.md` is left byte-identical

#### Scenario: Re-running propose does not duplicate the section
- **WHEN** `propose` runs twice
- **THEN** `AGENTS.md` contains the section exactly once

### Requirement: the doc is excluded from drift reporting
`compute_drift` SHALL NOT report `docs/engineering/pull-requests.md`, because it is prose an
operator may tailor.

#### Scenario: Customized doc is not drift
- **WHEN** the doc on disk differs from today's template
- **THEN** `drift` does not list it
