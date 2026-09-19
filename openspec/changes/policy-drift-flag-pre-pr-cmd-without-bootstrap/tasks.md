## 1. Flag a dependency-needing pre_pr_cmd with no worktree bootstrap

- [x] 1.1 In `src/worktrail/router/policy_drift_selfcheck.py`: add a
      `_NODE_DEP_RUNNER_RE` (matches `\b(?:npm|npx|yarn|pnpm|bun|vitest|jest|mocha)\b` or
      `playwright\s+test`, case-insensitive) and a `_SELF_INSTALL_RE` (matches
      `npm ci`, `npm install`, `npm i`, `yarn install`, `pnpm install`, `pnpm i`,
      `bun install` as whole words); add a `_key_value(text, key) -> str | None` helper
      that reads one top-level `key: value` line with the same `^key:\s*(.+)$` regex
      `_command_values` uses, strips surrounding quotes, and returns None for a missing
      key or a value of `null`, `~`, or empty. In `check_repo()`, after the
      `orphaned-tests` block, append a finding
      `{"signal": "pre-pr-cmd-without-bootstrap", "detail": ...}` when the `pre_pr_cmd`
      value is present, is not `skip`, matches `_NODE_DEP_RUNNER_RE`, does not match
      `_SELF_INSTALL_RE`, and `_key_value(text, "worktree_bootstrap_cmd")` is None; the
      detail names the `pre_pr_cmd` value and says to set `worktree_bootstrap_cmd`
      (e.g. `npm ci` or `worktrail-bootstrap-node-modules --app-dir .`). Do not consult
      `docs_only_paths`, do not touch `_COMMAND_KEYS` or `orphaned_test_paths()`. Add the
      new signal to the module docstring's "Signals" list and note why Python runners are
      excluded under "Deliberate limits".
      (Requirement: Dependency-needing pre_pr_cmd without a worktree bootstrap is flagged)
      In `tests/router/test_policy_drift_selfcheck.py`: add a
      `TestPrePrCmdWithoutBootstrap` class using the existing `_repo`/`_policy`/`_signals`
      helpers (extend `_policy` with an optional `extra: str = ""` appended after
      `base_branch`, keeping the default output byte-identical), covering: `"npm test"` with
      no bootstrap fires and its detail contains `npm test`; `"npm test"` plus
      `worktree_bootstrap_cmd: "npm ci"` does not fire; `"([ -d node_modules ] || npm ci)
      && npm test"` does not fire; `"PYTHONPATH=src pytest -q"` does not fire;
      `"npx vitest run"` plus `worktree_bootstrap_cmd: null` fires; `pre_pr_cmd: skip` does
      not fire; and `main(["--repo", ...])` returns 1 for the `npm test` repo. Assert the
      `_LINT_ONLY` and `_PYTEST` fixtures in the existing classes still produce exactly
      their prior signal sets (no `pre-pr-cmd-without-bootstrap`).
      files: src/worktrail/router/policy_drift_selfcheck.py, tests/router/test_policy_drift_selfcheck.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run
      `PYTHONPATH=src pytest -q tests/router/test_policy_drift_selfcheck.py`, then
      `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `PYTHONPATH=src python3 -m worktrail.router.policy_drift_selfcheck --repo ~/projects/aspens`
      and confirm `pre-pr-cmd-without-bootstrap` fires for aspens, and run the same
      command with `--repo .` from this repo's checkout and confirm it does not fire. Run
      `openspec validate policy-drift-flag-pre-pr-cmd-without-bootstrap --strict` and
      `worktrail-compile openspec/changes/policy-drift-flag-pre-pr-cmd-without-bootstrap`.
