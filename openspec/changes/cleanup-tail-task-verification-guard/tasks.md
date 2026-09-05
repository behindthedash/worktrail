## 1. Compile-time rejection of verification-bodied cleanup tasks (`compile-plan-shape-gate`)

- [x] 1.1 Implement requirement: Cleanup tail tasks authored as verification
      commands are rejected at compile. In
      `src/worktrail/conductor/parallelism.py`, add
      `_cleanup_verification_mismatches(merged)`: for every task with
      `kind == "cleanup"` whose `title` matches both an imperative-verb
      pattern (`run`/`execute`) and a recognizable command pattern (a
      backticked fragment, or `pytest`/`npm`/`yarn`/`jest`/`mocha`/`tox`/
      `openspec validate`/`python -m`), append a problem line naming the
      task and pointing at `[e2e]` (design.md Decisions 1-2). Wire it into
      `shape_problems` on the full `merged` list (not `fanout`, which
      excludes tail kinds by design) so it fires both in the normal path and
      when `fanout` is empty. Add regression tests in
      `tests/conductor/test_parallelism.py`: (a) a `[cleanup]` task with a
      backticked command is rejected; (b) the exact live-incident wording
      (imperative "Run" + command names, no backticks) is rejected; (c) a
      genuinely inert `[cleanup]` task (e.g. "Remove debug logging left in
      tasks 1-4") passes; (d) an `[e2e]` task with the same imperative title
      is unaffected; (e) a `[docs]` task with the same imperative title is
      unaffected; (f) the rule still fires when the merged task list's
      fanout (non-tail tasks) is empty.
      files: src/worktrail/conductor/parallelism.py, tests/conductor/test_parallelism.py

## 2. Scope-gap guidance and end-to-end compile fixture

- [x] 2.1 Implement requirement: Scope-gap remediation names what each tail
      kind executes. In `src/worktrail/conductor/compile.py`'s
      `_print_scope_gap_error`, reword the "give them a tail kind
      (docs/e2e/cleanup)" guidance line to state that `e2e` spawns a worker
      and runs commands, while `cleanup` and `docs` are journal-only status
      transitions that execute nothing (design.md Decision 3). Add a
      regression test in `tests/conductor/test_compile.py` asserting the
      CLI's scope-gap error text distinguishes `e2e` ("spawns a worker")
      from `cleanup`/`docs` ("execute nothing"). Also add
      `tests/fixtures/plan_shape/cleanup-verification-mismatch.tasks.md`
      (one co-scoped impl task plus a `[cleanup]` tail task carrying the
      live-incident's exact verification wording) and a CLI-level
      regression test asserting `worktrail-compile --no-llm` against it
      exits 1, names the mismatched task, points at `[e2e]`, and writes no
      compile marker.
      files: src/worktrail/conductor/compile.py, tests/conductor/test_compile.py, tests/fixtures/plan_shape/cleanup-verification-mismatch.tasks.md

## 3. Verification

- [x] 3.1 [e2e] Run `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`;
      confirm both are green, then run `openspec validate
      cleanup-tail-task-verification-guard --strict` and confirm it passes.
