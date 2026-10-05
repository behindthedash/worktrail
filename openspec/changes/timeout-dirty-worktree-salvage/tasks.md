## 1. Recover safely scoped timeout work

- [ ] 1.1 Extend the shared timeout recovery in
      `src/worktrail/orchestrator/live.py` so implement/fix timeouts first retain
      an already-advanced task-branch HEAD, otherwise inspect Git's
      machine-readable dirty state and create one recovery commit only when every
      changed path maps exactly to the task's declared `files` scope. Route the
      recovered result through the normal implement/fix report, pre-commit,
      review, journal, and cleanup flow in both `live_run_real` and
      `_pipeline_scheduler`; retain the present failed-timeout path, untouched
      worktree, and an actionable refusal reason for empty, out-of-scope, or
      unclassifiable changes and for review/cleanup roles. Add helper-level Git
      fixture coverage for scoped edits, out-of-scope edits, untracked/deleted
      paths, no-op worktrees, and non-implement/fix roles in
      `tests/orchestrator/test_resilience_helpers.py`; add focused execution-path
      regressions in `tests/orchestrator/test_timeout_dirty_worktree_salvage.py`
      proving both schedulers continue a salvaged task to review and preserve a
      refused timeout as failed. (Requirements: Timed-out scoped implementation
      work is recovered; Unsafe timeout work remains failed and preserved.)
      files: src/worktrail/orchestrator/live.py tests/orchestrator/test_resilience_helpers.py tests/orchestrator/test_timeout_dirty_worktree_salvage.py

## 2. Verify recovery behavior

- [ ] 2.1 [e2e] Run the focused timeout-salvage and resilience-helper tests,
      then `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, `python3
      scripts/ci/ruff_pinned.py check .`, and `python3
      scripts/ci/ruff_pinned.py format --check .`; confirm the recovered path
      still requires ordinary review and every refusal leaves the task failed.
