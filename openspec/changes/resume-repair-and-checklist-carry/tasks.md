## 1. Repair retained-worktree drift, safely carry checklist conflicts, and stamp terminal_status for failed reports

- [ ] 1.1 `ensure_wt` (live_run_real) and `_ensure_wt` (`_pipeline_scheduler`): in the
      retained-worktree (`else`) branch, catch `WorktreeMissingDependencyFileError` from
      the first `_require_dependency_files` call, re-attempt
      `_carry_squash_merged_dependencies` once (when `remote`/`base` are available), and
      re-run `_require_dependency_files`; re-raise only if it still fails
      (src/worktrail/orchestrator/live.py). `_carry_squash_merged_dependencies`: before
      the fail-loud abort on a merge conflict, detect via `git diff --name-only
      --diff-filter=U` whether the conflict is confined entirely to
      `openspec/changes/<change_id>/tasks.md`; if so, resolve deterministically by
      taking the union of checked task lines from both sides, commit, and continue
      instead of aborting (src/worktrail/orchestrator/live.py). `_apply_step_commit` and
      `live_run()`'s `drive()` closure: stamp `report_fields["terminal_status"] =
      "failed"` when the coordinator transition's new status is `"failed"`, mirroring
      the existing `"escalated"` stamp (src/worktrail/orchestrator/live.py). Add
      regression tests for: a retained worktree repairing itself after a dependency
      squash-merges post-creation; a tasks.md-only conflict resolving via checklist
      union; a conflict touching tasks.md plus another file still failing loud; and a
      normal `status: "failed"` report (e.g. `ROLE_FIX`) producing a journal entry
      `clear_tasks()` recognizes as clearable
      (tests/orchestrator/test_stacked_worktree_squash_carry.py or a new focused test
      module). Full existing suite passes.
