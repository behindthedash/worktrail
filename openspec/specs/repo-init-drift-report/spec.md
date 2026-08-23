# repo-init-drift-report Specification

## Purpose

Defines drift detection and reporting for `worktrail-repo-init propose`: identifying
already-scaffolded, worktrail-owned files whose content no longer matches what the
current generator would produce, and surfacing that as report-only data for a human
(or an agent on their behalf) to act on.

## Requirements

### Requirement: Always-on drift computation, no new flags
`propose` SHALL compute a `drift` list on every invocation (including `--check` mode)
without requiring any new CLI flag, and SHALL include it in both the JSON result and
the text-mode output.

#### Scenario: Fresh repo
- **WHEN** `propose` runs against a repo with none of the scaffolded files present yet
- **THEN** the result's `drift` list is empty

#### Scenario: Already-onboarded, unmodified repo
- **WHEN** `propose` runs a second time against a repo where every scaffolded file
  still matches what the current generator would produce
- **THEN** the result's `drift` list is empty

#### Scenario: Check mode includes drift
- **WHEN** `propose --check` runs against a repo with a drifted file
- **THEN** the printed state includes a `drift` entry for that file

### Requirement: Drift is report-only and never auto-applied
`propose` SHALL NOT modify, delete, or regenerate any file it reports as drifted.
Detected drift is data for the caller to act on, never an automatic action.

#### Scenario: Drifted file left untouched
- **WHEN** `propose` runs against a repo where a scaffolded file's content has
  diverged from the current template
- **THEN** that file's on-disk content is unchanged after the run, and it still
  appears in `skipped` as before, in addition to appearing in `drift`

### Requirement: Ruleset drift excludes required_status_checks
For `protect-<branch>.json` ruleset files, drift detection SHALL compare only the
structural rules (merge methods, review-thread resolution, linear-history policy,
deletion) against what `build_ruleset_for_branch` would generate today for that
branch, with any `required_status_checks` rule excluded entirely from the comparison.

#### Scenario: Operator-added required check is not drift
- **WHEN** an existing ruleset file's `required_status_checks` differs from (or is
  absent in) what a fresh `build_ruleset_for_branch` call would produce, but every
  other rule matches
- **THEN** that file is not reported as drifted

#### Scenario: Structural change is drift
- **WHEN** an existing ruleset file's non-required_status_checks rules differ from
  what `build_ruleset_for_branch` would generate today for that branch (for example,
  a stale `allowed_merge_methods` or missing `required_linear_history`)
- **THEN** that file is reported as drifted, with a detail explaining the
  required_status_checks exclusion

### Requirement: Content-only comparison for workflow and script templates
For files with no such operator-growth field (the auto-merge workflow, the
openspec-validate workflow, the rulesets-drift-guard workflow, and its vendored
`rulesets_sync.py`/`requirements.txt`), drift detection SHALL compare the full file
content against the current generator's output.

#### Scenario: Stale workflow content flagged
- **WHEN** an existing scaffolded workflow or vendored script file's content does not
  byte-for-byte match what its generator function produces today
- **THEN** that file is reported as drifted

### Requirement: Hand-edited and third-party-owned files are out of scope
Drift detection SHALL NOT evaluate `.worktrail/policy.yaml`, `CLAUDE.md`, `AGENTS.md`,
or any file owned by a third-party tool's own init/index step (`openspec/`,
`.aspens.json`, `.gitnexus/`) -- these have no single "current" template to diff
against.

#### Scenario: Policy and doc files never appear in drift
- **WHEN** `propose` runs against a repo with a customized `.worktrail/policy.yaml`
  and a hand-authored `AGENTS.md`
- **THEN** neither file ever appears in the `drift` list, regardless of its content
