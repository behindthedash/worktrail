## 1. Journal events for the two repair-path safety nets, plus their aggregation

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`: change
      `_carry_squash_merged_dependencies` to return `dict | None` instead of
      `None` unconditionally -- return a `{"event": "checklist_conflict_resolved",
      "task": task["id"], "at": round(time.time(), 3)}` event when
      `_resolve_tasks_md_checklist_conflict` resolves the conflict, `None` from
      every other return point (unchanged behavior otherwise, including the
      `add_stacked_worktree` call site at line ~1880, which keeps discarding the
      return value per design.md's Non-Goals). In `_require_dependency_files_with_repair`,
      capture that return value from its own call to `_carry_squash_merged_dependencies`;
      after the retry's `_require_dependency_files` re-check succeeds (does not
      raise), return a list that includes a `{"event": "worktree_drift_repaired",
      "task": task["id"], "at": round(time.time(), 3)}` event plus the checklist
      event (if any) plus any `dependency_file_drift` events the re-check itself
      returned. If the re-check still raises, propagate the exception unchanged --
      no event is journaled for that attempt. Both existing call sites
      (`ensure_wt`, `_ensure_wt`) already extend `entries` and call
      `record()`/`_record()` with whatever list they get back, so no change is
      needed at either call site itself.
- [x] 1.2 In `src/worktrail/orchestrator/safety_net_report.py`: extend `scan()` to
      also break down `worktree_drift_repaired` and `checklist_conflict_resolved`
      fire counts by `task` (mirroring `dependency_file_drift_by_dep_id`'s shape,
      keyed on `event.get("task", "unknown")`), and extend `render()` to print
      those breakdowns when non-empty, following the existing
      `dependency_file_drift_by_dep_id` section as the template.
- [ ] 1.3 Add regression tests: `_carry_squash_merged_dependencies` returns the
      `checklist_conflict_resolved` event on a tasks.md-only conflict and `None`
      on a normal (no-conflict) carry or a non-tasks.md conflict abort;
      `_require_dependency_files_with_repair` returns a list containing
      `worktree_drift_repaired` when the repair resolves a retained worktree's
      missing dependency content, and still raises
      `WorktreeMissingDependencyFileError` with no returned events when the
      repair does not resolve it (extend
      `tests/orchestrator/test_stacked_worktree_squash_carry.py` and/or
      `tests/orchestrator/test_resume.py`, whichever already covers these
      functions). Add `safety_net_report.py` aggregation tests for both new
      event types in `tests/orchestrator/test_safety_net_report.py`, following
      `test_scan_aggregates_dependency_file_drift_across_runs`'s shape. Full
      existing suite passes.
