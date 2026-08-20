# openspec-stale-bookkeeping-detection Specification

## Purpose
Detects pending OpenSpec tasks whose implementation already shipped and merged out-of-band, so
the dashboard scan stops reporting them ready-to-implement and the orchestrator is never
dispatched to re-implement already-merged code.
## Requirements
### Requirement: Stale detection uses only a cached RunPlan, never a model call

When the OpenSpec dashboard scan (`_safe_detect_openspec`) evaluates a pending `impl`-kind task,
it SHALL look up that task's file scope exclusively via `conductor.runplan.load_cached` keyed on
`conductor.runplan.fingerprint(change_dir, tasks)`. It SHALL NOT invoke `compile_run_plan` or any
other path that can trigger a model call. A cache miss SHALL be treated as "no file scope
available" rather than triggering a compile.

#### Scenario: No cached RunPlan exists for the change
- **WHEN** the scan evaluates a change whose content fingerprint has no matching cache entry
  under `<repo>-worktrees/runplans/` (never compiled, or `tasks.md`/`proposal.md`/`design.md`/
  `specs/**` edited since the last compile)
- **THEN** the scan reports the change's pending tasks exactly as it does today (no stale
  detection applied to them), and no model call is made

#### Scenario: A cached RunPlan exists and matches the current content fingerprint
- **WHEN** the scan evaluates a change whose fingerprint matches a cached RunPlan
- **THEN** the scan reads that cached plan's per-task file scope without compiling or calling a
  model

### Requirement: A pending task is stale only when its cached file scope is fully shipped

For each pending, non-tail-kind OpenSpec task with cached file scope, the dashboard scan SHALL
merge that scope onto the loaded task list via `conductor.runplan.apply_to_tasks` (the same
merge used by the orchestrator's own compile path) and then apply the devkit stale check (every
declared file is git-tracked on the base branch and present on disk) to the merged files. A task
SHALL be classified stale only when ALL of its merged files pass that check; a task with no
merged file scope, or with at least one file that is missing, untracked, or unmerged, SHALL NOT
be classified stale.

#### Scenario: All declared files for a pending task are shipped and tracked
- **WHEN** a pending task's cached file scope is non-empty and every listed file is git-tracked
  on the base branch and present on disk
- **THEN** the task is classified stale and excluded from the orchestrator-eligible pending count

#### Scenario: At least one declared file is missing or untracked
- **WHEN** a pending task's cached file scope includes at least one file that is not git-tracked
  on the base branch, or not present on disk
- **THEN** the task is NOT classified stale and remains in the orchestrator-eligible pending count

#### Scenario: Cached plan's task set has drifted from the current tasks.md
- **WHEN** `apply_to_tasks` rejects the cached plan because the task ids in the plan no longer
  match the task ids parsed from the current `tasks.md` (or merging the plan's edges would
  create a cycle)
- **THEN** no task in that change is classified stale for this scan, matching the cache-miss
  behavior

### Requirement: OpenSpec stale-bookkeeping reporting matches the devkit path's shape

When every remaining pending impl task in an OpenSpec change is classified stale, the scan
SHALL report `stage: "stale-bookkeeping"` for that change, with a `next_action` describing that
the files are already merged and only the task status needs to be flipped to completed, and a
`stale_task_ids` field listing the stale task ids — the same fields the devkit
`_pending_impl_stale` path already produces for `stage: "stale-bookkeeping"`. When at least one
pending impl task is not stale, the scan SHALL continue to report `stage: "ready-to-implement"`
as it does today.

#### Scenario: Every pending impl task is stale
- **WHEN** an OpenSpec change has one or more pending impl tasks and all of them are classified
  stale
- **THEN** the scan reports `stage: "stale-bookkeeping"` with `stale_task_ids` listing every
  stale task id and a `next_action` describing the status-flip closeout

#### Scenario: At least one pending impl task is not stale
- **WHEN** an OpenSpec change has at least one pending impl task that is not classified stale
- **THEN** the scan reports `stage: "ready-to-implement"`, unchanged from current behavior

