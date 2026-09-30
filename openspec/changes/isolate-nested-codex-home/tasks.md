## 1. Isolate automatic Codex child homes

- [x] 1.1 Update the shared Codex child-environment preparation so an automatic
      selection never reuses the inherited parent `CODEX_HOME`, including when
      the normal automatic location resolves to the parent; retain explicit
      `--codex-home` and `WORKTRAIL_CODEX_HOME` selection and its fail-closed
      validation. Extend the router tests with temporary parent/child homes and
      mocked login status to prove writable and read-only inherited parents use
      an isolated child, collision avoidance happens before auth linking, and
      an identical explicit parent/child selection remains rejected without
      modifying auth. Extend the direct `spawnlib` Codex worker coverage to
      prove the environment passed to its subprocess comes from the same shared
      preparation contract and carries the isolated automatic child home.
      (Requirement: Automatic Codex child home is isolated from the parent home)
      (Requirement: Explicit Codex child-home selection remains caller-controlled)
      (Requirement: Shared dispatch paths preserve automatic home isolation)
      files: src/worktrail/router/skill_dispatch.py tests/router/test_skill_dispatch.py tests/orchestrator/test_spawnlib.py

## 2. Verification

- [x] 2.1 [e2e] Verify the focused router and orchestrator regressions, then run
      `PYTHONPATH=src pytest -q`,
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`,
      `openspec validate isolate-nested-codex-home --strict`, and
      `worktrail-compile openspec/changes/isolate-nested-codex-home`.
      depends: 1.1
