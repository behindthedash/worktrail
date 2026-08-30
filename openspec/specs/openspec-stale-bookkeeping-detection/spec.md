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

### Requirement: A pending task is stale only when its cached file scope shows evidence of shipped content

For each pending, non-tail-kind OpenSpec task with cached file scope, the dashboard scan SHALL
merge that scope onto the loaded task list via `conductor.runplan.apply_to_tasks` (the same
merge used by the orchestrator's own compile path) and then apply the strengthened stale check to
the merged files: a declared file counts as shipped only when it is git-tracked on the base
branch, present on disk, AND its most recent commit is not older than the change's own creation
baseline — the timestamp of the oldest commit that introduced the change's directory
(`openspec/changes/<slug>/`). A file whose most recent commit predates that baseline already
existed before the change was created and was never touched by it; mere existence and tracking is
no longer sufficient evidence that the change's own work shipped. A task SHALL be classified stale
only when ALL of its merged files pass the strengthened check; a task with no merged file scope,
or with at least one file that is missing, untracked, unmerged, or shows no evidence of a
post-baseline change, SHALL NOT be classified stale.

A file with no commit history before the baseline, or whose earliest commit lands at or after the
baseline, still counts as shipped (the file did not exist when the change was created, so its mere
presence now is itself evidence of the change's work). This preserves the brand-new-file case,
including a file that reached its current path via a git rename after the baseline — the
destination path's own history starts at the rename, which is itself a post-baseline event.

#### Scenario: A declared file already existed unchanged since before the change was created
- **WHEN** a pending task's cached file scope includes a file that is git-tracked and present on
  disk, but whose most recent commit predates the change's own creation baseline
- **THEN** the task is NOT classified stale and remains in the orchestrator-eligible pending count

#### Scenario: A declared file is genuinely new since the change's creation
- **WHEN** a pending task's cached file scope includes a file with no commit history before the
  change's creation baseline (created at or after it)
- **THEN** that file counts as shipped, and the task is classified stale only if every other
  declared file also passes the strengthened check

#### Scenario: A declared pre-existing file's content changed after the change was created
- **WHEN** a pending task's cached file scope includes a file that existed before the change's
  creation baseline but has at least one commit after that baseline
- **THEN** that file counts as shipped, and the task is classified stale only if every other
  declared file also passes the strengthened check

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

#### Scenario: No creation baseline can be established for the change
- **WHEN** the change's directory has no commit history yet (never committed)
- **THEN** no declared file can show evidence of a post-baseline change, so no task in that
  change is classified stale for this scan

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

