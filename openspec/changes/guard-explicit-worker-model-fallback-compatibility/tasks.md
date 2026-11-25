## 1. Guard explicit models at the selected fallback cell

- [ ] 1.1 Add a routing-snapshot compatibility check for live explicit model overrides. After
      `LiveSpawn.__call__` selects its authoritative fallback cell, derive the deterministic,
      de-duplicated models declared by targets with that cell's harness and reject a requested
      role model that is absent before entering `explicit_cell_override()` or calling either spawn
      function. The failure must name the requested model, selected target and harness, and the
      compatible-model set; compatible model overrides must still write and use the selected
      target's one-cell routing override, and effort-only overrides must remain unchanged. Keep
      the check on the live-worker path and use `self._routing` rather than independently
      resolving policy. Add focused unit coverage for the routing-derived compatibility helper or
      boundary (same-harness models across multiple targets accepted; a model declared only on
      another harness rejected; stable error content) and live-spawn coverage proving a
      capacity-driven cross-harness fallback with `--model-map` raises before the override/spawn,
      while compatible fallback and effort-only cases retain their existing behavior. Tests must
      be hermetic using the existing routing and spawn mocks.
      (Requirement: Explicit worker model overrides remain compatible with the serving harness)
      files: src/worktrail/orchestrator/live.py src/worktrail/orchestrator/spawnlib.py tests/orchestrator/test_live_extras.py tests/orchestrator/test_spawnlib.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src python3.14 -m pytest -q
      tests/orchestrator/test_live_extras.py tests/orchestrator/test_spawnlib.py`, then
      `PYTHONPATH=src python3.14 -m pytest -q` and `PYTHONPATH=src python3.14 -m
      worktrail.orchestrator.orchestrate check`. Run `python3.14 scripts/ci/ruff_pinned.py check
      .`, `python3.14 scripts/ci/ruff_pinned.py format --check .`, and `python3.14
      scripts/ci/check_shebang_exec_bits.py`. Run `openspec validate
      guard-explicit-worker-model-fallback-compatibility --strict` and `worktrail-compile
      openspec/changes/guard-explicit-worker-model-fallback-compatibility`. Confirm a model-map
      model declared only for claude fails before a codex fallback spawn, while a model declared
      for codex continues through the explicit override. No file changes are expected.
      (Requirement: Explicit worker model overrides remain compatible with the serving harness)
      depends: 1.1
