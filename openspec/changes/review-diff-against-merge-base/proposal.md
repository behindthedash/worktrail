## Why

The review worker's brief (`ROLE_REVIEW` in `src/worktrail/orchestrator/dispatch.py`)
tells the reviewer to run `git diff {base_commit}..HEAD`. `LiveSpawn.__call__` in
`src/worktrail/orchestrator/live.py` fills `base_commit` at review-dispatch time by
resolving `dependency_start_ref(...)` to a SHA in the canonical repo. For a root task that
ref is the sentinel `HEAD`, so `base_commit` becomes the canonical repo's *current* tip,
not the commit the task branch actually forked from.

Those two commits are the same only while the base branch has not moved. Once a sibling
group PR merges (or anything else lands on `main`) before a task reaches review, the
two-dot range `main-tip..task-tip` diffs two divergent tips against each other: every
change the sibling merged shows up as a deletion on the branch under review. The reviewer
then either fails the task for "removing" code it never touched, or wastes its round on
noise, and neither the review report nor the journal records that the diff was wrong. A
dependent task has the same exposure whenever its dependency branch received commits
after the dependent forked from it.

`git log --all --grep` for merge-base / three-dot / reviewer shows only integrate-side
merge-base work (#475, #507); nothing has touched the review prompt's range, and no
active change mentions it.

## What Changes

- `LiveSpawn.__call__` resolves the review `base_commit` to the merge-base of the
  dependency start ref and the task worktree's `HEAD`, computed in the canonical repo
  (which shares the worktree's object store). For a root task that is the branch's fork
  point; for a dependent task it is the dependency tip the task actually stacked on.
- When no merge-base can be computed (no common ancestor, unresolvable worktree `HEAD`,
  or no canonical repo given), the current behavior is kept: the start ref's resolved SHA,
  or the literal `HEAD` sentinel when `repo` is `None`.
- The `ROLE_REVIEW` prompt text is unchanged; the fix is in the value handed to it, so the
  reviewer's `git diff {base_commit}..HEAD` now covers exactly the task's own commits.

## Capabilities

### New Capabilities

- `review-diff-base-resolution`: the review worker's diff base is the task branch's
  merge-base with its start ref, never a base tip that has since moved.

### Modified Capabilities

- None.

## Impact

- `src/worktrail/orchestrator/live.py` (`LiveSpawn.__call__` base-commit resolution, one
  new helper next to `_resolve_ref_to_sha`).
- `tests/orchestrator/test_live_extras.py`: the existing `base_commit` resolution tests
  gain the moved-base regression (root task reviewed after `main` advanced; dependent task
  reviewed after its dependency branch advanced) plus the no-common-ancestor fallback.
- No change to `dispatch.py`, the prompt text, the golden record/replay fixtures, or any
  console script.
