## 1. The readiness probe

- [ ] 1.1 Add `router/spawn_readiness.py` with `readiness_problems(routing, *, base_env=None)`,
      which enumerates every declared `(row, target)` tier cell, reports an `api`-pool target
      with no `api_opt_in` and a harness outside the supported set as unready, and otherwise
      builds each cell's command and child environment from the resolved table it was handed —
      `build_child_env` with the resolved `env_profiles` and the given `base_env`, and no path
      parameter or file access anywhere in the module, so it can only ever be fed
      `resolve_routing()`'s output. Import `spawnlib` inside the function, since a module-level
      edge back to it is an import cycle. In the same task, extract the two codex `api` home
      validations out of `_prepare_child_env` into a named helper that both the spawn path and
      the probe call, leaving home creation itself at spawn time; cover each unready class, the
      satisfied case, the codex lane, and the no-gate side effect.
      (Requirement: The routing check proves spawn readiness against the resolved table)
      files: src/worktrail/router/spawn_readiness.py, tests/router/test_spawn_readiness.py, src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py

## 2. Routing example

- [ ] 2.1 Document the readiness stage in the routing example file: what `--check` now proves
      beyond schema validity, that a readiness `FAIL` records no capacity gate, and that the
      named auth variables must be set in the checking shell for a claude `api` cell to report
      `ok`. (Requirement: The routing check proves spawn readiness against the resolved table)
      files: docs/config/routing.yaml.example

## 3. Skill gotcha

- [ ] 3.1 Add the operator-facing gotcha to the routing-config skill: `--check` is a spawn
      readiness probe, not a schema linter, so a cell whose auth lane cannot resolve in the
      current shell fails it; and a drain now refuses to start on such a failure instead of
      routing around it, while a capacity-gated cell is still walked past.
      (Requirement: A spawn-readiness failure stops a drain before any spawn)
      files: skills/worktrail-routing-config/SKILL.md, skills/worktrail-routing-config/references/gotchas.md

## 4. Skill recipe

- [ ] 4.1 Add the how-to recipe for reading a readiness failure to the routing-config skill:
      which unready class each failure message names, what the fix is for each (declare the
      missing profile, set the named variable in the spawning environment, add `api_opt_in`,
      provision the codex home), and why none of them is a capacity gate an operator can wait
      out. (Requirement: The routing check proves spawn readiness against the resolved table)
      files: skills/worktrail-routing-config/references/how-to.md

## 5. Wire the check

- [ ] 5.1 In `src/worktrail/router/routing_cli.py`, make `_check` resolve the routing table
      through `load_policy()` and `resolve_routing()` and run the probe against that table,
      marking every cell it reports unready as `FAIL` with the reported message, printing those
      messages to stderr and exiting non-zero, and recording no `agent_capacity` gate for them.
      Cover the resolver-drops-a-declared-key regression, the unselectable `api` target, the
      unset auth variable, and a servable table still exiting zero.
      (Requirement: The routing check proves spawn readiness against the resolved table)
      files: src/worktrail/router/routing_cli.py, tests/router/test_routing_cli.py
      depends: 1.1

## 6. Wire the drain

- [ ] 6.1 In `src/worktrail/drain/drain.py`, run the probe ahead of the intake-triage pre-pass
      and raise on any unready cell so `main()` exits 2 naming the cell and the routing file
      rather than logging and continuing, resolving the table itself for the probe instead of
      reusing the validated mapping already in scope. Leave the existing liveness call and its
      recorded `model_unavailable` gates exactly as they are. Cover the refusal ordered ahead of
      the spawning pre-pass, a capacity-gated cell still letting the run proceed, and a ready
      table starting normally.
      (Requirement: A spawn-readiness failure stops a drain before any spawn)
      files: src/worktrail/drain/drain.py, tests/drain/test_drain.py
      depends: 1.1

## 7. Verification

- [ ] 7.1 [e2e] Run the focused router and drain tests, then `PYTHONPATH=src python3.14 -m
      pytest -q`, `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`,
      `python3.14 scripts/ci/ruff_pinned.py check .`, `python3.14 scripts/ci/ruff_pinned.py
      format --check .` and `python3.14 scripts/ci/check_shebang_exec_bits.py`. Then prove the
      incident path end to end against a scratch routing file: a table whose resolved form drops
      `env_profiles` while the file still declares it fails `--check` and stops a drain before
      any process is spawned, while the same table with the key carried through both checks
      clean and starts.
      depends: 5.1, 6.1
