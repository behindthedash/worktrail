## MODIFIED Requirements

### Requirement: Sync-pending remediation

The system SHALL detect devkit specs and active OpenSpec changes in the
`sync-pending` stage across `--repos-root` and retain each finding's format,
defaulting a dashboard row with no format to `devkit`. For each finding, it
SHALL spawn a headless one-shot agent run of the format-native sync operation
in an isolated remediation worktree: `/opsx:sync <change-id>` for an OpenSpec
change, and developerkit spec-sync for a devkit spec. The developerkit sync
SHALL reconcile the spec against merged code and write a `spec-sync` agent
entry under `knowledge-graph.json`'s `metadata.analysis_sources`.

When a format-native sync exits successfully but produces no worktree diff,
the system SHALL re-check the finding's stage. If it is still `sync-pending`,
the action SHALL fail the finding rather than report a successful no-op or
open a pull request. A finding that is no longer sync-pending at re-check may
complete as a no-op.

Before committing a produced draft, when the sync was run for an OpenSpec
change, the action SHALL inspect the draft's diff. If the draft adds a
top-level `# ... Specification` title line to an
`openspec/specs/<capability>/spec.md` whose diff status is modified rather
than added — a spec that already existed at base — the action SHALL reject the
draft by raising, naming the spec path, with no commit, push, or pull request
attempted. Drafts that add a newly created canonical spec file, and drafts
that change only requirement or scenario content beneath an existing spec's
heading, SHALL be landed as before. The generic remediation sweep SHALL retain
its existing per-finding failure isolation.

#### Scenario: A spec is in the sync-pending stage

- **WHEN** `detect_stage()` reports a format-less devkit row as `sync-pending`
- **THEN** the finder records `format: devkit` and the sweep dispatches the
  developerkit spec-sync agent for that spec rather than `/opsx:sync`
- **AND** a successful sync records the `spec-sync` analysis source in the
  spec's knowledge graph before its resulting documentation diff is committed
  and proposed

#### Scenario: OpenSpec sync-pending finding

- **WHEN** the common dashboard scan reports an active OpenSpec change as
  `sync-pending`
- **THEN** the finder records `format: openspec` and the sweep dispatches
  `/opsx:sync <change-id>` for that change

#### Scenario: A draft adds a title line to an existing canonical spec

- **WHEN** a sync exits zero and its draft adds a top-level
  `# <capability> Specification` line to an `openspec/specs/<capability>/spec.md`
  that already existed at base
- **THEN** the action rejects the draft by raising an error naming the spec
  path, and no commit, push, or pull request is attempted for that finding
- **AND** the sweep continues to other findings

#### Scenario: A draft that adds a newly created canonical spec lands

- **WHEN** a sync exits zero and its draft creates a new
  `openspec/specs/<capability>/spec.md` that did not exist at base
- **THEN** the action commits, pushes, and opens the pull request as before,
  regardless of whether the new file carries a title line

#### Scenario: A draft that edits only existing spec content lands

- **WHEN** a sync exits zero and its draft changes only requirement or
  scenario content beneath a pre-existing canonical spec's heading
- **THEN** the action commits, pushes, and opens the pull request as before

#### Scenario: A successful sync makes no diff but remains pending

- **WHEN** a format-native sync exits zero, leaves the remediation worktree
  unchanged, and the finding still evaluates to `sync-pending`
- **THEN** the action reports a remediation failure for that finding
- **AND** it does not open a pull request
- **AND** the sweep continues to other findings

#### Scenario: A no-diff finding was reconciled concurrently

- **WHEN** a format-native sync exits zero and leaves the remediation worktree
  unchanged, but the re-check no longer finds the spec `sync-pending`
- **THEN** the action completes as a no-op without opening a pull request

#### Scenario: No sync-pending specs found

- **WHEN** no repo under `--repos-root` currently reports the `sync-pending`
  stage
- **THEN** the sweep performs no spawn for this remediation category and the
  summary's `resumed_sync_pending` key is an empty list

#### Scenario: Reconciled OpenSpec change is not repeated

- **WHEN** a prior sync has made every declared structural delta visible in the
  canonical capability specs
- **THEN** the next drain sweep does not return that change as a sync-pending
  finding
