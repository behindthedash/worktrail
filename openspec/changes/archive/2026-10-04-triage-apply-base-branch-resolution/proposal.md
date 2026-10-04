## Why

`queue-triage apply --confirm` lands a `fold-into-change` or `propose-change` verdict as a
pull request from a fresh worktree branched off `<push remote>/<base branch>`, but the base
branch *name* is resolved by a guess: `_repo_base_branch()`
(`src/worktrail/workqueue/queue_triage.py:2893-2927`) tries the repo policy's `base_branch`,
then the checkout's local `refs/remotes/origin/HEAD`, and otherwise returns a hardcoded
`main`. On a repo whose default branch is not `main` and whose checkout has no local
`origin/HEAD`, that guess drives the next step into `fatal: couldn't find remote ref main`,
surfaced as raw git stderr (`queue_triage.py:3115-3118`) with no hint that the repo's
`base_branch` policy key is the remedy.

Live 2026-09-20 on wake-up-sooner: default branch `dev`, the checkout had no
`refs/remotes/origin/HEAD`, and `git ls-remote --symref origin HEAD` answers `dev`. The run
was worked around out of band (`git remote set-head origin dev`, plus `base_branch` and
`docs_only_paths` entries in that repo's PR #29), and the code path is unchanged since —
`grep` finds no `ls-remote --symref` anywhere under `src/`. (Work-queue brief
`20260920-150413-queue-triage-base-branch-and`.)

The brief's other two premises are deliberately not acted on here:

- The pre-PR gate running in a worktree with no `node_modules` is already covered by the
  triage path's `worktree_bootstrap_cmd` bootstrap step (spec: "Fold and propose worktrees
  are bootstrapped before landing") and by the `pre-pr-cmd-without-bootstrap` policy-drift
  advisory (archived 2026-09-18); the residue the brief names — auto-skipping the gate for
  spec-only diffs without repo configuration — is a policy design choice over the existing
  `docs_only_paths` mechanism, not a mechanical fix.
- The evaluator reading a stale canonical checkout is evaluator-prompt behavior on a
  different surface; it is not touched by this change.

## What Changes

- The fold/propose apply resolves its base branch from three verified sources, in order:
  the policy `base_branch`; the checkout's local `refs/remotes/<push remote>/HEAD`; the
  push remote's own default branch via `git ls-remote --symref <remote> HEAD`. It never
  falls back to an unverified `main`.
- An unresolvable base branch fails closed *before* any fetch or worktree creation, with an
  error naming the repo's `base_branch` policy key as the remedy; the claimed brief is
  released back to `queue/`, matching every other pre-PR failure in this path.
- The base branch is resolved after the claim, against the same single push-remote
  resolution the fetch, the unpushed-base check, and the worktree base ref already use, so
  an already-claimed brief performs no probing at all (today it still runs the caller's
  `symbolic-ref`).
- The base-branch fetch failure error additionally names the resolved `<remote>/<base>`
  and the `base_branch` policy key, alongside the raw git failure — covering the remaining
  mis-resolution (a policy naming a branch the remote does not have).
- Regression tests: a non-main default resolves from the remote symref; the local ref
  answers without a wire probe; policy wins with no probe; unresolvable fails closed
  naming the policy key; fetch failure names the remedy; a fork-configured checkout probes
  the push remote and never `origin`.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `intake-triage`: fold/propose applies resolve their base branch from policy or the push
  remote itself — never a hardcoded `main` guess — and fail closed naming the repo's
  `base_branch` policy key when no source answers.

## Impact

- `src/worktrail/workqueue/queue_triage.py`: `_repo_base_branch()`,
  `_worktree_pr_close()`, `_apply_fold_into_change()`, `_apply_propose_change()`.
- `tests/workqueue/test_queue_triage.py`: both fold and propose dispatchers gain an
  `ls-remote` branch and a probe-unset knob; new resolution/failure cases; the two
  already-claimed tests stop describing a caller-side probe.
- No CLI, action-log schema, dashboard, or landing-pipeline change. The mirrored
  `router.sweep_stale_worktrees.default_base_branch()` `main` fallback is a classification
  default for worktree reclamation, not a write path, and is not acted on here.
