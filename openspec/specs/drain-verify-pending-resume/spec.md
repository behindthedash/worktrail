## Purpose

`drain.py`'s unattended queue-drainer detects and resumes specs stuck in the `verify-pending`
stage (implementation complete, but at least one orchestrator group's PR has not yet landed on
the base branch) across a configured `--repos-root`, so this class of stalled work does not
require a human to notice the `/go` dashboard and manually re-run `full-real`.

## Requirements

### Requirement: Detect verify-pending specs across a repos-root
The system SHALL scan every repository under a configured `--repos-root` (or a single
`--go-repo` when given) and identify every spec whose current stage, as determined by the
existing dashboard stage-detection logic, is `verify-pending` — implementation complete but
at least one orchestrator group's PR has not yet landed on the base branch.

#### Scenario: Repo has a verify-pending spec
- **WHEN** a repo under `--repos-root` contains a spec whose run journal has
  `integrate_complete: true` and at least one group whose state is not `MERGED` and whose PR
  has not landed on the base branch
- **THEN** that spec is included in the detected set, identified by its repo and spec id

#### Scenario: Repo has no verify-pending specs
- **WHEN** every spec in every repo under `--repos-root` is in a stage other than
  `verify-pending` (e.g. `done`, `ready-to-implement`, `tail-pending`)
- **THEN** the detected set is empty and no resume is attempted for that repo

#### Scenario: Spec's verify-pending journal was already resolved out-of-band
- **WHEN** every non-`MERGED` group's PR in a spec's run journal is already present in the base
  branch's commit history (e.g. a manual merge)
- **THEN** that spec is NOT included in the detected set — this mirrors the existing
  stale-bookkeeping exclusion the dashboard already applies

### Requirement: Resume detected verify-pending specs via full-real
For every spec detected as `verify-pending`, the system SHALL re-invoke
`worktrail-live full-real` for that spec's repo and resolved spec path, without a `--fresh`
flag, so the run resumes verify → merge → cleanup from its existing run journal rather than
restarting the fan-out.

#### Scenario: One verify-pending spec is resumed
- **WHEN** a spec is detected as `verify-pending` and its spec folder still exists under
  `docs/specs/<id>` or `openspec/changes/<id>` in that repo
- **THEN** the system invokes `worktrail-live full-real` with that repo, the resolved
  `--spec` path, the repo's configured base branch, and the active agent — with no `--fresh`
  flag

#### Scenario: A spec's folder has since been deleted or archived
- **WHEN** a spec is detected as `verify-pending` by journal inspection but its spec folder no
  longer exists at either the devkit or OpenSpec location
- **THEN** that spec is silently skipped and no resume command is issued for it

### Requirement: One spec's resume failure does not block others
The system SHALL attempt to resume every detected verify-pending spec independently; a
non-zero exit or failure resuming one spec SHALL NOT prevent the remaining detected specs
from being attempted.

#### Scenario: One of several detected specs fails to resume
- **WHEN** two or more specs are detected as `verify-pending` and the `full-real` resume for
  one of them exits non-zero
- **THEN** the system still attempts the resume for every other detected spec, and reports
  the failing spec's exit code in its result

### Requirement: Verify-pending sweep runs alongside the existing quarantine sweep
The unattended queue-drainer SHALL run the verify-pending sweep at the same points in its
loop where it already runs the existing budget-exhausted-quarantine sweep — once before the
queue-draining loop starts, and once after it finishes when at least one queue iteration ran.

#### Scenario: Drain invocation with a configured repos-root
- **WHEN** `drain()` is invoked with `--repos-root` set and not in dry-run mode
- **THEN** both the quarantine sweep and the verify-pending sweep run before the queue loop
  starts, and both run again after the loop finishes if the loop executed at least one
  iteration

#### Scenario: Drain invocation without a configured repos-root
- **WHEN** `drain()` is invoked without `--repos-root`
- **THEN** neither the quarantine sweep nor the verify-pending sweep runs, matching today's
  existing gating for the quarantine sweep
