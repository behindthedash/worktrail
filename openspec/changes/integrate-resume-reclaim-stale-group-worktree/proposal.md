## Why

Resuming a killed orchestrator run intermittently fails with
`git worktree add failed for group branch <run_id>/<group>` (work-queue brief
`20260905-203039-stale-integrate-worktree-blocks-resume`; the same
`KillAndResume::test_pipeline` flake in
`tests/orchestrator/lifecycle/test_lifecycle_harness.py` is cited by merged PRs
#593, #652, #784, #831 and #850, so it recurs rather than being a one-off).
`integrate.py:_integration_worktree` builds each group branch in a throwaway
checkout under `<repo>-integrate/<branch>-<uuid>` and tears it down in a
`finally`. When the run is killed mid-integration that teardown never runs, so
the checkout stays on disk with the group branch checked out. On resume the
code runs `git worktree prune` and then `git worktree add -f -B <branch> ...`,
retrying once with another prune. But `prune` only drops registrations whose
directory is gone, and `-B` refuses to reset a branch that another worktree
still has checked out, so both attempts fail identically and the resume aborts
before any merge happens. The existing retry addresses a different failure
(a registration that went stale between prune and add); it cannot reclaim an
intact leftover checkout.

## What Changes

- `_integration_worktree` SHALL, before its first `git worktree add`, detect
  linked worktrees that currently have the group branch checked out and that
  live under the run's own `<repo>-integrate/` scratch directory, and SHALL
  remove them (`git worktree remove --force` plus an `rmtree` fallback) so
  `-B` can reset the branch. Only checkouts under that scratch directory are
  eligible: a checkout of the same branch anywhere else (an operator's manual
  worktree) is never touched, and the add then fails with the existing
  `WorktreeAddError` whose message names the blocking path.
- The reclaim runs under the same `git_lock` as the add/prune calls, because
  `worktree remove` mutates the shared `.git/worktrees` registry.
- Regression coverage: a leftover integrate checkout holding the group branch
  is reclaimed and the add succeeds; a checkout of the same branch outside the
  scratch directory is left alone and the existing error surfaces.

## Capabilities

### New Capabilities
- `integrate-stale-group-worktree-reclaim`: reclamation of a killed run's
  leftover group-branch integrate checkout before `git worktree add -B`, scoped
  to the `<repo>-integrate/` scratch directory.

### Modified Capabilities
(none — this is new pre-add behavior inside `_integration_worktree`; the
existing prune-and-retry path and its `WorktreeAddError` contract are
unchanged.)

## Impact

- `src/worktrail/orchestrator/integrate.py` (`_integration_worktree`).
  `live._worktree_checkouts_on_branch` already parses `git worktree list
  --porcelain` for exactly this question and can be reused.
- `tests/orchestrator/test_integrate.py` (alongside the existing
  `_integration_worktree` tests around line 1886).
- No change to `live.py`'s task-level `add_stacked_worktree`, whose retry
  handles per-task worktrees that use a different lifecycle.
