## Why

`_worktree_pr_close()` and `_unpushed_base_error()` in
`src/worktrail/workqueue/queue_triage.py` hardcode the `origin` literal for the base ref
they fetch, compare against, and branch the triage worktree from (`git fetch origin
<base>`, `git rev-list --count origin/<base>..<base>`, `git worktree add ... origin/<base>`,
and the "ahead of origin/<base>" error text). The remote the branch is actually pushed to
is resolved separately -- `_push_target()` (mirrored by `router/land_pr.py`) honours
`git config remote.pushDefault` and falls back to `origin` -- so in a fork-configured
checkout the base ref comes from one remote and the PR branch goes to another.

Live 2026-09-16 against `aspens` (`remote.pushDefault=fork`, `origin` is the read-only
`aspenkit/aspens` upstream, local `main` diverged from `origin/main` by design under the
fork-soak policy but is a clean subset of `fork/main`): every `propose-change` apply
failed with `local main is 18 commit(s) ahead of origin/main`, because the staleness check
compares against the wrong remote. Had that check been bypassed, the worktree would have
been created from `origin/main` -- an unrelated, rewritten history -- so the PR would have
been opened against the wrong repo/base, violating the never-PR-upstream-during-soak rule.
The brief was cleanly released back to `queue/` by the existing fail-closed handling, but
no triage apply can land against a fork-configured repo at all.

## What Changes

- `_worktree_pr_close()` resolves the push remote once via `_push_target(repo_path)`
  (`remote.pushDefault`, falling back to `origin`) and uses that remote for the fetch, the
  unpushed-base staleness check, and the `git worktree add` base ref, instead of the
  `origin` literal. The fetch-failure error text names the resolved remote.
- `_unpushed_base_error()` takes the remote as a parameter and compares
  `<remote>/<base>..<base>`; its error text names `<remote>/<base>`.
- Behaviour for a checkout with no `remote.pushDefault` is unchanged: the remote resolves
  to `origin` and every git invocation is byte-identical to today.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `intake-triage`: the "Fold and propose are applied as a pull request, fail-closed"
  requirement now states that the base ref the triage worktree is fetched from, compared
  against, and branched off is `<push remote>/<base branch>`, where the push remote is the
  one `remote.pushDefault` names (falling back to `origin`) -- the same remote the PR
  branch is pushed to.

## Impact

- `src/worktrail/workqueue/queue_triage.py`: `_worktree_pr_close()` and
  `_unpushed_base_error()` only. `_push_target()` itself is unchanged.
- `tests/workqueue/test_queue_triage.py`: coverage that a `remote.pushDefault=fork`
  checkout fetches, rev-lists, and branches off `fork/<base>` (and that the error text
  names `fork/<base>`), plus the existing `origin` assertions continuing to pass for an
  unconfigured checkout.
- Out of scope, noted for a follow-up brief: `router/land_pr.py`'s compile-marker
  preflight (`git fetch origin <base>` / `changed_change_dirs(repo, "origin/<base>")`)
  has the same literal; it is not on the triage-apply path this brief describes.
