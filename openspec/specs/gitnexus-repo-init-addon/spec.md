## Purpose

Lets a repo bootstrapped via `worktrail-repo-init propose` opt into GitNexus code-index
registration at bootstrap time, instead of requiring a human to remember to run
`gitnexus analyze` by hand after onboarding.

## ADDED Requirements

### Requirement: `--with-gitnexus` opt-in flag on `propose`
The system SHALL provide a `--with-gitnexus` boolean flag on the `propose` subcommand. When
the flag is absent, `propose` SHALL NOT invoke GitNexus indexing and SHALL NOT alter its
existing written/skipped/warnings behavior.

#### Scenario: Flag omitted
- **WHEN** `worktrail-repo-init propose --repo <repo>` runs without `--with-gitnexus`
- **THEN** no GitNexus indexing is attempted and no GitNexus-related entry appears in the
  result's `written`, `skipped`, or `warnings` lists

#### Scenario: Flag provided on a fresh repo
- **WHEN** `worktrail-repo-init propose --repo <repo> --with-gitnexus` runs against a repo
  with no `.gitnexus/` directory
- **THEN** the system indexes the repo and records the outcome in the result's `written`,
  `skipped`, or `warnings` list

### Requirement: Idempotent bootstrap indexing
The system SHALL skip GitNexus indexing when the target repo already has a `.gitnexus/`
directory, treating that as evidence the repo is already indexed, without emitting a warning
for that case.

#### Scenario: Repo already indexed
- **WHEN** `--with-gitnexus` is passed for a repo where `.gitnexus/` already exists
- **THEN** the system performs no indexing action and reports the repo as skipped (not
  written, not warned)

### Requirement: Best-effort indexing with postcondition verification
The system SHALL treat GitNexus indexing as best-effort: a subprocess failure, timeout, or
non-zero exit code from the indexing command SHALL NOT cause `propose` to fail or exit
non-zero. Success SHALL be determined by checking that `.gitnexus/` exists after the attempt,
not by trusting the subprocess's exit code.

#### Scenario: Indexing command fails or times out
- **WHEN** the GitNexus indexing command fails, times out, or is not found, for a repo with
  no pre-existing `.gitnexus/` directory
- **THEN** `propose` still completes successfully (exit code 0) and reports a warning
  describing the failure, and `.gitnexus/` is confirmed absent

#### Scenario: Indexing command succeeds
- **WHEN** the GitNexus indexing command runs and `.gitnexus/` now exists where it did not
  before
- **THEN** the system reports the repo as successfully indexed in the `written` list and
  emits no warning for that step

### Requirement: Bootstrap indexing skips AGENTS.md/skills file injection
The system SHALL invoke GitNexus indexing in a mode that skips AGENTS.md/CLAUDE.md/skills
file injection, so that GitNexus's own documentation-injection side effect does not rewrite
files `worktrail-repo-init propose` just wrote in the same run.

#### Scenario: Bootstrap indexing runs alongside AGENTS.md/CLAUDE.md generation
- **WHEN** `propose --with-gitnexus` runs against a repo where `propose` also just wrote or
  split `AGENTS.md`/`CLAUDE.md` in the same invocation
- **THEN** the GitNexus indexing step does not modify `AGENTS.md`, `CLAUDE.md`, or any skills
  files

### Requirement: No per-task add-on wiring
The system SHALL NOT add a `gitnexus` entry to `.worktrail/policy.yaml`'s `add_ons` block and
SHALL NOT register GitNexus as a `worktrail.addons.runner`-managed add-on, since GitNexus has
no ongoing per-task synchronization step for that runner to invoke.

#### Scenario: Policy file after `--with-gitnexus`
- **WHEN** `worktrail-repo-init propose --repo <repo> --with-gitnexus` writes
  `.worktrail/policy.yaml` for a repo with no pre-existing policy file
- **THEN** the written policy file's `add_ons` block (if present at all) does not contain a
  `gitnexus` key
