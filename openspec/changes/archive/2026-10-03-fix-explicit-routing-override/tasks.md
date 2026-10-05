## 1. Preserve the selected routing table for explicit overrides

- [x] 1.1 Update `explicit_cell_override()` to derive the temporary explicit
      cell from a supplied resolved routing table, retaining fail-closed
      validation for an absent target; add helper coverage for supplied-table
      target lookup, harness/pool preservation, effort injection, and the
      missing-target error. (Requirement: Explicit overrides retain the routing
      table that selected their target)
      files: src/worktrail/orchestrator/spawnlib.py tests/orchestrator/test_spawnlib.py

- [x] 1.2 Pass `LiveSpawn`'s already-resolved routing table into the explicit
      model/effort override path; add an integration regression in which
      repository-local routing selects a target absent from the machine-wide
      table and verify the temporary routing file retains the selected target
      and dispatches it. (Requirement: Explicit overrides retain the routing
      table that selected their target)
      files: src/worktrail/orchestrator/live.py tests/orchestrator/test_live_extras.py
      depends: 1.1

## 2. Verify dispatch behavior

- [ ] 2.1 [e2e] Run the focused spawnlib and live-spawn regressions, then
      `PYTHONPATH=src pytest -q`, `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, `python3
      scripts/ci/ruff_pinned.py check .`, and `python3
      scripts/ci/ruff_pinned.py format --check .`; confirm an explicit model
      and effort override both preserve the selected routing target.
      depends: 1.2
