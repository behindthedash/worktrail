# Tasks

## 1. Claim-first concurrency guard

- [ ] 1.1 In `src/worktrail/workqueue/queue_triage.py`, move the `claim(v.brief_id,
      by="queue-triage")` call in `_worktree_pr_close()` to the very start of the function
      (before the `git fetch` step), returning an error result immediately (no worktree, no
      `prepare()`, no `land_pr`) when the claim does not succeed. Wrap the remaining body
      (fetch through the `land_pr` call and its existing worktree-cleanup `finally`) in an
      outer `try`/`finally` that calls `release(v.brief_id)` whenever no PR URL was
      obtained, so a pre-PR failure still leaves the brief retryable in `queue/`. Remove the
      now-redundant `claim()` call that previously sat immediately before `done()` (the
      brief is already claimed by the earlier step); the `done()` call and its own
      `release()` rollback on failure are unchanged. Update the docstring to describe the
      claim-first order. Extend `tests/workqueue/test_queue_triage.py`: a brief already
      claimed by another owner causes `_worktree_pr_close()` (exercised via
      `_apply_fold_into_change()`/`_apply_propose_change()`) to return an error with no
      `git`/`land_pr` calls made; a failure injected before `land_pr` returns a PR URL
      (e.g. a failing `openspec validate`) releases the brief back to `queue/` with
      `status: queued`; the existing success-path and post-PR-failure tests still pass
      unchanged.
      (Requirement: Fold and propose are applied as a pull request, fail-closed)
      files: src/worktrail/workqueue/queue_triage.py tests/workqueue/test_queue_triage.py

## 2. Verification

- [ ] 2.1 [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`, then
      `PYTHONPATH=src pytest -q` and `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`,
      and confirm all pass.
      depends: 1.1
