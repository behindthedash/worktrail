## MODIFIED Requirements

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
