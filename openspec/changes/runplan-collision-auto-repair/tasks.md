## 1. Repair logic in `apply_to_tasks()`

- [ ] 1.1 In `src/worktrail/conductor/runplan.py`, after `merged` is built (before the
      existing `compute_levels(merged)` cycle check), call
      `unordered_file_collisions(merged)` and, for each `(file, a, b)` violation, add a
      `deps` edge from whichever of `a`/`b` appears later in `merged`'s (authored) order
      to the one that appears earlier, updating that task's `deps` set in place on the
      already-built `merged` dict for that task id.
- [ ] 1.2 Re-run the existing `compute_levels(merged)` cycle check over the
      repair-augmented graph (do not add a second, separate cycle check). On a cycle,
      take the same rejection branch already used for the plan/baseline-cycle case:
      append a `notes` entry and return the original, unmodified tasks.
- [ ] 1.3 When at least one repair edge was added, append a distinct `notes` entry
      (e.g. `"auto-repaired N ordering edge(s) to close same-file collision(s): <file>:
      <b> now depends on <a>[, ...]"`) alongside the existing "run plan applied" summary
      note. Add no such entry when no repair was needed.

## 2. Tests

- [ ] 2.1 In `tests/conductor/test_runplan.py`, add coverage for
      `apply_to_tasks()` closing an unordered same-file collision left by a compiled
      plan: assert the returned merged tasks have the expected `deps` edge (later
      authored task depends on earlier) and that
      `runplan.unordered_file_collisions(merged)` on the result is empty.
- [ ] 2.2 Add a case where the plan already ordered the shared file correctly (directly
      or transitively) and assert `apply_to_tasks()` makes no additional `deps` change
      for that pair.
- [ ] 2.3 Add a case with two or more independent same-file collisions in one plan and
      assert every gap is closed independently.
- [ ] 2.4 Add a case where combining the plan's own edges with the repair edges would
      form a cycle, and assert `apply_to_tasks()` falls back to the original,
      unmodified tasks with an explanatory `notes` entry (mirroring the existing
      plan/baseline-cycle rejection test already in this file).
- [ ] 2.5 Add a case asserting the new "auto-repaired" notes entry is present when a
      repair occurred and absent when it did not.
- [ ] 2.6 Update the docstrings/comments in `tests/conductor/test_compile.py` and
      `src/worktrail/orchestrator/live.py::validate_task_metadata` only if their existing
      language ("fail-loud", "the exact miss this pass exists to catch") becomes
      inaccurate after this change — do not restructure their existing test coverage
      or fail-loud behavior; both stay as defense-in-depth per design.md.

## 3. Verification

- [ ] 3.1 Run `PYTHONPATH=src pytest -q` and confirm the full suite is green, including
      the new and existing `test_runplan.py`/`test_compile.py` cases.
- [ ] 3.2 Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check` (the
      golden record/replay regression) and confirm it is unaffected.
