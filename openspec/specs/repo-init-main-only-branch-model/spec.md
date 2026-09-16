# repo-init-main-only-branch-model Specification

## Purpose
TBD - created by archiving change repo-init-main-only-branch-model. Update Purpose after archive.
## Requirements
### Requirement: Main-only branch model is a supported repo-init choice

`worktrail-repo-init` SHALL accept `--branch-model main`, and for that model SHALL generate
exactly one ruleset, `.github/rulesets/protect-main.json`, targeting `refs/heads/main` with
squash-only merges and required linear history, and SHALL compute ruleset drift against
`main` only.

#### Scenario: Propose writes only the main ruleset

- **WHEN** `worktrail-repo-init propose --branch-model main` runs against a repo with no rulesets
- **THEN** `.github/rulesets/protect-main.json` is written targeting `refs/heads/main` with `squash` as the only allowed merge method and linear history required
- **AND** no `protect-dev.json`, `protect-stg.json`, or `protect-prd.json` is written

#### Scenario: Existing branch models are unchanged

- **WHEN** `propose` runs with `--branch-model 2` or `--branch-model 3`
- **THEN** the generated rulesets are identical to those produced before this change

### Requirement: Apply on a main-only repo never migrates branches

When the declared rulesets are exactly `protect-main.json`, `worktrail-repo-init apply` SHALL
NOT create, rename, or change the default of any branch; it SHALL require the repo's current
default branch to be `main`, and otherwise SHALL apply delete-branch-on-merge, rulesets, and
automerge labels as for the other branch models. A ruleset set mixing `protect-main.json`
with `protect-dev.json` or `protect-prd.json` SHALL be rejected.

#### Scenario: Main-only apply skips branch migration

- **WHEN** `apply` runs on a repo whose only ruleset is `protect-main.json` and whose default branch is `main`
- **THEN** no branch is created or renamed and the default branch is not changed
- **AND** the `protect-main` ruleset is applied and the result reports `branch_model` `main`

#### Scenario: Default branch is not main

- **WHEN** `apply` runs on a repo whose only ruleset is `protect-main.json` but whose default branch is not `main`
- **THEN** `apply` exits non-zero with an error naming the current default branch and makes no changes

#### Scenario: Mixed ruleset declarations are rejected

- **WHEN** `apply` runs with both `protect-main.json` and `protect-dev.json` declared
- **THEN** `apply` exits non-zero with an ambiguity error and makes no changes

