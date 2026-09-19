## ADDED Requirements

### Requirement: Scaffold a portable gitleaks secrets-scan workflow
Whenever `.github/workflows/gitleaks.yml` does not yet exist, `propose` SHALL write
that workflow together with the vendored
`scripts/ci/check_gitleaks_signal_integrity.py`. The workflow SHALL contain a
`gitleaks-pr-diff` job that runs on every `pull_request` targeting a protected branch
with no `paths`/`paths-ignore` filter and no change-detection gate, scans only
`base.sha..head.sha` with `fetch-depth: 0`, and asserts scan signal integrity so a
range covering zero commits fails the job rather than reporting "no leaks found". It
SHALL also contain a full-history job reachable only via `workflow_dispatch`.

#### Scenario: Fresh repo
- **WHEN** `propose` runs against a repo with no `.github/workflows/gitleaks.yml`
- **THEN** `propose` writes that workflow and
  `scripts/ci/check_gitleaks_signal_integrity.py`

#### Scenario: Workflow already present
- **WHEN** `propose` runs against a repo where `.github/workflows/gitleaks.yml`
  already exists
- **THEN** `propose` leaves it unchanged and reports it as skipped, matching the
  existing `automerge_workflow_exists` skip pattern

#### Scenario: The PR job is never filtered out of a docs-only diff
- **WHEN** the generated `gitleaks.yml` is parsed
- **THEN** its `gitleaks-pr-diff` job's `pull_request` trigger carries neither
  `paths` nor `paths-ignore`, and the job has no `needs` on a change-detection job

#### Scenario: The full-history job is not attached to pull requests
- **WHEN** the generated `gitleaks.yml` is parsed
- **THEN** the full-history job runs only when `github.event_name == 'workflow_dispatch'`

## MODIFIED Requirements

### Requirement: Wire the new check into required_status_checks, scoped to this job only
When `propose` writes a new `worktrail-openspec-validate.yml` workflow, or a new
`gitleaks.yml` workflow, in the current run, it SHALL append that workflow's job
display name to `required_status_checks` in the generated ruleset(s) for the
branch(es) `build_ruleset_for_branch()` protects. `propose` SHALL NOT add any other
discovered CI job's display name to `required_status_checks` — every other job
discovered via `discover_ci_checks()` continues to be reported for human review only,
unchanged from existing behavior. A workflow that was already present SHALL NOT have
its job name added, so re-running `propose` never grows the required-check list.

#### Scenario: New workflow written this run, ruleset file not yet present
- **WHEN** `propose` writes `worktrail-openspec-validate.yml` or `gitleaks.yml` for
  the first time in the current run, and a given branch's
  `.github/rulesets/protect-<branch>.json` does not yet exist
- **THEN** the freshly-generated ruleset for that branch includes a
  `required_status_checks` entry whose `context` is that workflow job's display name

#### Scenario: New workflow written this run, ruleset file already exists
- **WHEN** `propose` writes `worktrail-openspec-validate.yml` or `gitleaks.yml` for
  the first time in the current run, and a given branch's
  `.github/rulesets/protect-<branch>.json` already exists
- **THEN** `propose` patches that existing ruleset file in place to add a
  `required_status_checks` entry for each newly-written job's display name — creating
  the `required_status_checks` rule if the file doesn't already have one — without
  otherwise regenerating or reordering the file's existing rules

#### Scenario: Both workflows written in the same run
- **WHEN** `propose` writes both `worktrail-openspec-validate.yml` and `gitleaks.yml`
  in one run
- **THEN** each protected branch's ruleset carries a `required_status_checks` entry
  for both `openspec-validate` and `gitleaks-pr-diff`

#### Scenario: Only one workflow is new
- **WHEN** `gitleaks.yml` already exists but `worktrail-openspec-validate.yml` is
  written this run
- **THEN** only `openspec-validate` is added to `required_status_checks`

#### Scenario: Workflow already present, not newly written
- **WHEN** `propose` runs against a repo where
  `.github/workflows/worktrail-openspec-validate.yml` or `.github/workflows/gitleaks.yml`
  already existed before this run
- **THEN** `propose` does not add or re-add that job's display name to
  `required_status_checks` as a side effect of this run (it may already be present from
  a prior run's ruleset, which `propose` does not remove)

#### Scenario: Other CI jobs remain unaffected
- **WHEN** `propose` runs against a repo with existing, unrelated CI jobs discovered by
  `discover_ci_checks()` (for example a repo-owned `Lint, Test & Build` job)
- **THEN** none of those other jobs' display names are added to
  `required_status_checks` by this change; they continue to appear only in the
  existing informational discovered-jobs report
