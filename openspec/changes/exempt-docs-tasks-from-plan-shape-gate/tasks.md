## 1. Exempt docs-kind tasks from the fan-out shape rules

- [ ] 1.1 Exclude `docs`-kind tasks from the serial and same-file plan-shape rules.
      In `src/worktrail/conductor/parallelism.py`, add a module-level exemption set combining
      `TAIL_KINDS` with `{"docs"}` and use it to build `shape_problems()`'s gate fan-out, so
      docs tasks contribute neither chain length, width, nor same-file writers, and dependency
      edges through them no longer bridge two implementation tasks into a chain. Use the same
      set for the existing no-fan-out implementation check so a docs-only plan is still
      rejected while a completed-implementation-plus-pending-docs re-compile still passes.
      Leave `profile()` unchanged and update the module/function comments so the
      docs-versus-`TAIL_KINDS` distinction (gate exemption only, not scheduling) is explicit.
      (Requirement: Serial plans are rejected at compile)
      (Requirement: Same-file dependent chains are rejected at compile)
      In `tests/conductor/test_parallelism.py` cover: the reported shape (a completed
      implementation task plus a three-task pending docs chain sharing
      `openspec/changes/<id>/tasks.md`) returning no problems; a docs-only plan still rejected
      with the no-fan-out line; an implementation serial chain and an implementation same-file
      chain each still rejected when unrelated docs tasks are present; and `profile()` still
      counting docs tasks as before.
      files: src/worktrail/conductor/parallelism.py tests/conductor/test_parallelism.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/conductor/test_parallelism.py`, then the full suite and
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`, and
      `python3.14 scripts/ci/ruff_pinned.py check .`. Run
      `openspec validate exempt-docs-tasks-from-plan-shape-gate --strict` and
      `worktrail-compile openspec/changes/exempt-docs-tasks-from-plan-shape-gate`. Replay the
      reported fixture through `shape_problems` to confirm it returns no problem lines, while
      a docs-only plan still returns the no-fan-out line.
      depends: 1.1
