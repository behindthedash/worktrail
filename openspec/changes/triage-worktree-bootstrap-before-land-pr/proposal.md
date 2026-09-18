## Why

`queue-triage apply --confirm` for a `fold-into-change` or `propose-change` verdict runs
`_worktree_pr_close` (`src/worktrail/workqueue/queue_triage.py:2714`): it fetches the remote
base ref, runs `git worktree add`, authors the change, runs `openspec validate` and
`worktrail-compile`, and hands the worktree to `router.land_pr`. Nothing in that sequence
installs the repo's dependencies -- `grep -n 'bootstrap\|worktree_bootstrap_cmd'` over
`queue_triage.py` is empty, and the only subprocess calls between `git worktree add` and
`land_pr(land_request)` are the two openspec checks. `land_pr`'s mandatory pre-PR gate then
runs the repo's `pre_pr_cmd` in a bare checkout. A git worktree shares `.git`, not the
gitignored `node_modules`, so on any Node repo whose `pre_pr_cmd` needs an install the gate
fails with `vitest: not found` (exit 127), `land_pr` refuses at preflight, the worktree and
branch are torn down, and the brief is released back to the queue with no PR. Verified
2026-09-18 on aspens (`pre_pr_cmd: npm test`, `worktree_bootstrap_cmd: null`,
`docs_only_paths: []`) via `worktrail-go 20260918-154644`: the intake brief can never be
dispatched until this is fixed, and every spec-only propose/fold against such a repo fails
the same way.

The policy already has the right field. `worktree_bootstrap_cmd` is honored only by
`orchestrator/live.py`'s `bootstrap_worktree()` for fanned-out task worktrees, so the
triage apply path is the one worktree creator in the package that ignores it.
(Work-queue brief `20260918-155844-triage-worktree-skips-bootstrap`.)

## What Changes

- `_worktree_pr_close` runs the target repo's `worktree_bootstrap_cmd` (read from the
  resolved repo policy via `router.policy.load_policy`, the same accessor the file already
  uses for `base_branch` and `max_active_changes`) in the freshly created worktree,
  after `git worktree add` succeeds and before `prepare()`, `openspec validate`,
  `worktrail-compile`, and `land_pr`. It reuses `orchestrator.live.bootstrap_worktree` with
  `required=True` rather than a second shell-out implementation.
- A configured bootstrap that fails to launch or exits non-zero fails the verdict closed
  with the same `status="error"` shape as every other pre-PR failure in this path: the
  error names the bootstrap failure, the brief is released back to `queue/`, no
  `land_pr` call is made, and the worktree is removed in the existing `finally`.
- A repo with no `worktree_bootstrap_cmd` (unset, null, or empty) is unaffected: no
  subprocess runs and the sequence is byte-for-byte what it is today.
- The `_worktree_pr_close` docstring's sequence description gains the bootstrap step.

Option (b) from the brief -- treating `openspec/**` as docs-only in the pre-PR gate -- is
not taken here: `docs_only_paths` is already an opt-in per-repo policy knob, and a
propose/fold PR is not the only thing that will ever land from a triage worktree.
Setting `worktree_bootstrap_cmd` in aspens' own `.worktrail/policy.yaml` is a follow-up in
that repo, not part of this change.

## Capabilities

### New Capabilities

### Modified Capabilities
- `queue-triage`: the fold/propose apply path bootstraps the triage worktree with the
  repo policy's `worktree_bootstrap_cmd` before validating and landing, and fails closed
  when a configured bootstrap fails.

## Impact

- `src/worktrail/workqueue/queue_triage.py` (`_worktree_pr_close`: one policy read, one
  `bootstrap_worktree` call between `git worktree add` and `prepare()`, a new error
  branch, and the docstring).
- `tests/workqueue/test_queue_triage.py` (regression tests on the propose path: bootstrap
  runs in the worktree and before `land_pr`; a failing bootstrap returns `status="error"`,
  releases the brief, and never calls `land_pr`; an unset command runs nothing).
- No CLI, verdict-file, or action-log schema change. Repos without a configured
  `worktree_bootstrap_cmd` see no behavior change.
