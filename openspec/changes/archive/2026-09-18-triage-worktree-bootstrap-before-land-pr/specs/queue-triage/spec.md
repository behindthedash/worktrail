## ADDED Requirements

### Requirement: Fold and propose worktrees are bootstrapped before landing

For a `fold-into-change` or `propose-change` verdict applied with `--confirm`, after the
triage worktree is created off the fetched remote base ref and before the change is
authored, validated, compiled, or handed to `router.land_pr`, `apply` SHALL run the target
repo's resolved policy `worktree_bootstrap_cmd` in that worktree, using the same bootstrap
step the orchestrator applies to fanned-out task worktrees. When the policy has no
`worktree_bootstrap_cmd` (unset, null, or empty), `apply` SHALL run no bootstrap
subprocess and the sequence SHALL be unchanged. When a configured bootstrap cannot be
launched or exits non-zero, `apply` SHALL fail that verdict closed with an error
action-log entry naming the bootstrap failure, SHALL NOT call `router.land_pr`, SHALL
release the claimed brief back to the queue, and SHALL remove the worktree, matching the
failure shape of every other pre-PR failure in this path.

#### Scenario: Configured bootstrap runs in the worktree before land_pr

- **WHEN** `apply --confirm` executes a `propose-change` verdict against a repo whose
  policy sets `worktree_bootstrap_cmd: npm ci`
- **THEN** `npm ci` SHALL be run with the new triage worktree as its working directory,
  after `git worktree add` and before `router.land_pr` is called, and the verdict SHALL
  proceed to open the pull request as it does today

#### Scenario: Unset bootstrap command runs nothing

- **WHEN** `apply --confirm` executes a `propose-change` verdict against a repo whose
  policy has no `worktree_bootstrap_cmd`
- **THEN** no bootstrap subprocess SHALL run and the verdict SHALL land exactly as it
  does today

#### Scenario: Failing bootstrap fails the verdict closed

- **WHEN** `apply --confirm` executes a `propose-change` verdict against a repo whose
  policy sets `worktree_bootstrap_cmd` and that command exits non-zero
- **THEN** the action-log entry SHALL have `status: error` with an error naming the
  bootstrap failure, `router.land_pr` SHALL NOT be called, the brief SHALL be back in
  `queue/`, and the triage worktree SHALL be removed
