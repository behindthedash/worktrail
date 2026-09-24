## 1. Plan validation

- [ ] 1.1 Reject pending non-tail tasks that directly depend on `e2e` or `cleanup` tasks in the settled plan, with deterministic diagnostics naming both endpoints; cover rejected implementation-to-tail edges and accepted tail-to-tail, implementation-only, and completed-task cases. (Requirement: Tail dependency inversions are rejected before scheduling)
  files: src/worktrail/conductor/parallelism.py tests/conductor/test_parallelism.py tests/orchestrator/test_precheck_e2e.py

## 2. Verification

- [ ] 2.1 [e2e] Run the focused conductor and precheck tests, then the full pytest suite and orchestrator golden regression; confirm `worktrail-compile` rejects a fixture with a pending implementation-to-tail dependency.
  depends: 1.1
