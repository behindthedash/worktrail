## Purpose

Implements the `aspens` skill-doc sync tool as the first worktrail add-on,
so a repo that opts in gets aspens installed, configured, and kept in sync
with each task's own commit end-to-end, without a repo owner ever hand-running
the aspens CLI or aspens' own post-commit hook.

## ADDED Requirements

### Requirement: aspens CLI availability is worktrail's responsibility
The system SHALL ensure the `aspens` CLI is installed and usable before the
first time the add-on's run step needs it, without requiring the repo owner
to have run `npm install -g aspens` (or an update) by hand.

#### Scenario: aspens CLI missing
- **WHEN** the aspens add-on runs in an environment where the `aspens` CLI
  is not yet installed
- **THEN** the add-on installs it before proceeding with configure/run

### Requirement: aspens is configured, not left uninitialized
The system SHALL ensure `.aspens.json` and an initial set of skills exist for
an opted-in repo (running `aspens doc init` or equivalent with the add-on's
configured target/backend, or sane defaults if none is configured), without
requiring the repo owner to have run `aspens doc init` by hand.

#### Scenario: Repo has never run aspens
- **WHEN** the aspens add-on is enabled for a repo with no `.aspens.json`
- **THEN** the add-on initializes aspens for that repo before its first sync

#### Scenario: Repo already has a valid aspens configuration
- **WHEN** the aspens add-on runs in a repo with an existing, valid
  `.aspens.json`
- **THEN** the add-on does not destroy or blindly overwrite that
  configuration

### Requirement: aspens' own post-commit hook is never installed
The system SHALL NOT invoke aspens' own `--install-hook` (or equivalent)
mechanism at any point, since that hook's detached/async, non-committing
behavior is the specific failure mode this add-on replaces.

#### Scenario: Configure step runs
- **WHEN** the aspens add-on's configure step runs for any repo
- **THEN** no aspens-managed git hook is installed into that repo's
  `.git/hooks`

### Requirement: aspens sync runs and commits after each task
The system SHALL run `aspens doc sync` (or `--refresh`) as the add-on's run
step after a task's own commit, and its file output SHALL be staged and
committed via the shared post-task-addon-framework commit step so the sync
lands in the same PR as the task that triggered it.

#### Scenario: Sync produces skill-doc changes
- **WHEN** `aspens doc sync` updates or creates skill-doc files for a task
  that just committed its own work
- **THEN** those files are committed before that task's branch is pushed or
  its PR is opened, in the same PR the task produced
