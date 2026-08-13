## 1. Repair logic and its direct regression tests

- [x] 1.1 One atomic task across the affected files — the orchestrator's PR-grouping is by
      shared file, not by `deps` edges, so splitting this across tasks that touch
      different files would let them land in separate PRs/groups whose own smoke tests
      each fail in isolation (confirmed the hard way: an earlier attempt at splitting
      this exact work quarantined every group). All of the following land in one task,
      one commit:
      (a) In `src/worktrail/conductor/runplan.py`'s `apply_to_tasks()`, after `merged` is
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
      (b) In `tests/conductor/test_compile.py`, this change makes the compile-time
      collision genuinely unreachable through the CLI's normal `apply_to_tasks()` →
      `unordered_file_collisions()` path — `test_the_cli_fails_loudly_on_an_unordered_file_collision`
      and `test_the_cli_json_mode_fails_loudly_on_an_unordered_file_collision` (both
      currently assert `rc == 1` on an unordered same-file plan) are no longer testing
      real behavior once (a) lands and MUST be rewritten (not merely have their
      docstrings touched) to assert the CLI now exits 0 and the printed/JSON plan shows
      the repaired `deps` edge.
      (c) In `tests/conductor/test_runplan.py`, first update the existing
      `test_an_edge_is_dropped_even_when_both_ends_share_the_same_file` — it currently
      documents and asserts the pre-repair gap ("an edge is dropped ... `merged[1]["deps"]
      == []`") this change closes, so it must assert the repaired `deps` edge and an empty
      `unordered_file_collisions()` result instead, not merely have wording tweaked. Then
      add new coverage for: apply_to_tasks() closing an unordered same-file collision
      (assert the expected `deps` edge and that `unordered_file_collisions(merged)` is
      empty); the plan already ordering the shared file correctly, directly or
      transitively (assert no additional `deps` change); two or more independent
      same-file collisions in one plan (assert every gap closed independently);
      combining the plan's own edges with the repair edges forming a cycle (assert
      `apply_to_tasks()` falls back to the original, unmodified tasks with an
      explanatory `notes` entry, mirroring the existing plan/baseline-cycle rejection
      test); and the new "auto-repaired" `notes` entry present when a repair occurred,
      absent when it did not.
      (d) Update the docstrings/comments in
      `src/worktrail/orchestrator/live.py::validate_task_metadata` only if its existing
      language ("the same live-run enforcement point for `runplan.unordered_file_collisions`")
      becomes inaccurate after this change — do not restructure its existing fail-loud
      behavior or test coverage; it stays as defense-in-depth per design.md (a task whose
      merged tasks were built without going through `apply_to_tasks()` could still trip
      it).

## 3. Verification

- [x] 3.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm the full suite is green,
      including the new and existing `test_runplan.py`/`test_compile.py` cases. Tagged
      `[cleanup]` (tail kind): this needs the implementation and test tasks above merged
      first, not fanned out alongside them.
- [x] 3.2 [cleanup] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate
      check` (the golden record/replay regression) and confirm it is unaffected. Tagged
      `[cleanup]` for the same reason as 3.1.
