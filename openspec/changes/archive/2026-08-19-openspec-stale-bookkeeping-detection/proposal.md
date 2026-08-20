## Why

`dashboard._pending_impl_stale()` (`src/worktrail/router/dashboard.py:506`) protects the devkit
task format from stale bookkeeping: a pending `impl` task whose declared `files:` are already
git-tracked and present on the base branch means the code shipped out-of-band and the task's
`status:` was simply never flipped to `completed`. Detecting this drops the task from the
orchestrator-eligible set and reports `stage="stale-bookkeeping"` instead, so the fan-out never
re-implements code that already merged.

`dashboard._safe_detect_openspec()` (`src/worktrail/router/dashboard.py:1017`) has no equivalent
check. `taskformats/openspec/schema.py`'s `ParsedTask` carries no per-task file scope by design —
its module docstring is explicit that OpenSpec's `tasks.md` has "no per-task frontmatter, no file
scope, no explicit dependency edge... those come from the compiled RunPlan, not from the
authoring artifact." As a direct result, any pending OpenSpec task is unconditionally reported
`stage="ready-to-implement"` even when its code already shipped, risking the orchestrator
re-implementing already-merged code — the exact correctness gap `_pending_impl_stale`'s own
docstring says must never happen, just left uncovered for the other task format.

## What Changes

- Extend `_safe_detect_openspec` to run an equivalent stale-bookkeeping check for pending OpenSpec
  impl tasks, using the compiled `RunPlan` cache (`conductor.runplan.load_cached` +
  `conductor.runplan.fingerprint`) as the source of per-task file scope that `ParsedTask` itself
  cannot carry.
- On a cache hit, merge the cached plan onto the loaded tasks with `conductor.runplan.apply_to_tasks`
  (the same merge+drift-rejection the real orchestrator uses in `orchestrator/live.py`) and apply
  the devkit path's own git-tracked-and-present-on-disk check to the merged `files`.
- On a cache miss (change never compiled by the orchestrator, or `tasks.md` edited since the last
  compile — `apply_to_tasks` already rejects a drifted task set), fall back to today's behavior:
  no stale detection for that task, reported `ready-to-implement` as it is now. Strictly additive,
  never worse than current behavior.
- `dashboard.py` only ever calls `runplan.load_cached` (a local file read + hash, no model call).
  `compile_run_plan` (the model-call path) is never invoked from the dashboard scan path — that
  boundary stays exactly where it already lives, inside the orchestrator.
- When every remaining pending OpenSpec task is stale, report `stage="stale-bookkeeping"` with a
  `next_action` and `stale_task_ids` field, matching the devkit path's shape exactly so downstream
  consumers of the dashboard scan (stage rollups, remediation tables) need no format-specific
  branching.

## Capabilities

### New Capabilities
- `openspec-stale-bookkeeping-detection`: pending-OpenSpec-task staleness detection in the
  router dashboard scan, using cached RunPlan file scope to detect code that shipped out-of-band
  without its task status being flipped to completed.

### Modified Capabilities
(none — no existing capability spec currently documents `_safe_detect_openspec`'s stage
classification behavior for pending OpenSpec tasks)

## Impact

- `src/worktrail/router/dashboard.py` — `_safe_detect_openspec` gains a RunPlan-cache-backed
  stale check; no change to `_pending_impl_stale`/`_pending_tail_stale` (devkit path unaffected).
- `src/worktrail/conductor/runplan.py` — consumed read-only (`load_cached`, `fingerprint`,
  `apply_to_tasks`); no changes to its own logic.
- Tests: new coverage under `tests/router/` (or wherever `dashboard.py`'s existing OpenSpec-path
  tests live) for cache-hit/stale, cache-hit/not-stale, cache-miss, and drifted-cache-rejected
  cases.
- No change to the orchestrator's own compile/apply pipeline, the devkit adapter, or any
  user-facing CLI surface — this is confined to the dashboard's read-only scan.
