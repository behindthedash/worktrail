## 1. RunPlan-backed stale check for OpenSpec pending tasks

- [x] 1.1 In `src/worktrail/router/dashboard.py`, add a helper (mirroring `_pending_impl_stale`'s
      shape) that, given a `change_dir` and its loaded OpenSpec `tasks`, computes
      `conductor.runplan.fingerprint(change_dir, tasks)`, looks up
      `conductor.runplan.load_cached(conductor.compile.default_cache_dir(repo), spec_id, fp)`,
      and returns `[]` immediately on a cache miss (`None`).
      Implements "Stale detection uses only a cached RunPlan, never a model call".
- [x] 1.2 On a cache hit, merge the cached plan onto `tasks` via
      `conductor.runplan.apply_to_tasks(tasks, plan)` and, for each pending, non-tail-kind task
      in the merged result, apply the same shipped/tracked check `_pending_impl_stale` already
      uses (`_task_files_are_shipped` against the merged `files`) to decide staleness. Return the
      stale task ids.
      Implements "A pending task is stale only when its cached file scope is fully shipped".
- [x] 1.3 Wire this helper into `_safe_detect_openspec`: when pending impl tasks exist, compute
      the stale ids, subtract them from the pending count, and when every pending impl task is
      stale, set `stage="stale-bookkeeping"` with a `next_action` and `stale_task_ids` field in
      the same shape the devkit path already returns (`dashboard.py:887-896`). When at least one
      pending impl task remains non-stale, keep `stage="ready-to-implement"` as today. Implements
      "OpenSpec stale-bookkeeping reporting matches the devkit path's shape".
- [x] 1.4 Confirm the new code path degrades to today's behavior (no exception, no stale
      detection) if `conductor.runplan`/`conductor.compile` are not importable, matching the
      `_HAVE_LOADER`-gated pattern already used for the devkit task loader.

## 2. Tests

- [x] 2.1 In `tests/router/test_dashboard.py`, add a fixture that writes a cached `RunPlan` JSON
      file under a temp `<repo>-worktrees/runplans/` directory (via `conductor.runplan.store` or
      by constructing the expected fingerprint directly) so tests can exercise cache-hit paths
      without a model call.
- [x] 2.2 Test: pending OpenSpec task whose cached file scope is fully git-tracked and present on
      disk → `_safe_detect_openspec` reports `stage="stale-bookkeeping"` with the task id in
      `stale_task_ids`.
- [x] 2.3 Test: pending OpenSpec task whose cached file scope includes a missing/untracked file →
      `stage="ready-to-implement"`, task not in any stale list.
- [x] 2.4 Test: no cached RunPlan for the change's current fingerprint (cache miss) → behavior is
      unchanged from before this change (`stage="ready-to-implement"` for any pending task,
      regardless of what actually shipped).
- [x] 2.5 Test: cached RunPlan's task-id set has drifted from the current `tasks.md` (simulate by
      caching a plan for a different task list) → `apply_to_tasks` rejects the plan and no task
      is classified stale, same as the cache-miss case.
- [x] 2.6 Test: mixed change with some pending impl tasks stale and at least one not stale →
      `stage="ready-to-implement"` (not `stale-bookkeeping`), matching the devkit path's existing
      `pending_impl_real > 0` branch behavior.

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_dashboard.py` and confirm all new
      and existing tests pass.
- [x] 3.2 [e2e] Run the full suite: `PYTHONPATH=src pytest -q && PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`.
- [x] 3.3 [e2e] Run `openspec validate openspec-stale-bookkeeping-detection --strict` and
      confirm the change validates cleanly.
