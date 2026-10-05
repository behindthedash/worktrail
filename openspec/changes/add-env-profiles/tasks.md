## 1. Profile resolution

- [x] 1.1 Add `router/env_profile.py` with `resolve_env_profile(profile_name, profile, *,
      target, declared_in)`: expand and require an absolute `from`, read and JSON-parse it,
      require a mapping `env` object, assert every `expect` entry, then copy every `keys` entry.
      Refuse a missing/empty/non-string key and a relative `from`. Every failure raises
      `OperatorConfigError` naming target, profile, file and key, and never includes a value for
      a key absent from `expect`. (Requirement: A worker environment can be supplied from a
      declared profile without storing values)
      files: src/worktrail/router/env_profile.py, tests/router/test_env_profile.py

## 2. Validation

- [x] 2.1 Add `_validate_routing_env_profiles(raw, meta)` to `router/policy.py` (never-raise
      warn-and-drop, mirroring the sibling `_validate_routing_*` validators; a malformed `expect`
      drops the whole profile rather than ignoring the assertion) and
      `_warn_undeclared_env_profiles(targets, env_profiles, meta)`; resolve `env_profiles` before
      `targets` in `_validate_routing` and include it in the returned dict.
      (Requirement: A worker environment can be supplied from a declared profile without storing
      values)
      files: src/worktrail/router/policy.py, tests/router/test_policy.py

- [x] 2.2 Warn from `_validate_routing_targets` on a non-mapping `auth`, on `auth.env` and
      `auth.profile` together, and on `auth.profile` on a `subscription` target (that lane strips
      `ANTHROPIC_API_KEY` after injection). `auth.codex_home` alongside a profile is not a
      conflict. (Requirement: Profile and named-variable auth are mutually exclusive
      alternatives)
      files: src/worktrail/router/policy.py, tests/router/test_policy.py

## 3. Spawn-time injection

- [x] 3.1 Add `_apply_env_profile` and the `env_profiles` keyword to `build_child_env`, applied
      before the harness/pool branch so a subscription cell's `ANTHROPIC_API_KEY` pop still wins.
      Skip the `auth.env` requirement when a profile supplied the credentials. Thread the table
      from `_prepare_child_env`'s already-resolved routing dict.
      (Requirement: A worker environment can be supplied from a declared profile without storing
      values; Requirement: Profile and named-variable auth are mutually exclusive alternatives)
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py

- [x] 3.2 Strip every provider-redirect variable in the claude `subscription` lane, not just
      `ANTHROPIC_API_KEY`, so an ambient base URL or token cannot misroute a subscription worker.
      (Requirement: A subscription spawn cannot be redirected by an ambient provider environment)
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py

- [x] 3.3 Omit `--bare` for an `api` cell that declares `auth.profile`, since `--bare` skips the
      settings-injected worktree guard and injected credentials already pin the endpoint.
      (Requirement: A profile-backed api cell keeps its settings-injected hooks)
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py

## 4. Explicit overrides

- [x] 4.1 Make `explicit_cell_override` reproduce the target's whole definition — `api_opt_in`,
      the full `auth` mapping, and the referenced `env_profiles` entry — written with
      `yaml.safe_dump`. (Requirement: An explicit override reproduces the target's whole
      definition)
      files: src/worktrail/orchestrator/spawnlib.py, tests/orchestrator/test_spawnlib.py

## 5. Operator diagnostics

- [x] 5.1 Resolve each profile-backed cell in `worktrail-routing --check`, marking it `FAIL` and
      exiting non-zero on failure without recording an `agent_capacity` gate.
      (Requirement: An unresolvable profile fails the routing check without gating the cell)
      files: src/worktrail/router/routing_cli.py, tests/router/test_routing_cli.py

## 6. Documentation

- [x] 6.1 Document `env_profiles` in `docs/config/routing.yaml.example`, and add the
      "point a harness at a custom endpoint" recipe plus the two gotchas (user-level settings
      never reach a worker; `--bare` skips settings hooks) to the `worktrail-routing-config`
      skill.
      (Requirement: A worker environment can be supplied from a declared profile without storing
      values)
      files: docs/config/routing.yaml.example, skills/worktrail-routing-config/SKILL.md, skills/worktrail-routing-config/references/how-to.md, skills/worktrail-routing-config/references/gotchas.md

## 7. Verification

- [x] 7.1 [e2e] Run the full suite plus the repo gates (`ruff_pinned.py check`/`format --check`,
      `check_shebang_exec_bits.py`, `orchestrate check`), and prove the fixed path end to end: a
      driver that builds the child env from a real profile reaches the endpoint
      (`duration_api_ms > 0`, `total_cost_usd > 0`) under the worktrail argv shape, while a
      deliberately wrong `expect` raises before any process launches. Depends on 3.1.
      (Requirement: A worker environment can be supplied from a declared profile without storing
      values)
