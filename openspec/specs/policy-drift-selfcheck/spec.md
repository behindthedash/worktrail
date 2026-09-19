# policy-drift-selfcheck Specification

## Purpose
TBD - created by archiving change policy-drift-flag-pre-pr-cmd-without-bootstrap. Update Purpose after archive.
## Requirements
### Requirement: Dependency-needing pre_pr_cmd without a worktree bootstrap is flagged

`policy_drift_selfcheck.check_repo()` SHALL emit a finding with signal
`pre-pr-cmd-without-bootstrap` when the repo's policy file sets a `pre_pr_cmd` that
invokes a Node package-managed runner (`npm`, `npx`, `yarn`, `pnpm`, `bun`, `vitest`,
`jest`, `mocha`, or `playwright test`) and `worktree_bootstrap_cmd` is unset, `null`,
`~`, or empty. The finding's `detail` SHALL name the `pre_pr_cmd` value and say that
`worktree_bootstrap_cmd` must be set. The finding SHALL NOT be emitted when
`worktree_bootstrap_cmd` is set to a non-empty command, when `pre_pr_cmd` itself contains
an install step (`npm ci`, `npm install`, `npm i`, `yarn install`, `pnpm install`,
`pnpm i`, or `bun install`), when `pre_pr_cmd` is unset or the literal `skip`, or when
`pre_pr_cmd` invokes only non-Node runners such as `pytest`. `docs_only_paths` SHALL NOT
affect the finding. The existing signals, `orphaned_test_paths()`, the CLI exit codes,
and the JSON output shape SHALL be unchanged.

#### Scenario: npm test with no bootstrap is flagged

- **WHEN** a repo's policy file contains `pre_pr_cmd: "npm test"` and no
  `worktree_bootstrap_cmd` key
- **THEN** `check_repo()` SHALL include a finding with signal
  `pre-pr-cmd-without-bootstrap` whose detail names `npm test`, and the CLI SHALL exit 1
  for that repo

#### Scenario: Bootstrap configured suppresses the finding

- **WHEN** a repo's policy file contains `pre_pr_cmd: "npm test"` and
  `worktree_bootstrap_cmd: "npm ci"`
- **THEN** `check_repo()` SHALL NOT include a `pre-pr-cmd-without-bootstrap` finding

#### Scenario: Self-installing pre_pr_cmd is not flagged

- **WHEN** a repo's policy file contains
  `pre_pr_cmd: "([ -d node_modules ] || npm ci) && npm test"` and no
  `worktree_bootstrap_cmd`
- **THEN** `check_repo()` SHALL NOT include a `pre-pr-cmd-without-bootstrap` finding

#### Scenario: Python runner is not flagged

- **WHEN** a repo's policy file contains `pre_pr_cmd: "PYTHONPATH=src pytest -q"` and no
  `worktree_bootstrap_cmd`
- **THEN** `check_repo()` SHALL NOT include a `pre-pr-cmd-without-bootstrap` finding

#### Scenario: Explicit null bootstrap is treated as unset

- **WHEN** a repo's policy file contains `pre_pr_cmd: "npx vitest run"` and
  `worktree_bootstrap_cmd: null`
- **THEN** `check_repo()` SHALL include a `pre-pr-cmd-without-bootstrap` finding

#### Scenario: Skipped gate is not flagged

- **WHEN** a repo's policy file contains `pre_pr_cmd: skip` and no
  `worktree_bootstrap_cmd`
- **THEN** `check_repo()` SHALL NOT include a `pre-pr-cmd-without-bootstrap` finding

