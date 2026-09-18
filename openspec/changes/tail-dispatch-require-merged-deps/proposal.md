## Why

`_mark_integrate_complete_if_terminal` (`src/worktrail/orchestrator/integrate.py:554`) sets
`integrate_complete` as soon as every planned group is in `TERMINAL_GROUP_STATES =
{"OPEN", "MERGED", "QUARANTINED"}` (`integrate.py:43`), and `_pipeline_scheduler`
(`src/worktrail/orchestrator/live.py:6407`) then calls `_dispatch_pending_tail` with no
per-task check that a tail task's dependencies actually landed. A tail-kind (`e2e`/`cleanup`)
task therefore runs against `<remote>/<base>` even when the group carrying its `deps` was
QUARANTINED (conflict, task failure, budget) or its PR is still OPEN. The e2e task then
verifies a base that does not contain the work it was written to verify: it either fails
spuriously (burning a worker and a PR slot on a red result) or, worse, passes and ticks the
checkbox, making the run look done while the real work sits in quarantine. No fix has landed
since the incident brief (`git log --since=2026-09-16 -- src/worktrail/orchestrator` is empty
of it; PRs #494/#687/#688 only dedup tail PRs). (Work-queue brief
`20260916-182955-worktrail-orchestrator-tail-kind-task`.)

## What Changes

- Tail dispatch is gated per task: a held-out tail task is dispatched only when every
  non-tail task it depends on belongs to a group whose journal state is `MERGED`. A tail task
  with a dependency in an `OPEN` or `QUARANTINED` group is **held**, not skipped and not
  failed: it stays `pending`, so a later resume (after the PR merges or the quarantine is
  resolved) picks it up unchanged.
- Held tail tasks are excluded from the tail `live_run_real` pass rather than pre-marked
  complete, so their own dependents stay blocked too.
- The journal records why the tail did not run: `pending_tail_blocked` maps each held task id
  to the non-merged group(s) and their state, and the scheduler prints a `NOTE` naming them.
  When every held-out tail task is blocked, no tail pass is started at all.
- `integrate_complete` semantics are unchanged (it still reflects the impl groups only);
  `pending_tail_tasks`/`pending_tail_reason` keep listing the outstanding tail work.

## Capabilities

### New Capabilities
- `tail-dispatch-merged-deps-gate`: tail-kind tasks dispatch only once the groups carrying
  their dependencies are `MERGED`; otherwise they are held with a recorded, visible reason.

### Modified Capabilities

## Impact

- `src/worktrail/orchestrator/coordinator.py`: new pure helper computing the blocked tail
  task ids from tasks, planned groups, and journal group states.
- `src/worktrail/orchestrator/live.py`: `_dispatch_pending_tail` takes the planned groups,
  applies the gate, writes `pending_tail_blocked`, and excludes held tasks from the tail pass
  (`live_run_real` gains an exclusion seam).
- `tests/orchestrator/`: new tests for the helper and for dispatch/hold behaviour.
- No CLI or task-format change. Runs where every group is `MERGED` behave exactly as today.
