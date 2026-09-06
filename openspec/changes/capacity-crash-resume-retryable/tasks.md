## 1. Classify drive crashes before journaling

- [ ] 1.1 In `src/worktrail/orchestrator/live.py`: add a module-level
      `_crash_terminal_status(exc: BaseException) -> str` that lazily imports
      `NoExecutionTarget` from `..runtime.selection` (matching the existing lazy import at
      line ~2908) and returns `"retryable"` for an `isinstance` match, `"failed"` otherwise
      (design.md D1). Use it in BOTH `_safe_drive` bodies -- `live_run_real`'s (line ~4612) and
      the pipeline scheduler's (line ~5851): compute `terminal_status` from the caught
      exception, pass it to `_journal_failure_entry(...)`, and print
      `!! <task> no capacity: <exc> -- will re-dispatch on resume` for the retryable case while
      keeping today's `!! <task> drive crashed: <repr> -- marking failed` line for every other
      case. Keep the journal `notes` text `drive crashed: {e!r}` and
      `task["status"] = "failed"` unchanged in both branches (design.md D2, D3).
      In a new `tests/orchestrator/test_capacity_crash_resume.py`, add: `_crash_terminal_status`
      returns `retryable` for `NoExecutionTarget([...])`, `retryable` for a subclass of it,
      `failed` for `InvalidCandidate` and for a plain `RuntimeError`; a `live_run_real` drive
      that raises `NoExecutionTarget` journals a `drive` entry with
      `report.terminal_status == "retryable"` and `report.notes` starting `drive crashed:`,
      while a drive raising `RuntimeError` journals `"failed"`; the pipeline scheduler's
      `_safe_drive` produces the same `retryable` classification for the same exception; the
      capacity case prints the `no capacity` line and the ordinary case prints
      `-- marking failed`; and replaying a journal containing the `retryable` drive entry leaves
      the task `pending` while replaying the `failed` one leaves it `failed`
      (Requirements: A capacity-exhaustion crash is journaled as retryable; Resume re-dispatches
      a capacity-gated task without --fresh).
      files: src/worktrail/orchestrator/live.py, tests/orchestrator/test_capacity_crash_resume.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check` and confirm both repository gates pass;
      depends on 1.1. Verification-only, no file changes expected.
