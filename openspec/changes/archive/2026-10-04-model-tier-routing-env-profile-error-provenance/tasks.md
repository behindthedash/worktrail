## 1. Fix the attribution and pin it with regression tests

- [x] 1.1 (Requirements: A worker environment can be supplied from a declared profile without
      storing values) Implement design D1-D3 in
      `src/worktrail/orchestrator/spawnlib.py` and pin the contract in
      `tests/orchestrator/test_spawnlib.py` and `tests/router/test_spawn_readiness.py`.
      In `_apply_env_profile`, add the optional membership-only `declared_env_profiles`
      parameter (never consulted for resolution; document that) and branch before the existing
      raise: when `not env_profiles` (empty or absent resolved table) raise the design D3
      case-B message -- it names the target, the profile and the resolved routing source
      (`resolved from`), attributes the fault to the resolved table (`resolver/caller`), does
      NOT contain `not declared in routing.env_profiles` and does NOT contain `-- add an`, and
      appends the innocence clause `; the routing file does declare '<name>', which the
      resolved table dropped` iff `declared_env_profiles` is supplied and declares the name.
      Otherwise (`env_profiles.get(name)` not a `Mapping` in a populated table) keep today's
      raise byte-identical. Fix the docstring: its "every failure is operator configuration"
      claim is wrong for the empty-table case.
      In `build_child_env`, add the optional keyword `declared_env_profiles=None` and forward
      it, keeping the `env_profiles or {}` normalization (None and `{}` are the same case;
      note the parameter is attribution-only in the docstring).
      In `spawn_agent` (~line 1296), hoist its existing single load to
      `policy = load_policy(worktrail_home())`, keep `routing = resolve_routing(policy)`,
      compute `declared_env_profiles = (policy.get("routing") or {}).get("env_profiles")`, and
      thread it through `_prepare_child_env`'s `build_child_env` call. No new policy read;
      `router/spawn_readiness.py` stays unchanged and keeps passing only `env_profiles=`.
      Tests, in `BuildChildEnv` next to `test_undeclared_profile_fails_loud`: (a) a cell naming
      `auth.profile` against `env_profiles={}` and `env_profiles=None` raises
      `OperatorConfigError` containing the target, the profile, `resolved from` and
      `resolver/caller`, and not `not declared in routing.env_profiles` nor `-- add an`;
      (b) the innocence clause is present iff a supplied declared table declares the name
      (present / not declaring / omitted cases); (c) `spawn_agent`-level threading: patch
      `spawnlib.load_policy` to a policy whose `routing.env_profiles` declares the profile and
      `spawnlib.resolve_routing` to the same table minus `env_profiles`, point the tier row at
      that target, and assert the raise contains `does declare` -- before any launch; (d) keep
      the populated-table case of `test_undeclared_profile_fails_loud` asserting the old
      markers (`not declared in routing.env_profiles`, `-- add an`) and drop only its
      `None`/`{}` cases (superseded by (a)). In `tests/router/test_spawn_readiness.py`
      (`TestUnreadyClasses`), extend `test_profile_key_dropped_by_the_resolver_is_reported` and
      `test_profile_the_resolved_table_does_not_declare` with the provenance markers
      (`resolver/caller` present; `-- add an` and `not declared in routing.env_profiles`
      absent). Watch (a)-(c) fail before the `src/` edit, then pass after.
      Sibling-raise audit (design D4): no code change to those sites; confirm their existing
      tests stay green --
      `BuildChildEnv.test_claude_api_raises_when_auth_is_not_configured`,
      `BuildChildEnv.test_auth_env_and_auth_profile_together_fail_loud`,
      `CodexApiHomeHelper.test_a_missing_home_names_the_target_and_the_fix`,
      `CodexApiHomeHelper.test_an_unprovisioned_home_is_refused_and_never_created`,
      `test_codex_api_cell_without_codex_home_fails_loud_before_launch`. If one fails, stop:
      that is out of this change's scope.
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py, tests/router/test_spawn_readiness.py

## 2. Verification

- [x] 2.1 [e2e] Run the targeted suites (`PYTHONPATH=src python3.14 -m pytest -q
      tests/orchestrator/test_spawnlib.py tests/router/test_spawn_readiness.py`), then the full
      `PYTHONPATH=src python3.14 -m pytest -q` and the golden regression
      `PYTHONPATH=src python3.14 -m worktrail.orchestrator.orchestrate check`. Lint through the
      pinned wrappers: `python3.14 scripts/ci/ruff_pinned.py check .` and
      `python3.14 scripts/ci/ruff_pinned.py format --check .` (plus
      `python3.14 scripts/ci/check_shebang_exec_bits.py`). If `python3.14` resolves to an
      interpreter without dev extras, put the repo-local `.venv/bin` first on `PATH` per
      AGENTS.md. Confirm the defect against the live repro: building a child env for a cell
      whose target declares `auth.profile` with an empty resolved table now raises the
      resolver/caller-attributed message containing the innocence clause when the loader
      declares the name, while a populated table missing the name still raises the original
      `-- add an` message. Then `openspec validate
      model-tier-routing-env-profile-error-provenance --strict` and `worktrail-compile
      openspec/changes/model-tier-routing-env-profile-error-provenance`.
      depends: 1.1
