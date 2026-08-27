## Purpose

Detects when an open OpenSpec change's `MODIFIED`/`RENAMED`-target delta for a requirement has
been overtaken by an already-archived sibling change that touched the same requirement more
recently, so the drift is surfaced before that stale delta is synced or archived and silently
reverts the sibling's newer scenarios onto the canonical spec.

## ADDED Requirements

### Requirement: Drift detection compares only requirement names and git commit ordering, never a model call

For each open OpenSpec change's delta file under `specs/<capability-path>/spec.md`, the drift
check SHALL identify every requirement name the delta declares as `MODIFIED` or as a `RENAMED
Requirements` `TO:` target under that capability path. For each such name, it SHALL look for an
archived change under `openspec/changes/archive/**/specs/<capability-path>/spec.md` (same
capability path) whose own delta declares that same requirement name as `ADDED`, `MODIFIED`, or a
`RENAMED Requirements` `TO:` target. The check SHALL rely exclusively on parsing on-disk delta
headings and local `git log` commit timestamps for the two files being compared. It SHALL NOT
invoke a model call, `compile_run_plan`, or any network operation.

#### Scenario: No archived sibling touches the same requirement name

- **WHEN** an open change's delta declares a `MODIFIED` requirement whose name does not appear as
  an `ADDED`, `MODIFIED`, or `RENAMED ... TO:` target in any archived change's delta under the same
  capability path
- **THEN** the drift check reports no drift for that requirement

#### Scenario: A capability path has no archived changes at all

- **WHEN** an open change's delta targets a capability path with no matching directory under
  `openspec/changes/archive/**/specs/`
- **THEN** the drift check reports no drift for that change, and makes no git calls beyond
  resolving the archive directory listing

### Requirement: A shared requirement name is drifted only when the archived sibling's commit postdates the open change's delta

When an open change's delta and an archived change's delta both touch the same requirement name
under the same capability path, the drift check SHALL compare two git timestamps: the last commit
that modified the open change's delta file, and the commit that added the archived change's delta
file under `openspec/changes/archive/`. A requirement SHALL be flagged as drifted only when the
archived-change commit is strictly newer than the open change's delta's last commit. An open
change's delta file with no commit history (newly created, uncommitted) SHALL be treated as having
no prior baseline to drift from and SHALL NOT be flagged.

#### Scenario: The archived sibling was committed after the open change's delta was last touched

- **WHEN** an open change's delta was last committed at time T1, and an archived sibling change
  touching the same requirement name under the same capability path was committed (added under
  `openspec/changes/archive/`) at time T2, where T2 is after T1
- **THEN** the drift check flags that requirement as drifted for the open change

#### Scenario: The archived sibling predates the open change's own last touch

- **WHEN** the archived sibling's commit time is before or equal to the open change's delta's last
  commit time
- **THEN** the drift check does not flag that requirement — the open change's delta was authored
  or last revised with the sibling's content already reflected, or after it, so no information is
  at risk of being silently reverted

#### Scenario: The open change's delta file has no commit history yet

- **WHEN** the open change's delta file exists only in the working tree with no prior commit
- **THEN** the drift check does not flag any requirement in that file, regardless of any archived
  sibling's commit time

### Requirement: Drift is reported as an additive warning, independent of the change's existing dashboard stage

The `worktrail-go` orientation scan SHALL report drift findings for an OpenSpec change as an
additive field alongside that change's existing `stage` value (e.g. `needs-tasks`,
`ready-to-implement`, `stale-bookkeeping`, `verify-pending`, `sync-pending`, `complete`). Detecting
drift SHALL NOT change which `stage` value is reported. When one or more requirements are
flagged, the scan SHALL include the drifted requirement names and, for each, the id of the
archived change whose delta caused the drift.

#### Scenario: A change mid-implementation has a drifted requirement

- **WHEN** an open OpenSpec change has pending tasks (stage `ready-to-implement`) and the drift
  check flags one of its `MODIFIED` requirements
- **THEN** the scan continues to report `stage: "ready-to-implement"` and additionally reports the
  drifted requirement name and the causing archived change's id

#### Scenario: A change ready to archive has a drifted requirement

- **WHEN** an open OpenSpec change has stage `complete` (all tasks done, delta reconciled) and the
  drift check flags one of its `MODIFIED` requirements
- **THEN** the scan continues to report `stage: "complete"` and additionally reports the drift, so
  the drift is visible before the change is archived

#### Scenario: No requirement in the change is drifted

- **WHEN** the drift check finds no drifted requirement for an open change
- **THEN** the scan reports that change with no drift field set, unchanged from current behavior
