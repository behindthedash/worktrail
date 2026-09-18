## 1. Gate tail dispatch on MERGED dependency groups

- [ ] 1.1 In `src/worktrail/orchestrator/coordinator.py`, beside `tail_held_out_task_ids`,
      add the pure helper `tail_blocked_task_ids(tasks, groups, journal_groups) -> dict`:
      for each id from `tail_held_out_task_ids(tasks)`, look up every non-tail dep's group
      (via each group's `tasks` list) and its `journal_groups[name]["state"]`; collect
      `"<group>=<STATE>"` (`STATE` is `MISSING` when the group has no journal record) for
      each group whose state is not `MERGED`. Deps that are tail-kind are ignored. Return
      only the ids with a non-empty list. No I/O.
      In `src/worktrail/orchestrator/live.py`: add an `exclude: list | None = None`
      parameter to `live_run_real` that drops the named ids from the reloaded task list
      before scheduling (no status change on disk). Give `_dispatch_pending_tail` a
      `groups: list` parameter; read the journal's `groups` map, call
      `coordinator.tail_blocked_task_ids`, print a `NOTE` naming each held id and its
      blocking `<group>=<STATE>` entries, write `pending_tail_blocked` into the journal via
      `progress.atomic_write_text` (pop the key when nothing is blocked), return `None`
      when every held-out tail task is blocked, and otherwise pass the blocked ids as
      `exclude=` to `live_run_real`. Update the call site in `_pipeline_scheduler` to pass
      `groups`.
      (Requirements: Tail tasks dispatch only when their dependency groups are MERGED;
      Held tail tasks are excluded without unblocking their dependents; The hold is
      recorded and reported.)
      In `tests/orchestrator/test_coordinator_extras.py`, add helper tests for the four
      scenarios of the first requirement: all MERGED (empty result), QUARANTINED dep group,
      OPEN dep group, and a cleanup task whose only dep is an e2e task (not blocked).
      In `tests/orchestrator/test_tail_dispatch_only.py`, add tests using the file's
      existing fake-spawn repo harness: a quarantined dependency group holds the tail task
      (spawn never sees it, status stays `pending`, `pending_tail_blocked` written, `NOTE`
      printed); a mixed run dispatches only the unblocked tail task and not a task
      depending on the held one; all-blocked returns `None`; a resume with the group now
      `MERGED` dispatches the task and removes `pending_tail_blocked`.
      files: src/worktrail/orchestrator/coordinator.py, src/worktrail/orchestrator/live.py, tests/orchestrator/test_coordinator_extras.py, tests/orchestrator/test_tail_dispatch_only.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate tail-dispatch-require-merged-deps --strict` and
      `worktrail-compile openspec/changes/tail-dispatch-require-merged-deps`.
