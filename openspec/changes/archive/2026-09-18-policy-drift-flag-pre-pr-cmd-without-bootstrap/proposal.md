## Why

`policy_drift_selfcheck.py` asks "does this policy file still describe *this* repo?", but
it only looks at one shape of drift: test files no gate runs, and absence claims the
filesystem contradicts. It says nothing about a policy that is internally inconsistent:
`pre_pr_cmd` set to a command that needs a per-checkout dependency install
(`npm test`, `npx vitest`, `pnpm test`) while `worktree_bootstrap_cmd` is unset. A git
worktree shares `.git`, not the gitignored `node_modules`, so every worktree worktrail
creates for such a repo runs the pre-PR gate in a bare checkout and fails with
`vitest: not found` (exit 127) before any test runs. `grep -n 'pre_pr_cmd\|worktree_bootstrap_cmd'`
over the module shows the only use of either key is the `orphaned-tests` runner match, and
`_COMMAND_KEYS` (lines 71-73) deliberately excludes `worktree_bootstrap_cmd` because it is
not a test gate -- nothing checks whether the gate can run at all.

Verified 2026-09-18 on aspens: `.worktrail/policy.yaml` is `pre_pr_cmd: "npm test"` with no
`worktree_bootstrap_cmd` key and `docs_only_paths` empty. The `queue-triage apply` propose
path created a worktree, `land_pr`'s gate ran `npm test`, `sh: 1: vitest: not found`, the
worktree was torn down and brief `20260918-154644` released, undispatchable. The active
change `triage-worktree-bootstrap-before-land-pr` (PR #1250, brief `20260918-155844`) makes
the triage path *honor* `worktree_bootstrap_cmd`; it does nothing when the key is unset,
which is exactly aspens' state. This change surfaces that misconfiguration on the dashboard
as a drift finding before a PR gate has to fail to reveal it.
(Work-queue brief `20260918-161638-pre-pr-cmd-missing-bootstrap`.)

## What Changes

- `policy_drift_selfcheck.check_repo()` emits a new finding, signal
  `pre-pr-cmd-without-bootstrap`, when the policy's `pre_pr_cmd` invokes a Node
  package-managed runner and `worktree_bootstrap_cmd` is unset, `null`, `~`, or empty.
  The detail names the offending `pre_pr_cmd` and says to set `worktree_bootstrap_cmd`
  (e.g. `npm ci` or `worktrail-bootstrap-node-modules --app-dir .`).
- "Needs a dependency install" is a narrow, high-precision heuristic in the module's
  existing style, not a reachability proof: the command mentions `npm`, `npx`, `yarn`,
  `pnpm`, or `bun`, or a bare Node runner (`vitest`, `jest`, `mocha`, `playwright test`).
  Python runners are deliberately **not** matched: `pytest` in a worktree resolves to the
  machine's interpreter/venv, not a per-checkout tree (worktrail's own policy is
  `PYTHONPATH=src pytest -q` with no bootstrap and is correct), so matching it would fire
  on every Python repo in the fleet and the advisory would be ignored.
- A `pre_pr_cmd` that installs its own dependencies is not flagged: if the command
  contains `npm ci`, `npm install`, `npm i`, `yarn install`, `pnpm install`, `pnpm i`, or
  `bun install`, the gate is self-sufficient (the existing `_LINT_ONLY` test fixture,
  `([ -d node_modules ] || npm ci) && npm run lint`, is this shape). `pre_pr_cmd: skip`
  and an unset `pre_pr_cmd` are never flagged.
- `docs_only_paths` is not consulted. A non-empty `docs_only_paths` only exempts a
  docs-only diff from the gate; every code PR against the same repo still runs
  `pre_pr_cmd` in a bare worktree, so the misconfiguration stands regardless. The brief's
  "does not cover the diff" clause is a per-PR condition the self-check has no diff to
  evaluate; it is the gate's job (`pre_pr_gate.py`), not the drift detector's.
- The module docstring's signal list gains the new signal. The dashboard needs no change:
  it renders every `check_repo()` finding by its `signal` name.
- Setting `worktree_bootstrap_cmd` in aspens' own `.worktrail/policy.yaml` is the
  brief's follow-up in that repo once this finding fires for it; it is not a task of this
  change, which touches only worktrail.

## Capabilities

### New Capabilities
- `policy-drift-selfcheck`: the rationale-vs-reality drift detector flags a Node
  dependency-needing `pre_pr_cmd` paired with no `worktree_bootstrap_cmd`.

### Modified Capabilities

## Impact

- `src/worktrail/router/policy_drift_selfcheck.py` (one new regex pair, a key-value
  reader for `worktree_bootstrap_cmd`, one new finding block in `check_repo()`, docstring).
- `tests/router/test_policy_drift_selfcheck.py` (a new test class: `npm test` with no
  bootstrap fires; with `worktree_bootstrap_cmd: npm ci` it does not; a self-installing
  `pre_pr_cmd` does not; a pytest `pre_pr_cmd` does not; `skip` does not; the existing
  fixtures' signal sets are unchanged).
- No CLI, JSON-shape, or dashboard change; `orphaned_test_paths()` and `pre_pr_gate.py`
  are untouched. Fleet repos with Python or self-installing gates see no new finding.
