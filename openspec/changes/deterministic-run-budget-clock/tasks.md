## 1. Inject a clock seam into the run-budget decision

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`, add a `_clock=None` keyword parameter to
      `live_run_real` and to `_pipeline_scheduler`, normalised once at the top of each body to
      `_clock = time.time if _clock is None else _clock`. Replace the `run_start = time.time()`
      capture that precedes each fan-out (`live.py:5328` and `live.py:6610`) and the
      `now = time.time()` read plus the `_budget_stopped_at[0] = now` store inside each
      `_budget_stop` closure (`live.py:5358` and `live.py:6627`) with calls to that callable.
      Leave every other `time.time()` read in both functions untouched -- specifically the
      progress emitter's `elapsed` line, the per-task `t0`/`t1` timings and the journal `"at"`
      stamps. Where `_pipeline_scheduler` calls `live_run_real` for the tail pass, pass
      `_clock` through (including via `_dispatch_pending_tail`, which gains the same
      parameter and forwards it). Document the seam in a short comment beside the
      `RUN_BUDGET_DEFAULT` block explaining that only the budget decision is clock-injectable
      and why the audit timestamps deliberately are not.
      (Requirements: The run-budget decision reads one injectable clock; Non-budget timings
      keep using wall-clock time.)
      In `tests/orchestrator/test_pipeline_budget_partial_group.py`, add a `ManualClock` helper
      (callable returning `self.now`, with `advance(seconds)`), drop `FakeSpawn.sleep_after`'s
      `time.sleep` in favour of an `advance_after={task_id: seconds}` mapping that advances an
      injected clock after the task's real commit lands, thread `_clock` through the module's
      `_run` helper into `live._pipeline_scheduler`, and convert the three wall-clock tests
      (currently at lines 253, 306 and 384) to drive the cut off the manual clock -- keeping
      their existing assertions (TASK-002 dispatched, TASK-003 not, `feature-1` quarantined
      `budget_exhausted`, and the resume phases) unchanged. Replace the lines 242-244 comment
      about a "wide margin" with one stating the cut is now exact. Add new cases proving the
      seam: a never-advancing clock with a non-zero `run_budget` completes the fan-out; a
      default-clock run still journals a real wall-clock `budget_stopped_at`; and a journal
      event's `"at"` stamp under an injected clock is a real time, not the clock's value.
      (Requirements: Budget tests do not sleep in real time.)
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_pipeline_budget_partial_group.py

## 2. Verification

- [ ] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q
      tests/orchestrator/test_pipeline_budget_partial_group.py tests/orchestrator/test_budget_resume.py`,
      confirm the module no longer spends real seconds sleeping (`PYTHONPATH=src pytest -q
      --durations=5 tests/orchestrator/test_pipeline_budget_partial_group.py`), then
      `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`. Run `python3 scripts/ci/ruff_pinned.py check .`
      and `python3 scripts/ci/ruff_pinned.py format --check .`. Run `openspec validate
      deterministic-run-budget-clock --strict` and `worktrail-compile
      openspec/changes/deterministic-run-budget-clock`.
