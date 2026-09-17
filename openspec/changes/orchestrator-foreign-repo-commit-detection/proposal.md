## Why

When a task's real target lives outside `$REPO` (a fleet-wide change touching sibling repos
under `~/projects/`), the worker has no managed worktree there and commits straight onto the
sibling repo's checked-out default branch. Back in `$REPO` the merged group branch is
identical to its target, so `integrate_one()` quarantines the group with the single
undifferentiated reason `empty_diff` ("no changes to integrate") — zero signal that work
landed somewhere it must not. On 2026-08-22 (`fleet-ci-standard-rollout`, devops repo) 6 of 7
sibling-repo tasks committed to local `main` this way and were caught only by manual
git-state inspection.

## What Changes

- The empty-diff guard in `src/worktrail/orchestrator/integrate.py` splits its outcome: when
  any deliverable task in the group declares file scope that resolves outside `$REPO`, the
  group is quarantined with a new reason `foreign_repo_target` instead of `empty_diff`.
- For each foreign git repo so identified, the orchestrator inspects its checked-out branch
  and reports, in the quarantine message, whether that branch is the repo's default branch
  and carries commits not on its upstream (an unmanaged default-branch commit).
- `empty_diff` keeps its meaning of "nothing changed" for groups whose scope is entirely
  inside `$REPO`.
- Detection only: the orchestrator never modifies, resets, or pushes a foreign repo.

## Capabilities

### New Capabilities
- `orchestrator-foreign-repo-commit-detection`: distinguishing a foreign-repo-targeted group
  from a true no-op at the empty-diff guard, and surfacing unmanaged commits on a sibling
  repo's default branch.

### Modified Capabilities

## Impact

- `src/worktrail/orchestrator/integrate.py` (new quarantine reason + foreign-scope probe).
- `tests/orchestrator/test_integrate.py`.
- Journal consumers see one new `quarantine_reason` value; existing values are unchanged.
