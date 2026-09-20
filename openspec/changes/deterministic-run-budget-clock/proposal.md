## Why

The orchestrator's run-budget cut is tested against real wall-clock time. In
`tests/orchestrator/test_pipeline_budget_partial_group.py`, three tests
(lines 253, 306 and 384) pair a sub-second `run_budget` with a `FakeSpawn(sleep_after=...)`
that calls `time.sleep(1.0)` after a task's commit lands, so the budget expires "between
ticks". The file's own comment (lines 242-244) asserts this is safe -- "The margin is wide
... leaves no realistic room for flakiness either direction" -- but that is an argument
about a loaded CI runner's timing, not a guarantee: the margin is 0.5s of *real* time
against git worktree creation, `git add`/`commit` subprocesses and thread scheduling. A
runner that stalls anywhere in TASK-001's or TASK-002's git work crosses the 0.5s budget one
tick early and the test fails with TASK-002 never dispatched; a run that reorders around the
sleep fails the other way. The tests also pay ~3s of real sleeping per full suite run for
nothing.

The cause is that `_budget_stop` in both `live_run_real`
(`src/worktrail/orchestrator/live.py:5358`) and `_pipeline_scheduler`
(`live.py:6627`) reads `time.time()` directly, with `run_start` captured from
`time.time()` too (`live.py:5328`, `live.py:6610`). There is no seam a test can use to
control elapsed time, so a test that wants the budget to expire at a chosen point has no
option but to burn real seconds. Both call sites already take private test seams (`_spawn`,
`_integrate_one`, `_make_verifier`), so the fix is one more of the same, not new machinery.

Confirmed unfixed on `main` as of 2026-09-20: `grep -n 'time.time()' src/worktrail/orchestrator/live.py`
still shows the direct reads at both budget sites, and none of the 11 other active
`openspec/changes/` entries touch the orchestrator tick budget. (Work-queue brief
`20260919-160337-wall-clock-flaky-budget-test`.)

## What Changes

- `live_run_real` and `_pipeline_scheduler` gain a private `_clock` seam: a zero-argument
  callable returning seconds, defaulting to `time.time`. Every read that feeds the run-budget
  decision -- `run_start`, the `now` inside `_budget_stop`, and the value stored in
  `_budget_stopped_at` -- goes through it. `_pipeline_scheduler` threads its `_clock` into the
  `live_run_real` call it makes for the tail pass, so one run has one clock.
- Nothing else changes clock source: the progress emitter's `elapsed` line, per-task
  `started_at`/duration timings and journal `at` stamps keep calling `time.time()` directly.
  They are cosmetic or audit-only and are not part of the budget decision.
- With `_clock` unset, behaviour is byte-for-byte what it is today, including the journaled
  absolute `budget_stopped_at` timestamp.
- The three wall-clock-dependent tests in `test_pipeline_budget_partial_group.py` are
  converted: `FakeSpawn`'s `sleep_after` becomes an advance of an injected manual clock, so
  the budget expires at an exactly chosen tick with no `time.sleep` and no dependence on how
  long git takes.

## Capabilities

### New Capabilities
- `orchestrator-run-budget-clock`: the run-budget elapsed-time decision reads a single
  injectable clock, so a test can place the budget cut at an exact point without real
  sleeping.

### Modified Capabilities

## Impact

- `src/worktrail/orchestrator/live.py`: `_clock` parameter on `live_run_real` and
  `_pipeline_scheduler`; both `_budget_stop` closures and their `run_start` captures read it;
  `_pipeline_scheduler` passes it through to the tail `live_run_real` call.
- `tests/orchestrator/test_pipeline_budget_partial_group.py`: manual-clock fixture, `FakeSpawn`
  advance instead of `time.sleep`, `_run` threading `_clock`.
- No CLI, env-var, journal-schema or task-format change. `ORCH_RUN_BUDGET` / `--run-budget`
  stay in minutes and still convert to the same internal seconds.
