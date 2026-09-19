## Why

Queue-triage's `fold-into-change` / `propose-change` verdicts land through the shared
`_worktree_pr_close()` sequence in `src/worktrail/workqueue/queue_triage.py`, which creates a
sibling worktree on a deterministic `queue-triage/<verdict>-<name>` branch, calls
`router.land_pr`, and removes the worktree in a `finally` (except on `code_defect` /
`review_threads_blocking`, where it is kept for manual review). The local *branch*, however, is
only deleted when no PR URL was obtained (`queue_triage.py:2978-2986`). When `land_pr` returns
`landed` with `final_status="completed_and_merged"` (`router/land_pr.py:1623-1653`, including the
"merged externally" case), the worktree is removed but the merged branch stays behind in the
target repo, accumulating until `drain`'s `prune_stale_branch` remediation or a human gets to it.
`router/pr_labels.py:83` already documents that a torn-down queue-triage worktree is the expected
post-landing state, i.e. teardown is owed by the landing caller, not by `land_pr` (which lands a
repo it does not own and is shared by `close_stale_openspec`, `drain`, and `pr_ledger`).
(Work-queue brief `20260918-195825-queue-triage-propose-change-landings`.)

## What Changes

- After `_worktree_pr_close()` removes the triage worktree, it also deletes the local triage
  branch when the landing outcome is `landed` with `final_status="completed_and_merged"` --
  the branch's content is on the remote and merged, so the local ref is redundant. The deletion
  is best-effort (`check=False`), matching the existing worktree removal, and a failure never
  changes the returned verdict entry.
- Outcomes that keep the worktree (`code_defect`, `review_threads_blocking`) are unchanged: the
  branch stays because the worktree needs it. Outcomes with an open or blocked PR are unchanged
  too; `drain`'s stale-branch remediation still reclaims them once they merge.
- Regression tests cover the merged-landing teardown, the kept-worktree outcomes, and the
  existing no-PR branch deletion.

## Capabilities

### New Capabilities
- `queue-triage`: a merged fold/propose landing tears down its local branch along with its
  worktree.

### Modified Capabilities

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`_worktree_pr_close()` cleanup `finally`).
- `tests/workqueue/test_queue_triage.py` (new regression tests on the existing fake-runner
  harness that already records `worktree remove` / `branch -D` commands).
- No change to `router/land_pr.py`, its outcomes, or any CLI/on-disk format.
