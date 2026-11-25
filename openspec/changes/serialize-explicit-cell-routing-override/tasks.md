## 1. Thread-scoped routing-file override (code and tests)

- [x] 1.1 Replace the process-wide `WORKTRAIL_ROUTING_FILE` swap in `explicit_cell_override()`
      with a per-thread routing-file override, and pin the concurrent contract with tests.
      In `src/worktrail/router/policy.py`, add `contextlib`/`threading` imports, a module-level
      `threading.local()`, and a `routing_file_override(path)` context manager that saves,
      sets, and restores the current thread's path in a `finally` block (nesting restores the
      enclosing value). Make `resolved_routing_file_path()` consult that thread-local first,
      then `env_setting(ROUTING_FILE_ENV)` (legacy `GO_` name still honored), then
      `default_routing_file()`, and update its docstring to say the thread override outranks
      both. In `src/worktrail/orchestrator/spawnlib.py`, replace the
      `os.environ[ROUTING_FILE_ENV]` set/restore in `explicit_cell_override()` with
      `with routing_file_override(explicit_file): yield` (same signature, same lifetime, same
      temp-file write and unlink), drop the now-unused `ROUTING_FILE_ENV` import, and reword
      the docstring from "points `WORKTRAIL_ROUTING_FILE`" to the calling thread's routing file.
      Update the stale comment in `src/worktrail/router/env_profile.py` that says the override
      points the env var at a mkstemp file. In `tests/orchestrator/test_spawnlib.py`, change
      `ExplicitCellOverrideCarriesTargetDefinition._written` to read
      `spawnlib.resolved_routing_file_path()` instead of `os.environ`, and add a concurrency
      class: two threads enter overrides for different targets/models, synchronize on a
      `threading.Barrier` so both are inside at once, and each asserts its own
      `resolved_routing_file_path()`, its own `load_policy(worktrail_home())`-resolved
      `tiers.explicit` cell/model, and that the other's file is not visible; the main thread
      (or a third thread) asserts it still resolves the operator seed while both are inside;
      both threads assert `os.environ` was not mutated for `WORKTRAIL_ROUTING_FILE`; after
      both exit, `resolved_routing_file_path()` is the operator seed again; and a nested
      same-thread override restores the outer file. Update the fixtures that captured the
      override through `os.environ["WORKTRAIL_ROUTING_FILE"]` to use
      `resolved_routing_file_path()` instead -- `tests/orchestrator/test_live_extras.py`
      (`LiveSpawnEffortTests._call_capturing_routing_file`, treating "same as the pre-call
      seed" as no override so the no-override case still asserts `None`),
      `tests/orchestrator/test_resilience_helpers.py`'s fake `spawn_claude_p`, and
      `tests/conductor/test_compile.py`'s explicit-override test (assert the env var stays
      unset while the mocked spawn observes the thread's throwaway file, and that the operator
      path is restored after the block).
      (Requirement: Concurrent explicit overrides are isolated to their spawn's thread)
      files: src/worktrail/router/policy.py, src/worktrail/orchestrator/spawnlib.py, src/worktrail/router/env_profile.py, tests/orchestrator/test_spawnlib.py, tests/orchestrator/test_live_extras.py, tests/orchestrator/test_resilience_helpers.py, tests/conductor/test_compile.py

## 2. Verification

- [x] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/orchestrator/test_spawnlib.py
      tests/orchestrator/test_live_extras.py tests/orchestrator/test_resilience_helpers.py
      tests/conductor/test_compile.py tests/conductor/test_compile_spawn_exhausted.py`, then
      `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`, then `python3 scripts/ci/ruff_pinned.py check .`,
      `python3 scripts/ci/ruff_pinned.py format --check .`, and
      `python3 scripts/ci/check_shebang_exec_bits.py`. Confirm by hand that no override path
      touches the process env: grep `src/worktrail` for `ROUTING_FILE_ENV` writes and confirm
      only `policy.resolved_routing_file_path()` reads the environment. Re-run the concurrency
      test several times to catch interleaving (it uses a barrier, not timing). Run
      `openspec validate serialize-explicit-cell-routing-override --strict` and
      `worktrail-compile openspec/changes/serialize-explicit-cell-routing-override`.
      depends: 1.1
