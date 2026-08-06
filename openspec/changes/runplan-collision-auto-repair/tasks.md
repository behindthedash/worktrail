## 1. Repair logic and its direct regression tests

- [ ] 1.1 In `src/worktrail/conductor/runplan.py`'s `apply_to_tasks()`, after `merged` is
      built (before the existing `compute_levels(merged)` cycle check), call
      `unordered_file_collisions(merged)` and, for each `(file, a, b)` violation, add a
      `deps` edge from whichever of `a`/`b` appears later in `merged`'s (authored) order
      to the one that appears earlier, updating that task's `deps` set in place on the
      already-built `merged` dict for that task id. Let the existing
      `compute_levels(merged)` cycle check run unchanged over the repair-augmented graph
      (no second, separate cycle check) — on a cycle it already takes the same rejection
      branch used for the plan/baseline-cycle case. When at least one repair edge was
      added, append a distinct `notes` entry (e.g. `"auto-repaired N ordering edge(s) to
      close same-file collision(s): <file>: <b> now depends on <a>[, ...]"`) alongside the
      existing "run plan applied" summary note; add no such entry when no repair was
      needed.
- [ ] 1.2 In `tests/conductor/test_compile.py`, this change makes the compile-time
      collision genuinely unreachable through the CLI's normal `apply_to_tasks()` →
      `unordered_file_collisions()` path — `test_the_cli_fails_loudly_on_an_unordered_file_collision`
      and `test_the_cli_json_mode_fails_loudly_on_an_unordered_file_collision` (both
      currently assert `rc == 1` on an unordered same-file plan) are no longer testing
      real behavior once 1.1 lands and MUST be rewritten (not merely have their docstrings
      touched) to assert the CLI now exits 0 and the printed/JSON plan shows the repaired
      `deps` edge. This is the same file 1.1 changes conceptually and belongs in the same
      task, not a separate one, so the two land atomically — do not defer this update to a
      "different purpose" handoff.

## 2. Tests

- [ ] 2.1 In `tests/conductor/test_runplan.py`, first update the existing
      `test_an_edge_is_dropped_even_when_both_ends_share_the_same_file` — it currently
      documents and asserts the pre-repair gap ("an edge is dropped ... `merged[1]["deps"]
      == []`") this change closes, so it must assert the repaired `deps` edge and an empty
      `unordered_file_collisions()` result instead, not merely have wording tweaked. Then,
      in the same editing pass (all cases live in this one file, so they are one task, not
      several, to avoid multiple parallel tasks writing the same test file), add new
      coverage for:
      (a) `apply_to_tasks()` closes an unordered same-file collision left by a compiled
      plan — assert the returned merged tasks have the expected `deps` edge (later
      authored task depends on earlier) and that
      `runplan.unordered_file_collisions(merged)` on the result is empty;
      (b) the plan already ordered the shared file correctly (directly or transitively)
      — assert no additional `deps` change for that pair;
      (c) two or more independent same-file collisions in one plan — assert every gap is
      closed independently;
      (d) combining the plan's own edges with the repair edges would form a cycle —
      assert `apply_to_tasks()` falls back to the original, unmodified tasks with an
      explanatory `notes` entry (mirroring the existing plan/baseline-cycle rejection
      test already in this file);
      (e) the new "auto-repaired" `notes` entry is present when a repair occurred and
      absent when it did not.
- [ ] 2.2 Update the docstrings/comments in
      `src/worktrail/orchestrator/live.py::validate_task_metadata` only if its existing
      language ("the same live-run enforcement point for `runplan.unordered_file_collisions`")
      becomes inaccurate after this change — do not restructure its existing fail-loud
      behavior or test coverage; it stays as defense-in-depth per design.md (a task whose
      merged tasks were built without going through `apply_to_tasks()` could still trip
      it).

## 3. Verification

- [ ] 3.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm the full suite is green,
      including the new and existing `test_runplan.py`/`test_compile.py` cases. Tagged
      `[cleanup]` (tail kind): this needs the implementation and test tasks above merged
      first, not fanned out alongside them.
- [ ] 3.2 [cleanup] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate
      check` (the golden record/replay regression) and confirm it is unaffected. Tagged
      `[cleanup]` for the same reason as 3.1.
