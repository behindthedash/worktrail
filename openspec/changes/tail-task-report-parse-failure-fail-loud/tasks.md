## 1. Record and surface failed tail tasks

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, after the `_dispatch_pending_tail` block
      in `_pipeline_scheduler`, compute the failed tail tasks: for each id in
      `coordinator.tail_held_out_task_ids(tasks)`, look up the task in
      `(tail_res or {}).get("tasks", tasks)` and keep it when its status is terminal
      (`orchestrate.TERMINAL`) and not `done`; map the id to its status plus the detail of
      the last matching failure entry in the journal. Write the mapping to the journal as
      `failed_tail_tasks` via `progress.atomic_write_text` (pop the key when the mapping is
      empty). Replace the unconditional `=== PIPELINE RUN COMPLETE ===` print with the
      failure-naming banner when the mapping is non-empty, and add `failed_tail_tasks` to the
      returned dict. In `main()`'s `full-real` branch, capture `full_real(...)`'s return value
      and `return 1` when it reports a non-empty `failed_tail_tasks`, else `return 0`.
      (Requirements: Failed tail tasks are recorded in the journal; The completion banner
      names failed tail tasks; full-real exits non-zero when tail tasks failed.)
      In `tests/orchestrator/test_tail_task_failure_visibility.py` (new), reuse the fake-spawn
      repo harness used by `tests/orchestrator/test_tail_dispatch_only.py` to cover: a tail
      task whose fake worker returns output with no report-back JSON block (journal gets
      `failed_tail_tasks` with that id, banner names it, unqualified banner absent); a clean
      tail run (no `failed_tail_tasks` key, unqualified banner printed); a held-out tail task
      left `pending` (not listed); and `main(["full-real", ...])` returning non-zero for the
      failing run and `0` for the clean one, with `full_real` patched to return each result
      shape.
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_tail_task_failure_visibility.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate tail-task-report-parse-failure-fail-loud --strict` and
      `worktrail-compile openspec/changes/tail-task-report-parse-failure-fail-loud`.
