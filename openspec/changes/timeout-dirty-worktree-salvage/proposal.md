## Why

A worker can reach its timeout after making a complete but uncommitted change. The
orchestrator currently records that worker as failed immediately; its existing
`salvage_report()` recovery only recognizes a commit after an unparseable
report-back. The resulting dirty task worktree has to be rescued manually even
when its declared source and test files contain the intended work.

The queue brief `20260920-183217-worker-timeout-loses-uncommitted-work` recorded
this outcome for the one-task `policy-key-type-validation` run. The supplied
claim about the cause of the 1800-second timeout remains unconfirmed: the
recorded `e2b7f04c` commit changes planning prose only, and no active candidate
implements timeout recovery. The recovery gap itself remains present in
`live.py`, so it should be addressed independently of any later timeout-cause
investigation.

## What Changes

- On an implement or fix worker timeout, inspect the task worktree before
  recording failure. When it has dirty changes solely within the task's declared
  file scope, create a recovery commit and synthesize the same usable success
  report that existing committed-work salvage produces, allowing the ordinary
  review and cleanup flow to judge the work.
- Refuse to auto-commit a dirty worktree that contains an out-of-scope tracked
  change, a path that cannot be safely matched to the task scope, or no
  committable in-scope change. Preserve the current timeout failure behavior in
  those cases and make the refusal diagnosable.
- Apply the recovery consistently in both the direct live run and the pipeline
  scheduler timeout paths, with focused regression coverage for successful and
  refused recovery.

## Capabilities

### New Capabilities

- `worker-timeout-dirty-worktree-salvage`: recover a scoped, dirty implement or
  fix worktree after a worker timeout without treating unscoped changes as safe.

### Modified Capabilities

<!-- None. -->

## Impact

- `src/worktrail/orchestrator/live.py`: timeout recovery and salvage reporting.
- `tests/orchestrator/`: timeout recovery and scope-safety regression coverage.
- No new CLI option, policy key, dependency, or journal schema is required; a
  recovered task continues through the existing review and cleanup lifecycle.
