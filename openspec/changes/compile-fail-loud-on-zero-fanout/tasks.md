## 1. Reject zero-fan-out plans (`compile-plan-shape-gate`)

- [x] 1.1 In `src/worktrail/conductor/parallelism.py`, in `shape_problems()`, replace the
      `if not fanout: return _cleanup_verification_mismatches(merged)` early return: when no
      task in `merged` has a kind outside `TAIL_KINDS` (ignore `status` for this test, so a
      plan whose fan-out tasks are all `status: completed` is untouched), build a problem
      line naming the tail task ids (or "plan has no tasks") that instructs the author to
      add at least one implementation task with `files:` scope or retag a tail task whose
      body is implementation work; return it together with
      `_cleanup_verification_mismatches(merged)`. When fan-out tasks exist but are all
      completed, keep returning only the cleanup-verification mismatches.
      (Requirement: Plans with no fan-out task are rejected at compile.)
      In `tests/conductor/test_parallelism.py`, add tests for the four `shape_problems`
      scenarios: e2e-only plan rejected naming the id; e2e+docs+cleanup plan rejected naming
      all three ids alongside the cleanup line; two completed impl tasks plus a pending e2e
      not rejected; one pending impl task plus e2e not rejected. Adjust
      `test_cleanup_mismatch_fires_even_when_fanout_is_empty` to also expect the new line.
      In `tests/conductor/test_compile.py`, beside the existing `PlanShapeError` propagation
      tests, add one end-to-end test that a `tasks.md` holding a single `[e2e]` task makes
      `main()` exit 1 with the problem line on stderr and no marker written.
      files: src/worktrail/conductor/parallelism.py, tests/conductor/test_parallelism.py, tests/conductor/test_compile.py

- [x] 1.2 In `skills/worktrail-go/references/subagent-prompts.md`, in the `#compile-gate`
      "Defects in the change" table, add a row `no fan-out task (PlanShapeError)` whose
      recovery is to add at least one implementation task with `files:` scope or retag the
      tail task that actually carries the implementation work -- never a bare retry.
      (Requirement: Plans with no fan-out task are rejected at compile -- compile-gate
      recovery table scenario.)
      files: skills/worktrail-go/references/subagent-prompts.md

## 2. Verification

- [ ] 2.1 [depends: 1.1, 1.2] [e2e] Run `PYTHONPATH=src pytest -q tests/conductor`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate compile-fail-loud-on-zero-fanout --strict` and
      `worktrail-compile openspec/changes/compile-fail-loud-on-zero-fanout`.
