## Why

A tail task whose worker returns no report-back JSON is marked `failed` and then vanishes
from the run's outcome. `_drive` in `src/worktrail/orchestrator/live.py:6332` (and the
equivalent site in `live_run_real` at `:5020`, which is what `_dispatch_pending_tail`
actually runs) sets `task["status"] = "failed"`, pops the task from `actives`, prints
`!! <id>/<role> report parse FAILED: ...` and `break`s. Nothing downstream reads that
status: `_pipeline_scheduler` prints `=== PIPELINE RUN COMPLETE ===` and `main()`'s
`full-real` branch returns `0` unconditionally (`live.py:7752`).

Observed on run `go-20260918-212932` (spec `queue-triage-landing-teardown`): tail task 2.1
(verify-only -- pytest, `orchestrate check`, `openspec validate`, `worktrail-compile`) ran a
worker for ~24 minutes, logged `!! 2.1/implement report parse FAILED: no report-back JSON
block found`, and was followed directly by `=== PIPELINE RUN COMPLETE ===` with rc=0 -- 2.1
still unchecked in `tasks.md`, no PR, no evidence. The caller ran the verification by hand.
A verify-only tail task is exactly the case `salvage_report` cannot rescue (there is no
commit to salvage from), so the parse failure is terminal and silent.
(Work-queue brief `20260918-230954-tail-task-report-parse-failure`.)

## What Changes

- At the end of the tail pass, the pipeline scheduler collects every held-out tail-kind
  (`e2e`/`cleanup`) task that did not reach `done` and records it in the journal as
  `failed_tail_tasks` (task id -> terminal status + the last failure detail for that task).
- The completion banner is no longer unconditionally clean: when `failed_tail_tasks` is
  non-empty the run prints `=== PIPELINE RUN COMPLETE (TAIL FAILED: <ids>) ===` and the
  result dict carries `failed_tail_tasks`.
- `worktrail-live full-real` exits **non-zero** when the run ends with failed tail tasks, so
  a detached launch's exit sentinel and any CI/script caller see the failure instead of a
  clean rc=0.
- Tail tasks held back for a reason other than failure (never dispatched, still `pending`)
  are unaffected: only tasks that reached a terminal non-`done` status in this run count.

## Capabilities

### New Capabilities
- `tail-task-failure-visibility`: a tail task that ends terminal-but-not-done -- including
  the unparseable report-back case -- is recorded in the journal, named in the completion
  banner, and turns the `full-real` exit code non-zero.

### Modified Capabilities

## Impact

- `src/worktrail/orchestrator/live.py`: `_pipeline_scheduler` computes and records
  `failed_tail_tasks`, prints the failure banner, and returns the field; `main()`'s
  `full-real` branch returns the result-derived exit code.
- `tests/orchestrator/`: regression test with a fake worker returning no JSON block for a
  tail task, plus an exit-code test.
- No task-format, journal-schema-breaking, or CLI-flag change. A run whose tail tasks all
  pass behaves exactly as today (same banner, rc=0).
