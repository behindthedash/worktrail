## Why

The sync-pending drain remediation creates its isolated worktree from the local base-branch name without fetching first. A long-lived checkout can therefore run `/opsx:sync`, commit, and propose a result based on a stale base even when `origin/<base>` has advanced. This was confirmed in `src/worktrail/drain/drain.py` at the `_base_branch_for()` and `_run_sync_pending()` call sites; merged PR #1113 changed archived OpenSpec artifacts only, not this implementation. (Work-queue brief `20260927-031758-drain-sync-stale-local-base`.)

## What Changes

- Before creating a new sync-pending remediation worktree, fetch the target repository's configured base branch from `origin` and create the worktree from the refreshed `origin/<base>` ref.
- Preserve the existing re-entrant behavior: a finding that already has an open sync remediation PR is returned without fetching or creating a worktree.
- Add regression coverage using a remote base commit absent from the local base branch, proving the sync worktree starts from the refreshed remote base.

## Capabilities

### New Capabilities

_None._

### Modified Capabilities

- `drain-stage-remediation-table`: sync-pending remediation must base a newly created isolated worktree on a freshly fetched `origin/<base>` ref.

## Impact

- `src/worktrail/drain/drain.py`: the sync-pending remediation worktree preparation path.
- `tests/drain/test_drain.py`: real-git regression coverage for a stale local base and existing-PR fast path.
- No CLI, remediation-table, result-dict, or PR-landing-pipeline change.
