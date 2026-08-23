## Purpose

Defines the OpenSpec-validation CI job that `worktrail-repo-init` scaffolds into an
onboarded repo, and the narrowly-scoped exception under which that one job's check is
wired into the repo's `required_status_checks`.

## ADDED Requirements

### Requirement: Scaffold a portable openspec-validate workflow
`propose` always ends this run with `openspec/config.yaml` present — either it was
already there, or `propose` writes it unconditionally (existing, unchanged behavior;
there is no opt-out flag). Given that, whenever
`.github/workflows/worktrail-openspec-validate.yml` does not yet exist, `propose` SHALL
write that workflow file. The workflow SHALL run `openspec validate --all --strict` and
SHALL be paths-filtered so it only executes on diffs touching `openspec/**`.

#### Scenario: Fresh repo
- **WHEN** `propose` runs against a repo with no existing `openspec/` scaffold and no
  `.github/workflows/worktrail-openspec-validate.yml`
- **THEN** `propose` writes `openspec/config.yaml` (existing behavior) and also writes
  `.github/workflows/worktrail-openspec-validate.yml`

#### Scenario: Already-onboarded repo, workflow missing
- **WHEN** `propose` runs against a repo where `openspec/config.yaml` already exists
  but `.github/workflows/worktrail-openspec-validate.yml` does not
- **THEN** `propose` writes the missing workflow file without re-running
  `openspec init` or otherwise touching the existing `openspec/` scaffold

#### Scenario: Workflow already present
- **WHEN** `propose` runs against a repo where
  `.github/workflows/worktrail-openspec-validate.yml` already exists
- **THEN** `propose` leaves the existing file unchanged and reports it as skipped,
  matching the existing `automerge_workflow_exists` skip pattern

### Requirement: Wire the new check into required_status_checks, scoped to this job only
When `propose` writes a new `worktrail-openspec-validate.yml` workflow in the current
run (per the requirement above), it SHALL append that workflow's job display name to
`required_status_checks` in the generated ruleset(s) for the branch(es)
`build_ruleset_for_branch()` protects. `propose` SHALL NOT add any other discovered CI
job's display name to `required_status_checks` — every other job discovered via
`discover_ci_checks()` continues to be reported for human review only, unchanged from
existing behavior.

#### Scenario: New workflow written this run, ruleset file not yet present
- **WHEN** `propose` writes `worktrail-openspec-validate.yml` for the first time in
  the current run, and a given branch's `.github/rulesets/protect-<branch>.json` does
  not yet exist
- **THEN** the freshly-generated ruleset for that branch includes a
  `required_status_checks` entry whose `context` is that workflow job's display name

#### Scenario: New workflow written this run, ruleset file already exists
- **WHEN** `propose` writes `worktrail-openspec-validate.yml` for the first time in
  the current run (an already-onboarded repo, per the requirement above), and a given
  branch's `.github/rulesets/protect-<branch>.json` already exists
- **THEN** `propose` patches that existing ruleset file in place to add a
  `required_status_checks` entry for the new job's display name — creating the
  `required_status_checks` rule if the file doesn't already have one — without
  otherwise regenerating or reordering the file's existing rules
- **AND** if that ruleset file's `required_status_checks` rule already contains an
  entry with this job's exact display name, `propose` makes no change to that file

#### Scenario: Workflow already present, not newly written
- **WHEN** `propose` runs against a repo where
  `.github/workflows/worktrail-openspec-validate.yml` already existed before this run
- **THEN** `propose` does not add or re-add that job's display name to
  `required_status_checks` as a side effect of this run (it may already be present from
  a prior run's ruleset, which `propose` does not remove)

#### Scenario: Other CI jobs remain unaffected
- **WHEN** `propose` runs against a repo with existing, unrelated CI jobs discovered by
  `discover_ci_checks()` (for example a repo-owned `Lint, Test & Build` job)
- **THEN** none of those other jobs' display names are added to
  `required_status_checks` by this change; they continue to appear only in the
  existing informational discovered-jobs report

### Requirement: Idempotent on workflow-file presence, not solely on openspec_initialized
Whether `propose` writes the openspec-validate workflow and ruleset entry SHALL be
determined by the workflow file's own presence on disk, not solely by whether
`openspec_initialized` was already true before this run.

#### Scenario: Re-running propose on a fully onboarded repo
- **WHEN** `propose` runs a second time against a repo that already has both
  `openspec/config.yaml` and `.github/workflows/worktrail-openspec-validate.yml`
- **THEN** `propose` makes no changes related to this capability and reports both as
  already present
