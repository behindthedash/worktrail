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
only when ALL of its merged files pass the strengthened check AND the task also passes the
behavior-evidence check; a task with no merged file scope, or with at least one file that is
missing, untracked, unmerged, or shows no evidence of a post-baseline change, SHALL NOT be
classified stale.

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
  declared file also passes the strengthened check and the task passes the behavior-evidence check

#### Scenario: A declared pre-existing file's content changed after the change was created
- **WHEN** a pending task's cached file scope includes a file that existed before the change's
  creation baseline but has at least one commit after that baseline
- **THEN** that file counts as shipped, and the task is classified stale only if every other
  declared file also passes the strengthened check and the task passes the behavior-evidence check

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
SHALL report `stage: "stale-bookkeeping"` for that change, with a `next_action` describing the
closeout, a `stale_task_ids` field listing the stale task ids — the same fields the devkit
`_pending_impl_stale` path already produces for `stage: "stale-bookkeeping"` — and a
`stale_evidence` field recording the strength of the evidence the verdict rests on.

`stale_evidence` SHALL be `"behavior"` when every stale task carried extractable identifier
evidence that was found present, and `"files-only"` when at least one stale task carried no
extractable identifier evidence and was classified on file-level evidence alone. When
`stale_evidence` is `"behavior"` the `next_action` SHALL describe that the files are already
merged and only the task status needs to be flipped to completed, as today. When it is
`"files-only"` the `next_action` SHALL state that the evidence is file-level only and ask the
operator to confirm the behavior actually shipped before closing, rather than asserting that the
work is merged. The `stage` string SHALL be `"stale-bookkeeping"` in both cases.

When at least one pending impl task is not stale, the scan SHALL continue to report
`stage: "ready-to-implement"` as it does today, and SHALL NOT emit `stale_evidence`.

#### Scenario: Every pending impl task is stale
- **WHEN** an OpenSpec change has one or more pending impl tasks, all of them are classified
  stale, and every one of them carried extractable identifiers that were all found present
- **THEN** the scan reports `stage: "stale-bookkeeping"` with `stale_task_ids` listing every
  stale task id, `stale_evidence: "behavior"`, and a `next_action` describing the status-flip
  closeout

#### Scenario: Every pending impl task is stale but at least one rests on file evidence alone
- **WHEN** an OpenSpec change's pending impl tasks are all classified stale and at least one of
  them yielded no extractable identifier evidence
- **THEN** the scan reports `stage: "stale-bookkeeping"` with `stale_evidence: "files-only"` and
  a `next_action` that asks the operator to confirm the behavior shipped before closing

#### Scenario: At least one pending impl task is not stale
- **WHEN** an OpenSpec change has at least one pending impl task that is not classified stale
- **THEN** the scan reports `stage: "ready-to-implement"`, unchanged from current behavior, with
  no `stale_evidence` field

## ADDED Requirements

### Requirement: A task's claimed identifiers must be present before it is classified stale

The OpenSpec stale check SHALL extract identifier evidence from each candidate task's own text
and SHALL classify that task stale only when every extracted identifier is present in the current
on-disk content of the task's own declared files. Extraction SHALL be deterministic and offline —
no model call and no network access, consistent with the cache-only requirement above. It SHALL
take the code-like tokens the task names in backticks, excluding path-like tokens (any token
containing `/` or `.`) and tokens that merely name one of the task's own declared files.
Presence SHALL be decided by searching the text of those declared files; a declared file that
cannot be read as text SHALL be treated as containing none of the identifiers.

When a task yields no extractable identifiers, the behavior-evidence check SHALL pass and the
task's verdict SHALL rest on file-level evidence alone, which the reporting requirement records
as `stale_evidence: "files-only"`.

#### Scenario: A claimed symbol is absent from a tracked, recently-committed file
- **WHEN** a pending task names an identifier in backticks, its single declared file is
  git-tracked and has commits after the change's creation baseline (so the file-level check
  passes), but that identifier appears nowhere in the file's current content
- **THEN** the task is NOT classified stale, and a change whose only pending impl task is that
  task reports `stage: "ready-to-implement"`

#### Scenario: Every claimed symbol is present
- **WHEN** a pending task's declared files all pass the file-level check and every identifier
  extracted from the task's text is found in the content of those files
- **THEN** the task is classified stale with behavior evidence

#### Scenario: Only some claimed symbols are present
- **WHEN** a pending task names several identifiers and at least one of them is absent from every
  one of its declared files
- **THEN** the task is NOT classified stale

#### Scenario: A prose-only task names no identifiers
- **WHEN** a pending task's text contains no backticked code-like token (or only path-like tokens
  naming its own declared files)
- **THEN** the behavior-evidence check passes and the task's verdict rests on the file-level
  check alone

#### Scenario: Extraction and presence checking make no model call
- **WHEN** the behavior-evidence check runs for any candidate task
- **THEN** it reads only the task text and the declared files' on-disk content, invoking no model
  and no network call
