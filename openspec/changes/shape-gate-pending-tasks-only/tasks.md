## 1. Pending-only fan-out filter

- [ ] 1.1 In `src/worktrail/conductor/parallelism.py`, extend `shape_problems()`'s existing
  fan-out filter (`fanout = [t for t in merged if t.get("kind") not in TAIL_KINDS]`) to also
  exclude any task whose `status` is `"completed"`, so the critical-path/width, same-file
  chain, and missing-test-scope checks all evaluate only currently-pending fan-out tasks.
  Update the function's docstring to state the exclusion. In
  `tests/conductor/test_parallelism.py`, extend the `_task()` helper with a
  `status="pending"` keyword default (included in the returned dict) so existing tests are
  unaffected, and add: a completed predecessor no longer counting toward the serial rule, a
  completed writer no longer extending a same-file chain, a completed task with missing test
  scope no longer flagged, and a fully-pending plan (no task marked completed) evaluating
  exactly as before. (Requirement: Completed tasks are excluded from plan-shape gating)

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
  worktrail.orchestrator.orchestrate check` and confirm both repository gates pass; depends
  on 1.1.
