# devkit-dod-auto-verification Specification

## Purpose
Extends the devkit Definition-of-Done verification gate so a task that
completes without hand-authored `dod-checks:` frontmatter still gets a
baseline spot-check derived from its own existing frontmatter and body, and
adds a non-blocking audit surface for spot-checking the pre-existing task
backlog on demand.

## Requirements

### Requirement: Derived Check Fallback

The system SHALL derive a fallback set of Definition-of-Done checks for a
devkit task file whose `status` is `completed` and whose `dod-checks`
frontmatter field is absent or empty, using only that task file's own
frontmatter `files:` list and body `## Acceptance Criteria` section.
Derivation SHALL run in the same code path that already runs explicit
`dod-checks:` entries, so a derived check failure is reported and gates the
PR exactly as an explicit check failure does.

#### Scenario: Completed task with no dod-checks and a files list

- **WHEN** a task file's `status` is `completed`, `dod-checks` is absent, and
  `files:` lists one or more repo-relative paths
- **THEN** the system SHALL derive a check that each listed path exists in
  the worktree and is tracked by git

#### Scenario: Completed task with unchecked Acceptance Criteria

- **WHEN** a task file's `status` is `completed`, `dod-checks` is absent, and
  the task's `## Acceptance Criteria` section contains at least one `- [ ]`
  (unchecked) checkbox
- **THEN** the system SHALL report a Definition-of-Done verification failure
  for that task

#### Scenario: Completed task with a stub marker in a referenced file

- **WHEN** a task file's `status` is `completed`, `dod-checks` is absent,
  `files:` lists a path, and that path's content contains `TODO`, `FIXME`,
  `XXX`, or `NotImplementedError`
- **THEN** the system SHALL report a Definition-of-Done verification failure
  naming that path and marker

#### Scenario: Explicit dod-checks present

- **WHEN** a task file's `dod-checks` frontmatter field is present and
  non-empty
- **THEN** the system SHALL run only the explicit `dod-checks` entries and
  SHALL NOT derive any additional checks for that task

#### Scenario: Completed task with no files and no Acceptance Criteria drift

- **WHEN** a task file's `status` is `completed`, `dod-checks` is absent,
  `files:` is absent or empty, and the `## Acceptance Criteria` section (if
  present) has no unchecked checkboxes
- **THEN** the system SHALL report no Definition-of-Done verification
  failure for that task

#### Scenario: Task not newly completed in the current diff

- **WHEN** a task file is unchanged in the current diff, regardless of its
  `status` or `dod-checks` contents
- **THEN** the system SHALL NOT run derived or explicit checks for that task
  as part of the pre-PR gate

### Requirement: Backlog Audit Mode

The system SHALL provide a standalone command that spot-checks every devkit
task file under a repository's `docs/specs/` tree — not only files changed in
the current diff — using the same explicit-or-derived check logic, and SHALL
report failures without altering the pre-PR gate's pass/fail outcome for
files outside the current diff.

#### Scenario: Audit run against a repository with pre-existing completed tasks

- **WHEN** the audit command runs against a repository whose `docs/specs/`
  tree contains devkit task files marked `completed` before this feature
  existed
- **THEN** the system SHALL evaluate each such file's explicit-or-derived
  checks and report every failure found, identifying the task file and the
  specific failing check

#### Scenario: Audit findings do not block unrelated PRs

- **WHEN** the pre-PR gate runs for a PR whose diff does not change a task
  file that the audit command would report as failing
- **THEN** the pre-PR gate SHALL NOT fail on account of that task file
