## 1. Local-branch teardown after a merged landing (`queue_triage`)

- [x] 1.1 In `src/worktrail/workqueue/queue_triage.py`, in `_worktree_pr_close()`'s cleanup
      `finally`, after the `git worktree remove --force` call, run
      `git -C <repo> branch -D <branch>` (`check=False`, `capture_output=True`, `timeout=60`,
      same shape as the existing no-PR deletion) when EITHER `pr_url` is empty (existing
      behaviour) OR `outcome.outcome == "landed"` and
      `outcome.final_status == "completed_and_merged"`. Keep the `code_defect` /
      `review_threads_blocking` guard so those outcomes still keep both worktree and branch.
      Update the function docstring's cleanup paragraph to say the merged case also deletes
      the local branch. (Requirement: Merged fold/propose landing tears down its local branch.)
      In `tests/workqueue/test_queue_triage.py`, using the existing fake-runner harness that
      records `worktree remove` / `branch -D` commands in `self.seen`, add tests for the four
      spec scenarios: a merged `landed` outcome runs both `worktree remove` and `branch -D`
      and returns `status: executed`; a non-zero `branch -D` still returns `executed` with
      `landing.final_status == "completed_and_merged"`; a `code_defect` outcome runs neither;
      a `refused` outcome with no PR URL still runs `branch -D` and releases the brief.
      files: src/worktrail/workqueue/queue_triage.py, tests/workqueue/test_queue_triage.py

## 2. Verification

- [x] 2.1 [depends: 1.1] [e2e] Run `PYTHONPATH=src pytest -q tests/workqueue/test_queue_triage.py`,
      then `PYTHONPATH=src pytest -q` and
      `PYTHONPATH=src python3 -m worktrail.orchestrator.orchestrate check`. Run
      `openspec validate queue-triage-landing-teardown --strict` and
      `worktrail-compile openspec/changes/queue-triage-landing-teardown`.
