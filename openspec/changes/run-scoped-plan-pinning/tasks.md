## 1. Pin read, fail-closed path, and their regression tests

- [ ] 1.1 One atomic task across the affected files — the orchestrator groups PRs by
      shared file, not by `deps` edges, so splitting the `live.py` change from the tests
      that cover it would let them land in separate groups whose smoke tests each fail in
      isolation (this is the same reason `runplan-collision-auto-repair` kept its work in
      one task). All of the following land in one task, one commit:

      (a) In `src/worktrail/orchestrator/live.py`, add a small module-level helper beside
      `_record_plan_fingerprint` — e.g. `_pinned_plan_fingerprint(repo, spec_rel) -> str |
      None` — that reads `journal_path_for(repo, spec_rel)`, returns
      `journal.get("plan_fingerprint") or None`, and returns `None` on any `OSError` /
      `json.JSONDecodeError` (DEC-004: journal I/O never takes a run down). Do not change
      `_record_plan_fingerprint` itself.

      (b) Implements these requirements:
      **"A run reuses its pinned RunPlan instead of recompiling"**;
      **"The first compile of a run establishes the pin"**;
      **"An unresolvable pin fails the run instead of recompiling"**.
      In `apply_run_plan()`, before the `compile_run_plan(...)` call, consult that
      helper. When a pin is present, resolve it with
      `runplan.load_cached(conductor_compile.default_cache_dir(repo), spec_id, pinned)`:
        - **hit** → log that the run is reusing its pinned plan (include `pinned[:12]`),
          skip `compile_run_plan` entirely, and continue into the existing
          `runplan.apply_to_tasks(tasks, plan)` / notes / `_record_plan_fingerprint` flow
          unchanged, so the pinned fingerprint is re-stamped and `plan_fingerprints` stays
          at one entry;
        - **miss** → raise an explicit error (DEC-003) naming `spec_id`, the pinned
          fingerprint, and the re-plan escape hatch from DEC-005 ("clear
          `plan_fingerprint` from `<journal path>` to deliberately re-plan"). Do not
          compile a replacement.
      When no pin is present, fall through to the existing compile path with no behavior
      change. Keep the existing `except OSError` guard around the compile call as-is.

      (c) Covers these requirements:
      **"A run reuses its pinned RunPlan instead of recompiling"**;
      **"The first compile of a run establishes the pin"**;
      **"An unresolvable pin fails the run instead of recompiling"**.
      In `tests/orchestrator/test_apply_run_plan_autocompile.py`, add coverage using
      the existing injectable `spawn` seam so no model is called: a pinned run reuses the
      cached plan and the `spawn` seam is never invoked (assert both the returned `deps`/
      `files` come from the pinned plan and that a spawn that would raise if called is not
      called); a pinned run whose change content has since changed still reuses the pinned
      plan and does not compile; a run with no pin compiles as before and leaves the
      journal's `plan_fingerprint` equal to the applied plan's fingerprint; a pin whose
      cached plan file has been deleted raises, with the spec id, the pinned fingerprint,
      and the journal path present in the message, and no compile attempted; an unreadable
      or malformed journal is treated as no pin and takes the normal compile path.

      (d) Covers requirement **"Drift warning is retained as defense-in-depth"**.
      In `tests/orchestrator/test_plan_fingerprint_record.py`, add one case asserting
      the end-state this change exists to produce: across two `apply_run_plan()` calls for
      the same `(repo, spec)` — the shape a tail-bearing run actually takes — the journal's
      `plan_fingerprints` ends with exactly one entry and no `PLAN DRIFT` output is
      emitted. Leave the existing drift-detection tests intact; they cover the retained
      defense-in-depth path (DEC-006) and must keep passing.

      (e) Update `apply_run_plan()`'s docstring so its "compiling one if none is cached"
      framing reflects pinning, and cross-reference the pin in `_record_plan_fingerprint`'s
      docstring (which currently documents run `full-1786812908`'s drift as an unmitigated
      observation). Comment/docstring updates only — do not restructure either function's
      existing behavior beyond (a)/(b).

## 2. Verification

- [x] 2.1 [cleanup] Run `PYTHONPATH=src pytest -q` and confirm the full suite is green,
      including the new and existing `test_apply_run_plan_autocompile.py` /
      `test_plan_fingerprint_record.py` cases. Tagged `[cleanup]` (tail kind): this needs
      the implementation and test task above merged first, not fanned out alongside it.
- [ ] 2.2 [cleanup] Run `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate
      check` (the golden record/replay regression) and confirm it is unaffected. Tagged
      `[cleanup]` for the same reason as 2.1.
