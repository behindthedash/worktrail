## 1. Preserve and report uncommitted task-worktree work

- [ ] 1.1 In `src/worktrail/orchestrator/worktree.py`, add a best-effort
      salvage step to `WorktreeManager.remove` that runs before the
      `git worktree remove` call: check `git -C <worktree> status --porcelain
      --untracked-files=no` and, when non-empty, `add -u` and commit the
      tracked modifications onto the task's own branch with a message naming
      the task, then log that the task's worktree held uncommitted work and
      which branch now carries it. Wrap the whole salvage in a broad
      `except` that logs the failure and falls through to the removal, so
      neither inspection nor commit failure can block teardown or replace the
      `WorktreeError` that `remove` already raises. Emit nothing when the
      worktree is clean, and honour `dry_run` the same way the existing
      `_git` helper does.
      Cover it in `tests/orchestrator/test_worktree_salvage.py` (alongside the
      existing `test_worktree_extras.py`, reusing its `WorktreeManager`
      runner-injection pattern): a clean worktree issues no salvage commit and
      no salvage log line and removes exactly as before; a dirty worktree
      produces `add -u` plus a commit on the task branch before the removal,
      with untracked files excluded (`--untracked-files=no` asserted on the
      status call); the salvage log line names the task and its branch; a
      salvage step that raises still results in the removal being issued; and
      a removal that fails still raises the same `WorktreeError` as today.
      (Requirements: Task worktree teardown inspects for uncommitted work;
      Uncommitted task work is preserved rather than discarded; Salvaged task
      work is reported to the operator; Salvage never blocks teardown)

## 2. Verification

- [ ] 2.1 [e2e] Run `pytest -q` and
      `python3 -m worktrail.orchestrator.orchestrate check` green.
