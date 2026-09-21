## 1. Sweep policy key types against DEFAULTS

- [x] 1.1 In `src/worktrail/router/policy.py`, add a module-level `POLICY_KEY_TYPES` table
      beside `DEFAULTS` mapping every flat policy key to its expected type (a type or tuple of
      types; `None`-defaulted keys such as `base_branch`, `pre_pr_cmd`, `integrate_smoke_cmd`,
      `post_merge_smoke_cmd`, `worktree_bootstrap_cmd`, `release_gate`, `auth_testing`,
      `run_record_dir`, `agent_cli`, `agent_model`, `fallback_agent_cli`, `max_workers` get an
      explicit entry, and `None` stays an accepted value for them), with `routing` and
      `add_ons` declared explicitly exempt. Add a `_sweep_key_types(policy, meta)` helper that
      walks the table, replaces any wrong-typed value with `DEFAULTS[key]` (deep-copied for the
      mutable defaults) and appends one `meta["warnings"]` entry naming the key, the expected
      type and the rejected value; treat `bool` as not an `int` so a boolean cannot satisfy an
      integer key. Sweep `automerge.target_branches` the same way. Call the helper in
      `load_policy()` immediately after `policy["add_ons"] = _resolve_add_ons(...)` and before
      the existing `automerge.max_risk` clamp, so every existing per-key check still runs last
      and its warnings and fallbacks are unchanged. Document the two-layer arrangement in a
      comment on `POLICY_KEY_TYPES`.
      (Requirements: Every policy key is type-checked against its declared default; A rejected
      value is reported in the policy warnings; The sweep preserves the existing per-key
      validation; automerge.target_branches is swept like a flat key)
      Add `tests/router/test_policy_key_types.py` covering: a string under `protected_paths`
      falls back to `[]` with a warning naming the key, the expected type and the value; a
      boolean under `pre_pr_cmd` falls back to `None`; a mistyped `automerge.target_branches`
      falls back to `[]`; a fully correctly-typed policy file resolves with no type warning; the
      existing `automerge.max_risk: critical` clamp and `max_workers: 0` range warning are
      emitted exactly as before with no duplicate type warning; and a coverage ratchet asserting
      `set(DEFAULTS) == set(POLICY_KEY_TYPES) | EXEMPT`. Write policy files into a `tmp_path`
      repo (reuse the policy-file fixture style already used by the router policy tests) so the
      test never reads the developer's real `~` policy.
      (Requirements: Every DEFAULTS key has a declared expected type)
      files: src/worktrail/router/policy.py, tests/router/test_policy_key_types.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/router/test_policy_key_types.py`,
      then `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m
      worktrail.orchestrator.orchestrate check`. Run `python3 scripts/ci/ruff_pinned.py check .`
      and `python3 scripts/ci/ruff_pinned.py format --check .`. Run `openspec validate
      policy-key-type-validation --strict` and `worktrail-compile
      openspec/changes/policy-key-type-validation`.
